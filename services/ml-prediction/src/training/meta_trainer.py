"""Cross-symbol meta-learning model — T89.

Trains a single XGBoost model on all signal_outcomes across all symbols.
Adds sector_code, market_cap_bin, horizon_code as additional features for
cross-symbol generalization. Used as cold-start prior and 4th ensemble member.

Feature vector: FEATURE_COLUMNS (61) filtered to non-constant cols, plus:
  sector_code     — ordinal-encoded sector (0–10; -1=unknown)
  market_cap_bin  — 0=micro/unknown, 1=small, 2=mid, 3=large, 4=mega
  horizon_code    — 0=SHORT, 1=SWING, 2=LONG, 3=GROWTH
  confidence      — signal confidence from signal_outcomes
  fused_prob      — fused_prob from signal_outcomes
  ta_score        — ta_score from signal_outcomes
"""
from __future__ import annotations

import logging
import pathlib
import tempfile
import os

import numpy as np

log = logging.getLogger(__name__)

META_MODEL_PATH = pathlib.Path("/data/models/meta_model.joblib")
META_SCALER_PATH = pathlib.Path("/data/models/meta_scaler.joblib")

# Sector encoding (ordinal — simpler than one-hot for tree models)
SECTOR_MAP: dict[str, int] = {
    "Technology": 0,
    "Healthcare": 1,
    "Consumer Cyclical": 2,
    "Financial Services": 3,
    "Communication Services": 4,
    "Consumer Defensive": 5,
    "Energy": 6,
    "Industrials": 7,
    "Basic Materials": 8,
    "Real Estate": 9,
    "Utilities": 10,
}

HORIZON_MAP: dict[str, int] = {
    "SHORT": 0,
    "SWING": 1,
    "LONG": 2,
    "GROWTH": 3,
}

_HORIZON_DAYS: dict[str, int] = {
    "SHORT": 5,
    "SWING": 10,
    "LONG": 20,
    "GROWTH": 15,
}


def _market_cap_bin(market_cap: float | None) -> int:
    """Convert market_cap (USD) to 0–4 bin. 0=unknown/micro."""
    if not market_cap:
        return 0
    if market_cap >= 200e9:
        return 4  # mega
    if market_cap >= 10e9:
        return 3  # large
    if market_cap >= 2e9:
        return 2  # mid
    if market_cap >= 300e6:
        return 1  # small
    return 0      # micro / unknown


