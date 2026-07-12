"""Cross-symbol meta-learning model — T89.

Trains a single XGBoost model on all signal_outcomes across all symbols.
Adds sector_code, market_cap_bin, horizon_code as additional features for
cross-symbol generalization. Used as cold-start prior and 4th ensemble member.

Feature vector: FEATURE_COLUMNS (len(FEATURE_COLUMNS) in builder.py — AUD232-031: a
hardcoded "(61)" here was already stale, the real count had drifted to 60; deliberately
not repeating a hardcoded number so this comment can't drift out of sync again) filtered
to non-constant cols, plus:
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
# AUD232-054: kept in sync with builder.py's SECTOR_ETF_MAP sector-name coverage (see its
# comment) — "Financial" appended at 11 rather than renumbering existing entries, since
# existing trained model bundles already encode sectors against these exact integers and
# reordering would silently corrupt their learned sector coefficients.
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
    "Financial": 11,
}

HORIZON_MAP: dict[str, int] = {
    "SHORT": 0,
    "SWING": 1,
    "LONG": 2,
    "GROWTH": 3,
}

# AUD232-056: import trainer.py's _HORIZON_BY_STYLE instead of an independent duplicate —
# trainer.py is the module this file's own callers (predict_latest_ensemble_three) rely on,
# so it is the de facto authoritative definition of what each style's horizon means.
from .trainer import _HORIZON_BY_STYLE as _HORIZON_DAYS


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
        # AUD232-002/017/030/033: two bugs in the original query, both fixed here:
        # (1) joined stocks on the denormalized `symbol` string (`st.symbol = so.symbol`)
        #     instead of the real, indexed FK `so.stock_id` — stocks.symbol is only unique
        #     per (symbol, exchange), so two listings sharing a ticker string could silently
        #     fan out or attach the wrong sector/market_cap to an outcome.
        # (2) the fundamentals join had no as_of/date filter or LIMIT — every signal_outcome
        #     row fanned out once per historical fundamentals snapshot for that stock, each
        #     carrying an arbitrary (non-deterministic, not point-in-time) market_cap. Fixed
        #     with a LATERAL join picking the single most-recent fundamentals row AS OF
        #     so.signal_date (not "most recent overall"), matching the point-in-time
        #     discipline T228-POINT-IN-TIME-FUNDAMENTALS/T234-ML-FUND-BROADCAST-LEAKAGE
        #     already established for builder.py's per-row fundamental features.
        rows = _session.execute(text("""
            SELECT so.symbol, so.horizon, so.signal_date, so.confidence,
                   so.fused_prob, so.ta_score, so.is_correct, so.signal_direction,
                   st.sector, f.market_cap
            FROM signal_outcomes so
            JOIN stocks st ON st.id = so.stock_id
            LEFT JOIN LATERAL (
                SELECT market_cap FROM fundamentals
                WHERE stock_id = st.id AND as_of <= so.signal_date
                ORDER BY as_of DESC
                LIMIT 1
            ) f ON true
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
            # AUD232-046: BUY (63.3%) and SELL (43.7%) signal_outcomes have documented divergent
            # base rates (see T232-OC5 comment in routes.py) — every other calibration consumer
            # (calibrate_conviction_weights, confidence-calibration, outcomes/summary) keys by
            # signal_direction instead of pooling. Add it as a feature so the model can learn
            # direction-specific patterns rather than blending two populations with different
            # base rates into one implicit prior that silently drifts with the BUY/SELL mix.
            vec.append(1.0 if str(row.signal_direction).upper() == "BUY" else 0.0)
            # T237-ML-META1: row.confidence is stored 0-100 (SignalOutcome.confidence), but
            # predict_meta()'s inference call site (trainer.py) divides xgb["confidence"] by 100
            # before passing it in — normalize here too so training and inference features match.
            vec.append(float(row.confidence) / 100.0 if row.confidence is not None else 0.0)
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

    # SELFIMPROVE-PROMOTION-GATES-INCOMPLETE: this used to unconditionally overwrite
    # META_MODEL_PATH regardless of how the new AUC compared to whatever bundle was already
    # deployed — the exact failure mode every OTHER calibration mechanism in this codebase
    # (calibrate_ta_weights, calibrate_conviction_weights, calibrate_ml_weight,
    # outcomes_calibrate_apply, tune_style_profiles) was explicitly built to avoid. Load the
    # CURRENTLY DEPLOYED bundle's own stored AUC (predict_meta() already reads this back at
    # inference time) and refuse to replace it with something strictly worse.
    #
    # MIN_AUC_IMPROVEMENT is deliberately 0.0 (reject only if strictly worse), not a positive
    # margin: AUC on this validation slice (20% of up to 20,000 rows, retrained monthly) is
    # noisy enough that an invented margin with no real variance data behind it would be
    # security theater, and a margin set too strict would fail every future retrain forever,
    # silently freezing a pipeline that's supposed to keep improving. See
    # docs/DESIGN_MODEL_PROMOTION_GATES_2026-07-12.md §2.3 for the full reasoning — revisit
    # once real promotion_rejected/promoted log volume exists.
    MIN_AUC_IMPROVEMENT = 0.0
    previous_auc: float | None = None
    if META_MODEL_PATH.exists():
        try:
            previous_bundle = joblib.load(META_MODEL_PATH)
            previous_auc = previous_bundle.get("auc")
        except Exception as exc:
            # An unreadable/corrupt existing bundle must NOT block the new one — failing
            # closed here would turn a corrupted file into a permanent retrain freeze, worse
            # than the bug this gate exists to prevent. Same fail-open principle as
            # hard_rejects.py's macro-blackout check.
            log.warning("meta_trainer.previous_bundle_unreadable error=%s", exc)

    if previous_auc is not None and auc < previous_auc - MIN_AUC_IMPROVEMENT:
        log.warning(
            "meta_trainer.promotion_rejected new_auc=%.4f previous_auc=%.4f n_samples=%d",
            auc, previous_auc, len(records),
        )
        _record_promotion_status(promoted=False, auc=auc, previous_auc=previous_auc, n_samples=len(records))
        return {
            "trained": True, "promoted": False, "n_samples": len(records),
            "auc": round(auc, 4), "previous_auc": round(previous_auc, 4),
        }

    META_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)

    bundle = {
        "model": model,
        "scaler": scaler,
        "non_const": non_const,
        "feature_columns": list(FEATURE_COLUMNS),
        "n_meta_features": 6,  # sector_code, market_cap_bin, horizon_code, confidence, fused_prob, ta_score
        "auc": round(auc, 4),
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

    log.info("meta_trainer.promoted new_auc=%.4f previous_auc=%s", auc, previous_auc)
    _record_promotion_status(promoted=True, auc=auc, previous_auc=previous_auc, n_samples=len(records))
    return {
        "trained": True, "promoted": True, "n_samples": len(records),
        "auc": round(auc, 4), "previous_auc": round(previous_auc, 4) if previous_auc is not None else None,
    }


def _record_promotion_status(promoted: bool, auc: float, previous_auc: float | None, n_samples: int) -> None:
    """Write this retrain's promotion verdict to the SAME Redis key namespace market-data's
    scheduler already uses for job status (scheduler:job:{name}) — market-data's own
    GET /scheduler-status endpoint (already consumed by admin-health.tsx) reads any key
    matching scheduler:job:*, so writing here directly, from ml-prediction, surfaces this
    result in the existing dashboard with zero changes needed on the market-data/frontend
    side. Also writes a small promoted/rejected history list (last 20 runs) to a separate key
    for signal-tuning.tsx's more detailed view — see docs/DESIGN_MODEL_PROMOTION_GATES_2026-07-12.md
    §4 decision 3. Best-effort: a Redis failure here must never break the retrain itself, since
    the actual model file has already been written (or correctly not written) by this point.
    """
    try:
        import json as _json
        from datetime import datetime, timezone
        import redis as _redis_lib
        from common.config import get_settings as _get_settings

        r = _redis_lib.from_url(_get_settings().redis_url, decode_responses=True)
        now_iso = datetime.now(timezone.utc).isoformat()

        r.setex(
            "scheduler:job:meta_model_promotion",
            86400 * 14,
            _json.dumps({
                "job": "meta_model_promotion",
                "status": "ok" if promoted else "skipped: promotion rejected (new AUC worse than deployed)",
                "last_run": now_iso,
                "duration_s": 0.0,
                "error": None,
            }),
        )

        history_key = "meta_model:promotion_history"
        raw = r.get(history_key)
        history = _json.loads(raw) if raw else []
        history.append({
            "ts": now_iso, "promoted": promoted, "auc": round(auc, 4),
            "previous_auc": round(previous_auc, 4) if previous_auc is not None else None,
            "n_samples": n_samples,
        })
        history = history[-20:]  # keep the last 20 runs only
        r.setex(history_key, 86400 * 90, _json.dumps(history))
    except Exception as exc:
        log.warning("meta_trainer.promotion_status_write_failed error=%s", exc)


def predict_meta(
    symbol: str,
    horizon: str,
    confidence: float,
    fused_prob: float,
    ta_score: float,
    sector: str | None = None,
    market_cap: float | None = None,
    direction: str = "BUY",
) -> float | None:
    """Return meta-model probability for a single prediction, or None if model unavailable.

    This function never crashes the main prediction path — any exception returns None.
    Loads prices internally so it can build the feature vector at the current date.

    direction defaults to "BUY" — every current call site (trainer.py's ensemble blend)
    evaluates bullish_probability from BUY-only per-model training (_load_outcome_features
    filters signal_direction == "BUY"), so there is no live SELL-direction caller yet. Accept
    it explicitly so callers aren't silently limited if a SELL-side caller is added later.
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
        auc = bundle.get("auc", 0.0)
        if auc < 0.55:
            log.debug("meta_trainer.predict_skipped_low_auc symbol=%s auc=%.4f", symbol, auc)
            return None
        model = bundle["model"]
        scaler = bundle["scaler"]
        non_const = bundle["non_const"]
        # AUD232-008: use the FEATURE_COLUMNS snapshot saved in the bundle at train time, not
        # the live import above — builder.py's FEATURE_COLUMNS has already changed length
        # multiple times (T220-F/T237-ML2, CRIT-3/4). Building vec from the CURRENT list while
        # non_const holds positional indices from the OLD list either raises IndexError (if the
        # new vector is shorter) or silently selects the wrong, now-shifted columns (if longer
        # or reordered) — the same bug class as this codebase's documented "index 66 out of
        # bounds" incident. bundle["feature_columns"] was already saved at train time but never
        # read back until this fix.
        saved_feature_columns = bundle.get("feature_columns")
        if saved_feature_columns and saved_feature_columns != list(FEATURE_COLUMNS):
            log.warning(
                "meta_trainer.feature_columns_drift symbol=%s "
                "saved_len=%d live_len=%d — using saved snapshot for this prediction",
                symbol, len(saved_feature_columns), len(FEATURE_COLUMNS),
            )
        feature_columns_for_vec = saved_feature_columns or list(FEATURE_COLUMNS)

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
            for col in feature_columns_for_vec
        ]

        # Meta features (must match training order)
        vec.append(float(SECTOR_MAP.get(sector or "", -1)))
        vec.append(float(_market_cap_bin(market_cap)))
        vec.append(float(HORIZON_MAP.get(horizon.upper(), -1)))
        vec.append(float(confidence))
        vec.append(float(fused_prob))
        vec.append(float(ta_score))
        vec.append(1.0 if str(direction).upper() == "BUY" else 0.0)

        X_raw = np.array([vec], dtype=np.float32)
        # Defensive bounds check: non_const holds positional indices computed at train time
        # against len(feature_columns_for_vec) + 6 meta features. If an older bundle predates
        # the "feature_columns" field (saved_feature_columns is None) and the live
        # FEATURE_COLUMNS has since changed length, this catches the mismatch explicitly
        # instead of letting X_raw[:, non_const] raise a raw IndexError.
        if non_const.max(initial=-1) >= X_raw.shape[1]:
            log.warning(
                "meta_trainer.predict_skipped_shape_mismatch symbol=%s "
                "vec_len=%d max_non_const_index=%d — stale model bundle, skipping meta prediction",
                symbol, X_raw.shape[1], int(non_const.max(initial=-1)),
            )
            return None
        X_sel = X_raw[:, non_const]
        X_scaled = scaler.transform(X_sel)
        prob = float(model.predict_proba(X_scaled)[0, 1])
        return prob

    except Exception as exc:
        log.warning("meta_trainer.predict_failed symbol=%s exc=%s", symbol, exc)
        return None