def train_meta_model(db=None) -> dict:
    """Train cross-symbol meta model on all available signal_outcomes with is_correct set.

    Accepts an optional SQLAlchemy Session (db) for test injection; uses SessionLocal()
    when db is None (standard production call).

    Feature vector: FEATURE_COLUMNS (non-constant subset) + sector_code + market_cap_bin
                    + horizon_code + confidence + fused_prob + ta_score
    Label: is_correct (bool → int)

    Returns {"trained": bool, "n_samples": int, "auc": float}
    """
    from ..features.builder import FEATURE_COLUMNS, build_features, fetch_macro_features, compute_label_threshold
    from sklearn.preprocessing import StandardScaler
    from sklearn.metrics import roc_auc_score
    from sqlalchemy import text, select
    try:
        import xgboost as xgb
    except ImportError:
        log.error("meta_trainer.xgb_missing")
        return {"trained": False, "n_samples": 0, "auc": 0.0}

    import joblib
    from datetime import date, timedelta
    import pandas as pd

    # Use provided session or open a fresh one
    if db is not None:
        _session = db
        _close_session = False
    else:
        from db import SessionLocal
        _session = SessionLocal()
        _close_session = True

    try:
        rows = _session.execute(text("""
            SELECT so.symbol, so.horizon, so.signal_date, so.confidence,
                   so.fused_prob, so.ta_score, so.is_correct,
                   st.sector, f.market_cap
            FROM signal_outcomes so
            JOIN stocks st ON st.symbol = so.symbol
            LEFT JOIN fundamentals f ON f.stock_id = st.id
            WHERE so.is_correct IS NOT NULL
            ORDER BY so.signal_date DESC
            LIMIT 20000
        """)).fetchall()
    finally:
        if _close_session:
            _session.close()

    if len(rows) < 50:
        log.warning("meta_trainer.insufficient_data n=%d", len(rows))
        return {"trained": False, "n_samples": len(rows), "auc": 0.0}

    log.info("meta_trainer.building_features n_rows=%d", len(rows))

    # Group outcomes by symbol so we only load prices once per symbol
    from collections import defaultdict
    symbol_rows: dict[str, list] = defaultdict(list)
    for row in rows:
        symbol_rows[row.symbol].append(row)

    from db import Price, SessionLocal as _SessionLocal, Stock, TimeFrame
    from sqlalchemy import select as _select

    records: list[tuple[list, int]] = []

    for symbol, sym_rows in symbol_rows.items():
        # Sort by signal_date ascending so we build features chronologically
        sym_rows_sorted = sorted(sym_rows, key=lambda r: r.signal_date)

        # Load price history for this symbol (5 years for full feature coverage)
        try:
            with _SessionLocal() as sess:
                stock_obj = sess.execute(
                    _select(Stock).where(Stock.symbol == symbol.upper())
                ).scalar_one_or_none()
                if stock_obj is None:
                    continue
                since = date.today() - timedelta(days=365 * 5)
                price_rows = sess.execute(
                    _select(Price).where(
                        Price.stock_id == stock_obj.id,
                        Price.timeframe == TimeFrame.D1,
                        Price.ts >= since,
                    ).order_by(Price.ts)
                ).scalars().all()
        except Exception as exc:
            log.warning("meta_trainer.price_load_failed symbol=%s err=%s", symbol, exc)
            continue

        if len(price_rows) < 100:
            continue

        df = pd.DataFrame([{
            "ts": p.ts, "open": p.open, "high": p.high,
            "low": p.low, "close": p.close, "volume": p.volume,
        } for p in price_rows])
        df["ts"] = pd.to_datetime(df["ts"])
        df = df.sort_values("ts").reset_index(drop=True)

        # Fetch macro features once per symbol (same date range as prices)
        try:
            start_date = df["ts"].min().date()
            end_date = date.today() + timedelta(days=1)
            macro_df = fetch_macro_features(start_date, end_date)
        except Exception:
            macro_df = None

        # For each signal_outcome row, build features up to signal_date
        for row in sym_rows_sorted:
            signal_date = pd.Timestamp(row.signal_date)
            # Slice price data up to signal_date (look-ahead safe)
            df_upto = df[df["ts"] <= signal_date].copy()
            if len(df_upto) < 60:
                continue

            try:
                horizon_days = _HORIZON_DAYS.get(str(row.horizon).upper(), 10)
                label_thr = compute_label_threshold(df_upto.iloc[-min(252, len(df_upto)):], horizon_days)
                X_feat, _, _ = build_features(
                    df_upto,
                    horizon=horizon_days,
                    macro_df=macro_df,
                    label_threshold=label_thr,
                    inference_mode=True,  # include latest bar without requiring future label
                )
            except Exception:
                continue

            if X_feat.empty:
                continue

            # Use the last bar's features (= feature vector at signal_date)
            latest = X_feat.iloc[-1]
            vec: list[float] = [float(latest.get(col, np.nan)) if latest.get(col) is not None else np.nan
                                 for col in FEATURE_COLUMNS]

            # Meta features
            vec.append(float(SECTOR_MAP.get(row.sector or "", -1)))
            vec.append(float(_market_cap_bin(row.market_cap)))
            vec.append(float(HORIZON_MAP.get(str(row.horizon).upper(), -1)))
            vec.append(float(row.confidence) if row.confidence is not None else 0.0)
            vec.append(float(row.fused_prob) if row.fused_prob is not None else 0.0)
            vec.append(float(row.ta_score) if row.ta_score is not None else 0.0)

            records.append((vec, int(row.is_correct)))

    if len(records) < 50:
        log.warning("meta_trainer.insufficient_feature_records n=%d", len(records))
        return {"trained": False, "n_samples": len(records), "auc": 0.0}

    X_raw = np.array(
        [[v if (v is not None and not (isinstance(v, float) and np.isnan(v))) else 0.0 for v in r[0]]
         for r in records],
        dtype=np.float32,
    )
    y = np.array([r[1] for r in records], dtype=np.int32)

    # Remove constant columns (avoids numerical issues in StandardScaler / XGBoost)
    non_const = np.where(X_raw.std(axis=0) > 1e-8)[0]
    X = X_raw[:, non_const]

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # 80/20 chronological split for AUC evaluation
    split = int(len(X_scaled) * 0.8)
    X_tr, X_val = X_scaled[:split], X_scaled[split:]
    y_tr, y_val = y[:split], y[split:]

    pos_count = max((y_tr == 1).sum(), 1)
    neg_count = (y_tr == 0).sum()

    model = xgb.XGBClassifier(
        n_estimators=300,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.8,
        colsample_bytree=0.7,
        min_child_weight=5,
        scale_pos_weight=max(1, neg_count / pos_count),
        eval_metric="auc",
        early_stopping_rounds=20,
        random_state=42,
        verbosity=0,
    )
    model.fit(X_tr, y_tr, eval_set=[(X_val, y_val)], verbose=False)

    auc = 0.0
    if len(np.unique(y_val)) > 1:
        auc = float(roc_auc_score(y_val, model.predict_proba(X_val)[:, 1]))
    log.info("meta_trainer.trained n=%d auc=%.4f", len(records), auc)

    META_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)

    bundle = {
        "model": model,
        "scaler": scaler,
        "non_const": non_const,
        "feature_columns": list(FEATURE_COLUMNS),
        "n_meta_features": 6,  # sector_code, market_cap_bin, horizon_code, confidence, fused_prob, ta_score
    }

    # Atomic write — same pattern as trainer.py (RACE-001)
    tmp_fd, tmp_path = tempfile.mkstemp(dir=META_MODEL_PATH.parent, suffix=".tmp")
    try:
        os.close(tmp_fd)
        joblib.dump(bundle, tmp_path)
        os.replace(tmp_path, META_MODEL_PATH)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    return {"trained": True, "n_samples": len(records), "auc": round(auc, 4)}


def predict_meta(
    symbol: str,
    horizon: str,
    confidence: float,
    fused_prob: float,
    ta_score: float,
    sector: str | None = None,
    market_cap: float | None = None,
) -> float | None:
    """Return meta-model probability for a single prediction, or None if model unavailable.

    This function never crashes the main prediction path — any exception returns None.
    Loads prices internally so it can build the feature vector at the current date.
    """
    if not META_MODEL_PATH.exists():
        return None
    try:
        import joblib
        import pandas as pd
        from datetime import date, timedelta
        from ..features.builder import FEATURE_COLUMNS, build_features, fetch_macro_features, compute_label_threshold
        from db import Price, SessionLocal, Stock, TimeFrame
        from sqlalchemy import select as _select

        bundle = joblib.load(META_MODEL_PATH)
        model = bundle["model"]
        scaler = bundle["scaler"]
        non_const = bundle["non_const"]

        # Load recent prices for the symbol to reconstruct feature vector
        with SessionLocal() as sess:
            stock_obj = sess.execute(
                _select(Stock).where(Stock.symbol == symbol.upper())
            ).scalar_one_or_none()
            if stock_obj is None:
                return None
            since = date.today() - timedelta(days=400)
            price_rows = sess.execute(
                _select(Price).where(
                    Price.stock_id == stock_obj.id,
                    Price.timeframe == TimeFrame.D1,
                    Price.ts >= since,
                ).order_by(Price.ts)
            ).scalars().all()

        if len(price_rows) < 60:
            return None

        df = pd.DataFrame([{
            "ts": p.ts, "open": p.open, "high": p.high,
            "low": p.low, "close": p.close, "volume": p.volume,
        } for p in price_rows])
        df["ts"] = pd.to_datetime(df["ts"])
        df = df.sort_values("ts").reset_index(drop=True)

        try:
            start_date = df["ts"].min().date()
            macro_df = fetch_macro_features(start_date, date.today() + timedelta(days=1))
        except Exception:
            macro_df = None

        horizon_days = _HORIZON_DAYS.get(horizon.upper(), 10)
        label_thr = compute_label_threshold(df.iloc[-min(252, len(df)):], horizon_days)

        X_feat, _, _ = build_features(
            df,
            horizon=horizon_days,
            macro_df=macro_df,
            label_threshold=label_thr,
            inference_mode=True,
        )
        if X_feat.empty:
            return None

        latest = X_feat.iloc[-1]
        vec: list[float] = [
            float(latest.get(col, 0.0)) if not (
                isinstance(latest.get(col), float) and np.isnan(latest.get(col))
            ) else 0.0
            for col in FEATURE_COLUMNS
        ]

        # Meta features (must match training order)
        vec.append(float(SECTOR_MAP.get(sector or "", -1)))
        vec.append(float(_market_cap_bin(market_cap)))
        vec.append(float(HORIZON_MAP.get(horizon.upper(), -1)))
        vec.append(float(confidence))
        vec.append(float(fused_prob))
        vec.append(float(ta_score))

        X_raw = np.array([vec], dtype=np.float32)
        X_sel = X_raw[:, non_const]
        X_scaled = scaler.transform(X_sel)
        prob = float(model.predict_proba(X_scaled)[0, 1])
        return prob

    except Exception as exc:
        log.warning("meta_trainer.predict_failed symbol=%s exc=%s", symbol, exc)
        return None
