"""Trainer — walks the DB for price history, builds features, fits & persists."""
from __future__ import annotations

import os
import tempfile
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score, f1_score, precision_recall_curve,
    precision_score, recall_score, roc_auc_score,
)
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from sklearn.utils.class_weight import compute_sample_weight
from sqlalchemy import select

from common.config import get_settings
from common.logging import get_logger
from db import EarningsEvent, Fundamental, Price, SessionLocal, Signal, SignalOutcome, Stock, TimeFrame
from sqlalchemy import desc as sa_desc

from ..features import build_features, compute_label_threshold, fetch_macro_features, fetch_sector_features, FEATURE_COLUMNS, FUNDAMENTAL_COLUMNS, SECTOR_COLUMNS, WEEKLY_COLUMNS
from ..models import BaseModel, get_model

log = get_logger("trainer")
_settings = get_settings()

_HORIZON_BY_STYLE: dict[str, int] = {
    "SHORT":  5,
    "SWING":  10,
    "LONG":   20,
    "GROWTH": 15,
}

_MIN_PRECISION = 0.60  # fallback precision floor (SWING)

# SHORT trades have little time to recover from false entries — require tighter precision.
# LONG trades can absorb more noise over a 90-day hold — accept a lower floor.
# Survivorship bias: training universe is active stocks only (delisted stocks
# are underrepresented). Each style's precision floor is raised by 3pp to
# compensate for the known upward bullish bias this introduces.
_PRECISION_BY_STYLE: dict[str, float] = {
    "SHORT":  0.73,  # was 0.70
    "SWING":  0.63,  # was 0.60
    "LONG":   0.53,  # was 0.50
    "GROWTH": 0.63,  # same as SWING (similar horizon)
}


def _load_prices(symbol: str, lookback_days: int = 365 * 5) -> pd.DataFrame:
    with SessionLocal() as session:
        stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalar_one_or_none()
        if not stock:
            raise ValueError(f"Unknown symbol: {symbol}")
        since = date.today() - timedelta(days=lookback_days)
        rows = session.execute(
            select(Price)
            .where(
                Price.stock_id == stock.id,
                Price.timeframe == TimeFrame.D1,
                Price.ts >= since,
            )
            .order_by(Price.ts)
        ).scalars().all()
    if not rows:
        raise ValueError(f"No prices for {symbol} — run ingestion first")
    return pd.DataFrame(
        {
            "ts": [r.ts for r in rows],
            "open": [r.open for r in rows],
            "high": [r.high for r in rows],
            "low": [r.low for r in rows],
            "close": [r.close for r in rows],
            "volume": [r.volume for r in rows],
        }
    )


def _load_fundamentals(symbol: str) -> dict | None:
    """Fetch the most-recent Fundamental row for a symbol and return as a dict.

    Returns None if no row exists (model will train with NaN fundamental features).
    fcf_yield is computed here as free_cashflow / market_cap so builder.py
    only needs to consume scalar values.
    Also computes short_ratio_delta from the two most-recent snapshots (covering=negative delta).
    """
    with SessionLocal() as session:
        stock = session.execute(
            select(Stock).where(Stock.symbol == symbol)
        ).scalar_one_or_none()
        if not stock:
            return None
        rows = session.execute(
            select(Fundamental)
            .where(Fundamental.stock_id == stock.id)
            .order_by(sa_desc(Fundamental.as_of))
            .limit(2)
        ).scalars().all()
    if not rows:
        return None
    row = rows[0]
    fcf = row.free_cashflow
    mkt = row.market_cap
    fcf_yield = (fcf / mkt) if (fcf and mkt and mkt > 0) else None
    # short_ratio_delta: negative = short covering (bullish), positive = building shorts (bearish)
    short_ratio_delta: float | None = None
    if len(rows) >= 2 and row.short_ratio is not None and rows[1].short_ratio is not None:
        short_ratio_delta = row.short_ratio - rows[1].short_ratio
    return {
        "revenue_growth":      row.revenue_growth,
        "earnings_growth":     row.earnings_growth,
        "gross_margin":        row.gross_margin,
        "return_on_equity":    row.return_on_equity,
        "fcf_yield":           fcf_yield,
        "short_ratio":         row.short_ratio,
        "short_ratio_delta":   short_ratio_delta,
        "recommendation_mean": row.recommendation_mean,
        "price_to_book":       row.price_to_book,
        "peg_ratio":           getattr(row, "peg_ratio", None),
        "debt_to_equity":      getattr(row, "debt_to_equity", None),
    }


def _load_earnings_features(symbol: str) -> dict:
    """Fetch EPS beat streak, surprise average, and days-to-next-earnings.

    eps_beat_streak  — consecutive quarters beating consensus estimate (0=no streak; capped at 4)
    eps_surprise_avg — rolling 4-quarter average surprise_pct (positive = consistent beats)
    days_to_earnings — calendar days to next expected report (0–90; 90 when unknown or far)

    All values are NaN-tolerant — missing data returns NaN keys (XGBoost handles natively).
    """
    today = date.today()
    result: dict = {}
    with SessionLocal() as session:
        stock = session.execute(
            select(Stock).where(Stock.symbol == symbol)
        ).scalar_one_or_none()
        if not stock:
            return result

        # Completed earnings (with actual EPS) — last 5 quarters
        past = session.execute(
            select(EarningsEvent)
            .where(
                EarningsEvent.stock_id == stock.id,
                EarningsEvent.report_date <= today,
                EarningsEvent.eps_actual.isnot(None),
            )
            .order_by(sa_desc(EarningsEvent.report_date))
            .limit(5)
        ).scalars().all()

        # Next upcoming earnings date
        next_date = session.execute(
            select(EarningsEvent.report_date)
            .where(
                EarningsEvent.stock_id == stock.id,
                EarningsEvent.report_date > today,
            )
            .order_by(EarningsEvent.report_date)
            .limit(1)
        ).scalar_one_or_none()

    # EPS beat streak: count consecutive beats from most-recent going back
    beat_streak = 0
    for ev in past:
        if ev.eps_actual is not None and ev.eps_estimate is not None and ev.eps_estimate != 0:
            if ev.eps_actual > ev.eps_estimate:
                beat_streak += 1
            else:
                break
        elif ev.surprise_pct is not None:
            if ev.surprise_pct > 0:
                beat_streak += 1
            else:
                break
    result["eps_beat_streak"] = float(min(beat_streak, 4))

    # Rolling 4-quarter surprise average
    surprises = [ev.surprise_pct for ev in past[:4] if ev.surprise_pct is not None]
    result["eps_surprise_avg"] = float(np.mean(surprises)) if surprises else None

    # Days to next earnings — clamp to [0, 90]; use 90 when no upcoming date known
    if next_date is not None:
        days = (next_date - today).days
        result["days_to_earnings"] = float(min(max(days, 0), 90))
    else:
        result["days_to_earnings"] = 90.0  # unknown / far — treat as low-urgency

    return result


def _artifact_path(symbol: str, model_name: str, style: str = "SWING") -> Path:
    """Return the model artifact path for the given symbol, model type, and training style.

    SWING falls back to legacy {symbol}.joblib so existing artifacts continue to
    work until they are retrained with the new per-style naming convention.
    """
    s = style.upper()
    base = Path(_settings.model_dir) / model_name
    if s == "SWING":
        new_path = base / f"{symbol}_swing.joblib"
        legacy_path = base / f"{symbol}.joblib"
        # Prefer legacy if new-style artifact does not exist yet (backward compat)
        return legacy_path if (legacy_path.exists() and not new_path.exists()) else new_path
    return base / f"{symbol}_{s.lower()}.joblib"


def _params_path(symbol: str) -> Path:
    return Path(_settings.model_dir) / "xgboost" / f"{symbol}_params.json"


def _load_best_params(symbol: str) -> dict:
    """Load Optuna-tuned hyperparams if they exist for this symbol."""
    import json
    p = _params_path(symbol)
    if p.exists():
        with open(p) as f:
            return json.load(f)
    return {}


def _precision_threshold(
    y_true: np.ndarray,
    y_prob: np.ndarray,
    min_precision: float = _MIN_PRECISION,
    symbol: str = "",
) -> float:
    """Find the lowest threshold where precision >= min_precision and recall >= 5%.

    For trading we care about precision (when we say BUY, we're right) more than
    recall (we don't need to catch every winner).

    Two-stage fallback:
      1. precision >= min_precision AND recall >= 5%  (ideal)
      2. precision >= min_precision, any recall       (high-precision, low-recall model)
      3. 0.5 — model has no signal at target precision
    """
    if len(np.unique(y_true)) < 2:
        return 0.5
    precisions, recalls, thresholds = precision_recall_curve(y_true, y_prob)
    # thresholds[i] matches precisions[i] / recalls[i] (last element of precisions/recalls
    # has no threshold — that's the (1.0, 0.0) sentinel).
    valid = [
        t for t, p, r in zip(thresholds, precisions[:-1], recalls[:-1])
        if p >= min_precision and r >= 0.05
    ]
    if valid:
        return float(min(valid))

    # Stage 2: precision target met but recall < 5% — still tradeable, just rare signals
    prec_only = [
        t for t, p in zip(thresholds, precisions[:-1])
        if p >= min_precision
    ]
    if prec_only:
        log.warning(
            "train.threshold_low_recall",
            symbol=symbol,
            min_precision=min_precision,
            note="precision target achievable but recall<5%; signals will be rare",
        )
        return float(min(prec_only))

    log.warning(
        "train.threshold_fallback",
        symbol=symbol,
        min_precision=min_precision,
        note="model cannot achieve precision target on test set; falling back to 0.5",
    )
    return 0.5


def _recency_weights(n: int, newest_to_oldest_ratio: float = 5.0) -> np.ndarray:
    """Exponential weights so most-recent bar has ~ratio× the weight of oldest.

    Normalised so the mean weight equals 1 (total weight ≈ n, consistent with
    an unweighted dataset of the same size).
    """
    w = np.exp(np.log(newest_to_oldest_ratio) * np.arange(n) / max(n - 1, 1))
    return w / w.mean()


def _blend_weights(y: np.ndarray, recency_w: np.ndarray) -> np.ndarray:
    """Blend recency weights with balanced class weights, then enforce equal class mass.

    Three-step process (AUD-C1 fix):
      1. Multiply recency weight by class balance weight per sample.
      2. Rescale each class so its total weight equals half the grand total.
         Without this step, recent bull-market samples can dominate even after
         class balancing because the majority class gets more recent rows.
      3. Normalise to mean=1 so total effective sample size is preserved.
    """
    class_w = compute_sample_weight("balanced", y)
    combined = recency_w * class_w
    # Enforce equal class mass after blending.
    target_per_class = combined.sum() / 2.0
    for cls in np.unique(y):
        mask = y == cls
        cls_sum = combined[mask].sum()
        if cls_sum > 0:
            combined[mask] *= target_per_class / cls_sum
    return combined / combined.mean()


def _load_outcome_features(symbol: str, style: str = "SWING", lookback_days: int = 365) -> tuple[pd.DataFrame, pd.Series]:
    """Load closed signal_outcomes for this symbol and reconstruct feature vectors.

    For each closed SignalOutcome (is_correct is not None), looks up the price bar
    on signal_date and rebuilds the feature vector using the same build_features()
    pipeline. Returns (X_outcomes, y_outcomes) aligned on date index.

    Min 20 outcomes required — otherwise returns empty DataFrames.
    Called from train_model() to augment training data with real live trading labels.
    Outcomes are weighted 2× relative to price-history training rows.
    """
    from datetime import date as _date, timedelta as _td
    from db import SignalHorizon

    cutoff = _date.today() - _td(days=lookback_days)
    try:
        horizon_val = SignalHorizon[style.upper()]
    except KeyError:
        return pd.DataFrame(), pd.Series(dtype=int)

    with SessionLocal() as session:
        stock = session.execute(select(Stock).where(Stock.symbol == symbol.upper())).scalar_one_or_none()
        if stock is None:
            return pd.DataFrame(), pd.Series(dtype=int)

        outcomes = session.execute(
            select(SignalOutcome).where(
                SignalOutcome.stock_id == stock.id,
                SignalOutcome.horizon == horizon_val,
                SignalOutcome.signal_direction == "BUY",
                SignalOutcome.is_correct.is_not(None),
                SignalOutcome.signal_date >= cutoff,
            )
        ).scalars().all()

        if len(outcomes) < 20:
            return pd.DataFrame(), pd.Series(dtype=int)

        # For each outcome, look up all price bars from (signal_date - 300d) to build features
        outcome_dates = sorted({o.signal_date for o in outcomes})
        signal_date_set = {o.signal_date for o in outcomes}
        label_map = {o.signal_date: int(o.is_correct) for o in outcomes}

        # Fetch enough price history to build features for the earliest signal date
        earliest = min(outcome_dates) - _td(days=400)
        prices = session.execute(
            select(Price).where(
                Price.stock_id == stock.id,
                Price.ts >= earliest,
                Price.timeframe == TimeFrame.D1,
            ).order_by(Price.ts)
        ).scalars().all()

    if len(prices) < 100:
        return pd.DataFrame(), pd.Series(dtype=int)

    df = pd.DataFrame([{
        "ts": p.ts,
        "open": p.open, "high": p.high, "low": p.low, "close": p.close, "volume": p.volume,
    } for p in prices])
    # Keep "ts" as a column (build_features reads df["ts"] internally).
    # Sort by date so rolling windows are computed in chronological order.
    df["ts"] = pd.to_datetime(df["ts"])
    df = df.sort_values("ts").reset_index(drop=True)

    try:
        _outcome_horizon = {"SWING": 10, "LONG": 20, "GROWTH": 15, "SHORT": 5}.get(style.upper(), 10)
        X_full, y_dir, _ = build_features(df, horizon=_outcome_horizon, macro_df=None)
    except Exception:
        return pd.DataFrame(), pd.Series(dtype=int)

    if X_full.empty:
        return pd.DataFrame(), pd.Series(dtype=int)

    # Assign a date-based index so we can look up rows by signal_date.
    # build_features returns X with df's RangeIndex; map each position back to
    # the normalized date from the "ts" column using the surviving mask positions.
    ts_dates = df["ts"].dt.normalize().iloc[X_full.index]
    X_full.index = ts_dates.values

    outcome_idx = [d for d in [pd.Timestamp(d) for d in signal_date_set] if d in X_full.index]
    if not outcome_idx:
        return pd.DataFrame(), pd.Series(dtype=int)

    X_out = X_full.loc[outcome_idx]
    y_out = pd.Series([label_map[d.date()] for d in outcome_idx], index=X_out.index, dtype=int)

    return X_out, y_out


def train_model(
    symbol: str,
    model_name: str = "xgboost",
    horizon: int = 5,
    hyperparams: dict | None = None,
    style: str = "SWING",
) -> dict:
    try:
        df = _load_prices(symbol)
    except ValueError as exc:
        log.warning("train.skipped", symbol=symbol, reason=str(exc))
        return {"symbol": symbol, "skipped": True, "reason": str(exc)}

    # Exclude any bar timestamped today — partially-observed intraday bars skew
    # rolling features (SMA, ATR, z-scores) even though their label is dropped.
    today = date.today()
    df = df[pd.to_datetime(df["ts"]).dt.date < today].copy()
    if df.empty:
        log.warning("train.skipped", symbol=symbol, reason="all bars are today (post-open ingest)")
        return {"symbol": symbol, "skipped": True, "reason": "no closed bars available"}

    # --- Macro features (SPY + VIX) give market-wide context to every symbol ---
    try:
        start_date = pd.to_datetime(df["ts"]).min().date()
        end_date = date.today() + timedelta(days=1)
        macro_df = fetch_macro_features(start_date, end_date)
    except Exception:
        macro_df = None

    # TIER90: sector relative strength vs SPY from DB prices
    sector_df = fetch_sector_features(symbol, start_date, end_date)

    # Per-symbol volatility-adjusted dead zone — computed on training rows only
    # to prevent future volatility from leaking into the label dead-zone boundary.
    _train_rows = int(len(df) * 0.70)
    label_threshold = compute_label_threshold(df.iloc[:max(_train_rows, 60)], horizon)

    fund_data: dict = {}
    try:
        fund_data = _load_fundamentals(symbol) or {}
    except Exception:
        pass
    try:
        fund_data.update(_load_earnings_features(symbol))
    except Exception:
        pass

    X, y_dir, y_ret = build_features(
        df, horizon=horizon, macro_df=macro_df, label_threshold=label_threshold,
        fund_data=fund_data, sector_df=sector_df,
    )
    if len(X) < 200:
        log.warning("train.skipped", symbol=symbol, reason=f"only {len(X)} clean samples")
        return {"symbol": symbol, "skipped": True, "reason": f"only {len(X)} clean samples"}

    # Tier 87 — Outcome-informed augmentation: append closed signal_outcomes as
    # additional training rows. These rows carry real live-trading labels (is_correct)
    # tied to actual strategy decisions — higher-quality ground truth than synthetic
    # forward-return labels. Falls back silently if <20 outcomes.
    # Outcome rows are 2× weighted in the final model fit but NOT in CV (to avoid
    # contaminating the OOS CV measurement with live-trading data).
    n_outcome_rows = 0
    outcome_dates_set: set = set()  # tracks which X rows came from outcomes
    try:
        X_out, y_out = _load_outcome_features(symbol, style=style)
        if not X_out.empty and len(X_out) >= 20:
            shared_cols = [c for c in FEATURE_COLUMNS if c in X_out.columns and c in X.columns]
            if shared_cols:
                X_out = X_out[shared_cols]
                # Drop outcome rows that exactly overlap main training set dates
                overlap_idx = X.index.intersection(X_out.index)
                X_out = X_out.drop(index=overlap_idx, errors="ignore")
                y_out = y_out.drop(index=overlap_idx, errors="ignore")
                if len(X_out) >= 5:
                    # Track outcome row positions by integer index (after ignore_index=True reset).
                    # Append at the TAIL so they land past the 70% CV split boundary —
                    # keeping CV folds uncontaminated. 2× weighting fires when the dataset
                    # is small enough that outcome rows fall within the 70% train window.
                    _n_before_concat = len(X[shared_cols])
                    outcome_dates_set = set(range(_n_before_concat, _n_before_concat + len(X_out)))
                    X = pd.concat([X[shared_cols], X_out], ignore_index=True)
                    y_dir = pd.concat([y_dir, y_out], ignore_index=True)
                    # Extend y_ret to match new X length; outcome rows get NaN forward
                    # returns (unknown) — they are excluded from IC computation below
                    # because NaN rows produce NaN IC which is filtered out.
                    y_ret = pd.concat(
                        [y_ret, pd.Series([np.nan] * len(X_out))], ignore_index=True
                    )
                    n_outcome_rows = len(X_out)
                    log.info("train.outcome_augment", symbol=symbol, n_outcomes=n_outcome_rows)
    except Exception as _oe:
        log.warning("train.outcome_augment_failed", symbol=symbol, error=str(_oe))

    # --- Hyperparams: passed > saved tuned > defaults ---
    if hyperparams is None and model_name == "xgboost":
        hyperparams = _load_best_params(symbol)

    # --- SA-9: Walk-forward OOS metrics (5-fold, no data leakage) ---
    # Each fold trains on months 1–N, evaluates on month N+1 (true OOS).
    # IC = Spearman rank correlation between predicted probability and actual
    # forward return — measures whether the model ranks returns correctly, not
    # just whether it classifies direction correctly.
    cv_aucs: list[float] = []
    cv_accs: list[float] = []
    oos_precisions: list[float] = []
    oos_recalls: list[float] = []
    oos_ics: list[float] = []
    # Compute split point before CV so the loop only sees the training portion —
    # splitting on the full X would leak future rows into validation folds.
    split_train = int(len(X) * 0.70)
    tscv = TimeSeriesSplit(n_splits=5)
    for tr_idx, val_idx in tscv.split(X.iloc[:split_train]):
        X_cv_tr, X_cv_val = X.iloc[tr_idx].values, X.iloc[val_idx].values
        y_cv_tr, y_cv_val = y_dir.iloc[tr_idx].values, y_dir.iloc[val_idx].values
        sc = StandardScaler()
        X_cv_tr_s = sc.fit_transform(X_cv_tr)
        X_cv_val_s = sc.transform(X_cv_val)

        # ML-FIX-2: recency + balanced class weights combined
        cv_recency = _recency_weights(len(tr_idx), newest_to_oldest_ratio=5.0)
        cv_weights = _blend_weights(y_cv_tr, cv_recency)
        cv_model = get_model(model_name, **(hyperparams or {}))
        cv_model.fit(X_cv_tr_s, y_cv_tr, sample_weight=cv_weights)

        preds_proba = cv_model.predict_proba(X_cv_val_s)[:, 1]  # positive-class only, shape (n,)
        if len(np.unique(y_cv_val)) > 1:
            cv_aucs.append(roc_auc_score(y_cv_val, preds_proba))
        preds_binary = (preds_proba > 0.5).astype(int)
        cv_accs.append(accuracy_score(y_cv_val, preds_binary))
        oos_precisions.append(float(precision_score(y_cv_val, preds_binary, zero_division=0)))
        oos_recalls.append(float(recall_score(y_cv_val, preds_binary, zero_division=0)))

        # IC: Spearman corr between predicted probability and actual return.
        # Drop any rows where y_ret is NaN (e.g. outcome-augmented rows have no
        # synthetic forward return; they never appear in CV folds but guard anyway).
        ret_cv_val = y_ret.iloc[val_idx].values
        _valid_ic = ~np.isnan(ret_cv_val)
        if _valid_ic.sum() >= 5:
            ic, _ = spearmanr(preds_proba[_valid_ic], ret_cv_val[_valid_ic])
            if not np.isnan(ic):
                oos_ics.append(float(ic))

    # --- Four-way split: train / early-stop / calibration / threshold evaluation ---
    # AUD-C2 fix: XGBoost/LightGBM partially overfit to their eval_set during early stopping,
    # so using the same set for calibration produces optimistically biased probabilities.
    # Solution: dedicate a separate early-stop slice (80%) that the model sees during fitting,
    # and keep the calibration slice (80–90%) fully clean — never passed to fit() or eval_set.
    # split_train already computed above (reused here for clarity).
    split_es  = int(len(X) * 0.80)   # end of early-stop window (same as split_train when train=70%)
    split_cal = int(len(X) * 0.90)
    X_train = X.iloc[:split_train]
    X_es    = X.iloc[split_train:split_es]
    X_cal   = X.iloc[split_es:split_cal]
    X_test  = X.iloc[split_cal:]
    y_train = y_dir.iloc[:split_train]
    y_es    = y_dir.iloc[split_train:split_es]
    y_cal   = y_dir.iloc[split_es:split_cal]
    y_test  = y_dir.iloc[split_cal:]

    if len(np.unique(y_train)) < 2:
        log.warning("train.skipped", symbol=symbol, reason="degenerate labels — all same class after dead-zone filter")
        return {"symbol": symbol, "skipped": True, "reason": "degenerate labels after dead-zone filter"}

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train.values)
    X_es_s    = scaler.transform(X_es.values)
    X_cal_s   = scaler.transform(X_cal.values)
    X_test_s  = scaler.transform(X_test.values)

    # ML-FIX-2: recency + balanced class weights blended for final training
    _recency_w = _recency_weights(len(X_train), newest_to_oldest_ratio=5.0)
    train_weights = _blend_weights(y_train.values, _recency_w)
    # Tier 87: apply 2× multiplier to outcome rows (real live-trading labels).
    # outcome_dates_set holds integer positions; X_train.index is a RangeIndex after ignore_index=True.
    if outcome_dates_set:
        _outcome_mask = np.array([i in outcome_dates_set for i in X_train.index])
        train_weights[_outcome_mask] *= 2.0

    # Early stopping on the dedicated early-stop set (X_es_s); LightGBM handles via its own
    # callbacks (AUD-M10). AUD-C2: X_cal_s is intentionally NOT passed here — keeping it clean
    # for probability calibration below.
    model = get_model(model_name, early_stopping_rounds=50, **(hyperparams or {}))
    if model_name == "xgboost":
        model.fit(
            X_train_s, y_train.values,
            sample_weight=train_weights,
            eval_set=[(X_es_s, y_es.values)],
            verbose=False,
        )
    elif model_name == "lightgbm":
        # AUD-M10: LGBMClassifier.fit() accepts eval_set; early_stopping callback injected in lgb.py
        model.fit(
            X_train_s, y_train.values,
            sample_weight=train_weights,
            eval_set=[(X_es_s, y_es.values)],
        )
    else:
        model.fit(X_train_s, y_train.values, sample_weight=train_weights)

    # --- Probability calibration (on calibration set) ---
    # Use positive-class probabilities only (shape (n,)) — both calibrators expect 1D input.
    # Platt scaling (LogisticRegression) is more stable when the calibration set is small;
    # IsotonicRegression needs ≥300 samples to avoid overfitting the monotone mapping.
    raw_cal_probs = model.predict_proba(X_cal_s)[:, 1]
    calibrator: IsotonicRegression | LogisticRegression | None = None
    if len(np.unique(y_cal)) > 1 and len(y_cal) >= 20:
        if len(y_cal) < 300:
            calibrator = LogisticRegression(C=1e6, solver="lbfgs")
            calibrator.fit(raw_cal_probs.reshape(-1, 1), y_cal.values)
        else:
            calibrator = IsotonicRegression(out_of_bounds="clip")
            calibrator.fit(raw_cal_probs, y_cal.values)

    # --- Precision-optimised BUY threshold (on held-out test set) ---
    raw_test_probs = model.predict_proba(X_test_s)[:, 1]  # shape (n,)
    if calibrator is None:
        preds = raw_test_probs
    elif isinstance(calibrator, LogisticRegression):
        preds = calibrator.predict_proba(raw_test_probs.reshape(-1, 1))[:, 1]
    else:
        preds = calibrator.predict(raw_test_probs)
    min_prec = _PRECISION_BY_STYLE.get(style.upper(), _MIN_PRECISION)
    buy_threshold = _precision_threshold(y_test.values, preds, min_precision=min_prec, symbol=symbol)

    y_pred = (preds > buy_threshold).astype(int)

    # --- Feature importance (XGBoost and RandomForest both support it) ---
    feature_importance: dict[str, float] = {}
    if hasattr(model.clf, "feature_importances_"):
        scores = model.clf.feature_importances_
        feature_importance = {
            col: round(float(scores[i]), 4)
            for i, col in enumerate(FEATURE_COLUMNS)
        }
        top5 = sorted(feature_importance, key=feature_importance.get, reverse=True)[:5]
        log.info("train.top_features", symbol=symbol, top5=top5)

    cv_auc_mean = float(np.mean(cv_aucs)) if cv_aucs else None
    if cv_auc_mean is not None and cv_auc_mean < 0.55:
        log.warning(
            "train.low_auc",
            symbol=symbol,
            cv_auc_mean=round(cv_auc_mean, 4),
            note="model is near-random; predictions will carry low weight in signal fusion",
        )

    test_auc_val = float(roc_auc_score(y_test, preds)) if len(np.unique(y_test)) > 1 else None
    overfit_gap_val = round(cv_auc_mean - test_auc_val, 4) if (cv_auc_mean is not None and test_auc_val is not None) else None
    oos_acc_mean = float(np.mean(cv_accs)) if cv_accs else None
    oos_ic_mean = round(float(np.mean(oos_ics)), 4) if oos_ics else None
    metrics = {
        "accuracy": float(accuracy_score(y_test, y_pred)),
        "auc": test_auc_val,
        "precision": float(precision_score(y_test, y_pred, zero_division=0)),
        "recall": float(recall_score(y_test, y_pred, zero_division=0)),
        "f1": float(f1_score(y_test, y_pred, zero_division=0)),
        "buy_threshold": float(buy_threshold),
        "cv_auc_mean": cv_auc_mean,
        "cv_auc_std": float(np.std(cv_aucs)) if cv_aucs else None,
        "cv_acc_mean": oos_acc_mean,
        "oos_precision_mean": round(float(np.mean(oos_precisions)), 4) if oos_precisions else None,
        "oos_recall_mean": round(float(np.mean(oos_recalls)), 4) if oos_recalls else None,
        "oos_ic_mean": oos_ic_mean,
        "overfit_gap": overfit_gap_val,
        "n_train": int(len(X_train)),
        "n_cal": int(len(X_cal)),
        "n_test": int(len(X_test)),
        "n_features": len(FEATURE_COLUMNS),
        "label_threshold": label_threshold,
    }

    # SA-9: suppress signals when OOS accuracy < 52% (coin-flip model)
    oos_suppressed = cv_auc_mean is not None and cv_auc_mean < 0.52
    if oos_suppressed:
        log.warning(
            "train.oos_suppressed",
            symbol=symbol,
            oos_acc=round(oos_acc_mean, 4),
            note="model OOS AUC < 0.52 (near coin-flip); live predictions will be held at 0.5 (neutral)",
        )

    # ML-FIX-4: overfitting detection — CV-AUC is measured on in-distribution folds;
    # test-AUC is the final held-out split. A gap > 0.10 means the model memorised
    # training patterns that don't generalise to unseen data.
    if overfit_gap_val is not None and overfit_gap_val > 0.10:
        log.warning(
            "train.overfit_detected",
            symbol=symbol,
            cv_auc=round(cv_auc_mean, 4),
            test_auc=round(test_auc_val, 4),
            gap=overfit_gap_val,
            note="CV-AUC vs test-AUC gap >0.10; consider reducing max_depth or increasing min_child_weight",
        )

    path = _artifact_path(symbol, model_name, style)
    path.parent.mkdir(parents=True, exist_ok=True)
    import joblib
    bundle = {
        "model": model,
        "scaler": scaler,
        "calibrator": calibrator,
        "buy_threshold": buy_threshold,
        "label_threshold": label_threshold,
        "metrics": metrics,
        "feature_columns": list(FEATURE_COLUMNS),
        "feature_importance": feature_importance,
        "oos_suppressed": oos_suppressed,
        "trained_at": datetime.now(timezone.utc).isoformat(),
        "style": style,
        "survivorship_bias_warning": True,  # training universe is active stocks only
        "n_outcome_rows": n_outcome_rows,  # Tier 87: live trading outcome rows used in training
    }
    # RACE-001: atomic write — write to a temp file in the same directory, then rename.
    # joblib.dump to the final path directly can produce a corrupt read if a prediction
    # request hits joblib.load mid-write. os.replace() is atomic on POSIX.
    tmp_fd, tmp_path = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        os.close(tmp_fd)
        joblib.dump(bundle, tmp_path)
        os.replace(tmp_path, path)
    except Exception:
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise

    log.info("train.done", symbol=symbol, model=model_name, **{k: v for k, v in metrics.items() if v is not None})
    return {"symbol": symbol, "model": model_name, "path": str(path), "metrics": metrics}


def load_trained(symbol: str, model_name: str, style: str = "SWING") -> tuple[BaseModel, StandardScaler, dict]:
    import joblib
    path = _artifact_path(symbol, model_name, style)
    if not path.exists():
        raise FileNotFoundError(f"No trained model at {path}")
    bundle = joblib.load(path)
    return bundle["model"], bundle["scaler"], bundle["metrics"]


_MODEL_STALE_DAYS = 30  # warn when a model artifact is older than this


def predict_latest(symbol: str, model_name: str = "xgboost", horizon: int = 5, style: str = "SWING") -> dict:
    import joblib
    import structlog as _slog
    _log = _slog.get_logger()
    path = _artifact_path(symbol, model_name, style)
    if not path.exists():
        raise FileNotFoundError(f"No trained model at {path}")
    bundle = joblib.load(path)
    model = bundle["model"]
    scaler = bundle["scaler"]
    calibrator = bundle.get("calibrator")
    buy_threshold = bundle.get("buy_threshold", 0.5)
    saved_cols = bundle.get("feature_columns", list(FEATURE_COLUMNS))

    # STALE-001: warn when the model artifact is older than _MODEL_STALE_DAYS
    trained_at_str = bundle.get("trained_at")
    model_age_days: int | None = None
    if trained_at_str:
        try:
            trained_at = datetime.fromisoformat(trained_at_str)
            if trained_at.tzinfo is None:
                trained_at = trained_at.replace(tzinfo=timezone.utc)
            model_age_days = (datetime.now(timezone.utc) - trained_at).days
            if model_age_days > _MODEL_STALE_DAYS:
                _log.warning("model.stale", symbol=symbol, model=model_name,
                             trained_at=trained_at_str, age_days=model_age_days)
        except Exception:
            pass

    df = _load_prices(symbol, lookback_days=400)

    # Fetch macro features aligned to the stock's price dates
    macro_df = None
    infer_start = None
    try:
        infer_start = pd.to_datetime(df["ts"]).min().date()
        macro_df = fetch_macro_features(infer_start, date.today() + timedelta(days=1))
    except Exception:
        pass

    # TIER90: sector RS for inference
    sector_df = fetch_sector_features(symbol, infer_start or (date.today() - timedelta(days=400)), date.today() + timedelta(days=1))

    infer_fund_data: dict = {}
    try:
        infer_fund_data = _load_fundamentals(symbol) or {}
    except Exception:
        pass
    try:
        infer_fund_data.update(_load_earnings_features(symbol))
    except Exception:
        pass

    # inference_mode=True: keeps the latest bar even without a known future return
    X, _, _ = build_features(
        df, horizon=horizon, macro_df=macro_df,
        label_threshold=0.0, inference_mode=True,
        fund_data=infer_fund_data, sector_df=sector_df,
    )
    if X.empty:
        return {"symbol": symbol, "bullish_probability": 0.5, "confidence": 0}

    # SA-9: suppress predictions for coin-flip models (OOS accuracy < 52%)
    if bundle.get("oos_suppressed"):
        log.warning("predict.oos_suppressed", symbol=symbol,
                    note="model OOS accuracy <52%; returning neutral 0.5")
        return {
            "symbol": symbol, "model": model_name,
            "bullish_probability": 0.5, "direction": "neutral",
            "confidence": 0.0, "horizon_days": horizon,
            "metrics": bundle.get("metrics", {}),
            "oos_suppressed": True,
        }

    # Preserve NaN for fundamental/weekly columns — XGBoost routes NaN natively;
    # filling with 0.0 breaks the learned split directions for sparse fundamentals.
    _nan_ok = set(FUNDAMENTAL_COLUMNS) | set(WEEKLY_COLUMNS)
    X_aligned = X.reindex(columns=saved_cols, fill_value=np.nan)
    _fill_cols = [c for c in X_aligned.columns if c not in _nan_ok]
    X_aligned[_fill_cols] = X_aligned[_fill_cols].fillna(0.0)
    Xs = scaler.transform(X_aligned.values)
    # Positive-class probability for the latest bar (calibrator expects 1D input).
    # XGBModel.predict_proba returns 1D (n_samples,); XGBClassifier returns 2D (n_samples, 2).
    proba = model.predict_proba(Xs)
    raw_prob = float(proba[-1, 1] if proba.ndim == 2 else proba[-1])

    if calibrator is None:
        prob = raw_prob
    elif isinstance(calibrator, LogisticRegression):
        prob = float(calibrator.predict_proba([[raw_prob]])[0, 1])
    else:
        prob = float(calibrator.predict([raw_prob])[0])

    # ── Feature attribution (top-5 drivers) ────────────────────────────────────
    # Approximation: importance × sign(scaled_value) gives a directional contribution.
    # Positive = pushes toward BUY, negative = pushes toward SELL.
    feature_attributions: dict[str, float] = {}
    try:
        fi = bundle.get("feature_importance", {})
        if fi and not X.empty:
            latest = X.iloc[-1]
            for feat, imp in fi.items():
                if feat in latest.index:
                    val = latest[feat]
                    if not np.isnan(val) and imp > 0:
                        # Directional contribution: positive features push BUY
                        feature_attributions[feat] = round(float(imp * np.sign(val)), 5)
            # Return top 5 by absolute contribution
            top5 = sorted(feature_attributions.items(), key=lambda x: abs(x[1]), reverse=True)[:5]
            feature_attributions = dict(top5)
    except Exception:
        pass

    return {
        "symbol": symbol,
        "model": model_name,
        "bullish_probability": prob,
        "direction": "up" if prob > buy_threshold else "down",
        "confidence": round(abs(prob - 0.5) * 200, 2),
        "horizon_days": horizon,
        "metrics": bundle.get("metrics", {}),
        "trained_at": bundle.get("trained_at"),
        "model_age_days": model_age_days,
        "feature_attributions": feature_attributions,
    }


def predict_latest_ensemble(symbol: str, horizon: int = 5, style: str = "SWING") -> dict:
    """Average XGBoost + RandomForest predictions, weighted by each model's CV AUC.

    Falls back to XGBoost-only if the RF model has not been trained for this symbol.
    Using two structurally different models (gradient boosting vs. bagging) reduces
    variance and smooths out individual model over-reactions to recent noise.
    """
    xgb = predict_latest(symbol, "xgboost", horizon, style=style)

    rf_path = _artifact_path(symbol, "random_forest", style)
    if not rf_path.exists():
        return {**xgb, "ensemble": False, "model": "xgboost"}

    try:
        rf = predict_latest(symbol, "random_forest", horizon, style=style)
    except Exception:
        return {**xgb, "ensemble": False, "model": "xgboost"}

    # Prefer held-out test AUC (unbiased) over CV AUC for internal weighting.
    xgb_auc = float((xgb.get("metrics") or {}).get("auc") or (xgb.get("metrics") or {}).get("cv_auc_mean") or 0.55)
    rf_auc  = float((rf.get("metrics") or {}).get("auc") or (rf.get("metrics") or {}).get("cv_auc_mean") or 0.55)
    total   = xgb_auc + rf_auc
    w_xgb, w_rf = xgb_auc / total, rf_auc / total

    prob = float(w_xgb * xgb["bullish_probability"] + w_rf * rf["bullish_probability"])
    # Weight-average each model's precision-optimised threshold by the same AUC weights
    # so the ensemble threshold reflects both models, not just XGBoost's.
    xgb_thr = float((xgb.get("metrics") or {}).get("buy_threshold") or 0.5)
    rf_thr  = float((rf.get("metrics") or {}).get("buy_threshold") or 0.5)
    buy_threshold = float(w_xgb * xgb_thr + w_rf * rf_thr)

    return {
        "symbol": symbol,
        "model": "ensemble_xgb_rf",
        "bullish_probability": prob,
        "direction": "up" if prob > buy_threshold else "down",
        "confidence": round(abs(prob - 0.5) * 200, 2),
        "horizon_days": horizon,
        "ensemble": True,
        "weights": {"xgboost": round(w_xgb, 2), "random_forest": round(w_rf, 2)},
        "metrics": {
            "mean_model_test_auc": round((xgb_auc + rf_auc) / 2, 4),
            "cv_auc_mean": round(
                (((xgb.get("metrics") or {}).get("cv_auc_mean") or xgb_auc) +
                 ((rf.get("metrics") or {}).get("cv_auc_mean") or rf_auc)) / 2, 4
            ),
            "buy_threshold": buy_threshold,
        },
    }


def predict_latest_ensemble_three(symbol: str, horizon: int = 5, style: str = "SWING") -> dict:
    """XGBoost (40%) + LightGBM (35%) + RandomForest (25%) weighted ensemble.

    Agreement logic (SA-8):
    - Unanimous (all 3 same direction): nudge probability 0.05 toward that direction.
    - Split (2-1): compress 0.05 toward 0.5 to reflect the disagreement.
    Per-model probabilities and agreement status are stored in the response for
    inclusion in Signal.reasons.

    Falls back gracefully: if LightGBM or RF is not trained, falls back to
    predict_latest_ensemble (XGBoost + RF), then to XGBoost-only.
    """
    xgb = predict_latest(symbol, "xgboost", horizon, style=style)

    lgb_path = _artifact_path(symbol, "lightgbm", style)
    rf_path  = _artifact_path(symbol, "random_forest", style)

    lgb_res = None
    if lgb_path.exists():
        try:
            lgb_res = predict_latest(symbol, "lightgbm", horizon, style=style)
        except Exception:
            lgb_res = None

    rf_res = None
    if rf_path.exists():
        try:
            rf_res = predict_latest(symbol, "random_forest", horizon, style=style)
        except Exception:
            rf_res = None

    # Determine which models are available and blend accordingly
    available = [(xgb, 0.40)]
    if lgb_res is not None:
        available.append((lgb_res, 0.35))
    if rf_res is not None:
        available.append((rf_res, 0.25))

    if len(available) == 1:
        # Only XGBoost — no ensemble
        return {**xgb, "ensemble": False, "model": "xgboost",
                "model_probabilities": {"xgboost": round(xgb["bullish_probability"], 4)},
                "ensemble_agreement": "single_model"}

    # Renormalize weights to sum to 1.0 for whatever subset is available
    total_w = sum(w for _, w in available)
    prob = sum(m["bullish_probability"] * w / total_w for m, w in available)

    # Agreement: bullish if prob > 0.5 per model
    probs = [m["bullish_probability"] for m, _ in available]
    directions = [p > 0.5 for p in probs]
    n_bull = sum(directions)
    n_models = len(probs)

    if n_bull == n_models:
        agreement = "unanimous_bull"
        prob = min(0.95, prob + 0.05)
    elif n_bull == 0:
        agreement = "unanimous_bear"
        prob = max(0.05, prob - 0.05)
    else:
        agreement = "majority_bull" if n_bull > n_models / 2 else "majority_bear"
        # Slight compression toward 0.5 for disagreement
        prob = prob + (0.5 - prob) * 0.05

    prob = float(prob)

    model_probs = {"xgboost": round(xgb["bullish_probability"], 4)}
    if lgb_res is not None:
        model_probs["lightgbm"] = round(lgb_res["bullish_probability"], 4)
    if rf_res is not None:
        model_probs["random_forest"] = round(rf_res["bullish_probability"], 4)

    model_name = f"ensemble_xgb{'_lgb' if lgb_res else ''}{'_rf' if rf_res else ''}"

    # Weight-average thresholds by the same portfolio weights used for blending
    buy_threshold = sum(
        float((m.get("metrics") or {}).get("buy_threshold") or 0.5) * w / total_w
        for m, w in available
    )

    xgb_auc = float((xgb.get("metrics") or {}).get("auc") or (xgb.get("metrics") or {}).get("cv_auc_mean") or 0.55)
    auc_vals = [xgb_auc]
    if lgb_res:
        auc_vals.append(float((lgb_res.get("metrics") or {}).get("auc") or 0.55))
    if rf_res:
        auc_vals.append(float((rf_res.get("metrics") or {}).get("auc") or 0.55))
    mean_auc = sum(auc_vals) / len(auc_vals)

    return {
        "symbol": symbol,
        "model": model_name,
        "bullish_probability": prob,
        "direction": "up" if prob > buy_threshold else "down",
        "confidence": round(abs(prob - 0.5) * 200, 2),
        "horizon_days": horizon,
        "ensemble": True,
        "model_probabilities": model_probs,
        "ensemble_agreement": agreement,
        "metrics": {
            "mean_model_test_auc": round(mean_auc, 4),
            "cv_auc_mean": round(mean_auc, 4),
            "buy_threshold": buy_threshold,
        },
    }


def validate_walkforward(
    symbol: str,
    model_name: str = "xgboost",
    style: str = "SWING",
    train_days: int = 252,
    test_days: int = 63,
) -> dict:
    """True walk-forward validation: retrain per window, evaluate on held-out test slice.

    Unlike the CV in train_model() (which folds on training-period data only), this
    trains one model per window using only data up to the window start, then evaluates
    on the subsequent test_days — a genuine out-of-sample simulation.

    Returns a list of per-window metrics and summary statistics (mean OOS precision,
    mean AUC, buy signal count, and annualised Sharpe proxy).
    """
    try:
        df_all = _load_prices(symbol, lookback_days=365 * 6)
    except ValueError as exc:
        return {"symbol": symbol, "error": str(exc)}

    today = date.today()
    df_all = df_all[pd.to_datetime(df_all["ts"]).dt.date < today].copy()
    if df_all.empty or len(df_all) < train_days + test_days:
        return {"symbol": symbol, "error": f"Insufficient price history ({len(df_all)} bars)"}

    horizon = _HORIZON_BY_STYLE.get(style.upper(), 10)

    # Build macro + earnings + sector features once (same data used across all windows)
    start_d = pd.to_datetime(df_all["ts"]).min().date()
    try:
        macro_df = fetch_macro_features(start_d, today)
    except Exception:
        macro_df = None

    wf_sector_df = fetch_sector_features(symbol, start_d, today)

    fund_data: dict = {}
    try:
        fund_data = _load_fundamentals(symbol) or {}
        fund_data.update(_load_earnings_features(symbol))
    except Exception:
        pass

    windows: list[dict] = []
    n = len(df_all)
    pos = train_days  # start of first test window

    while pos + test_days <= n - horizon:
        df_train = df_all.iloc[:pos].copy()
        df_test  = df_all.iloc[pos:pos + test_days + horizon].copy()

        # Compute label threshold on training portion only (no lookahead)
        label_threshold = compute_label_threshold(df_train.iloc[-min(252, len(df_train)):], horizon)

        try:
            X_tr, y_tr, _ = build_features(
                df_train, horizon=horizon, macro_df=macro_df,
                label_threshold=label_threshold, fund_data=fund_data,
                sector_df=wf_sector_df,
            )
            X_te, y_te, y_ret_te = build_features(
                df_test, horizon=horizon, macro_df=macro_df,
                label_threshold=label_threshold, fund_data=fund_data,
                sector_df=wf_sector_df,
            )
        except Exception:
            pos += test_days
            continue

        if len(X_tr) < 100 or len(X_te) < 5:
            pos += test_days
            continue

        scaler = StandardScaler()
        X_tr_s = scaler.fit_transform(X_tr.values)
        X_te_s = scaler.transform(X_te.values)

        _rw = _recency_weights(len(X_tr), newest_to_oldest_ratio=5.0)
        sw = _blend_weights(y_tr.values, _rw)

        try:
            m = get_model(model_name)
            m.fit(X_tr_s, y_tr.values, sample_weight=sw)
            probs = m.predict_proba(X_te_s)
            raw = probs[:, 1] if probs.ndim == 2 else probs
        except Exception:
            pos += test_days
            continue

        # Use a fixed 0.5 threshold for OOS eval (no threshold optimisation on OOS data)
        preds = (raw > 0.5).astype(int)
        n_buy = int(preds.sum())
        oos_prec = float(precision_score(y_te, preds, zero_division=0)) if n_buy else None
        oos_auc = float(roc_auc_score(y_te, raw)) if len(np.unique(y_te)) > 1 else None
        avg_ret  = float(np.mean(y_ret_te.values[preds == 1])) if n_buy > 0 else None

        # IC: Spearman rank correlation of predicted probability vs actual return
        ic_val = None
        if len(y_ret_te) >= 5:
            ic, _ = spearmanr(raw, y_ret_te.values)
            ic_val = round(float(ic), 4) if not np.isnan(ic) else None

        train_end = str(pd.to_datetime(df_train.iloc[-1]["ts"]).date())
        test_end  = str(pd.to_datetime(df_test.iloc[min(test_days - 1, len(df_test) - 1)]["ts"]).date())

        windows.append({
            "train_end":   train_end,
            "test_end":    test_end,
            "n_train":     len(X_tr),
            "n_test":      len(X_te),
            "n_buy_signals": n_buy,
            "oos_precision": round(oos_prec, 3) if oos_prec is not None else None,
            "oos_auc":     round(oos_auc, 4) if oos_auc is not None else None,
            "avg_return_pct": round(avg_ret * 100, 2) if avg_ret is not None else None,
            "ic":          ic_val,
        })
        pos += test_days

    if not windows:
        return {"symbol": symbol, "error": "No valid WF windows produced"}

    precs = [w["oos_precision"] for w in windows if w["oos_precision"] is not None]
    aucs  = [w["oos_auc"] for w in windows if w["oos_auc"] is not None]
    rets  = [w["avg_return_pct"] for w in windows if w["avg_return_pct"] is not None]
    ics   = [w["ic"] for w in windows if w["ic"] is not None]

    return {
        "symbol": symbol,
        "style": style,
        "model": model_name,
        "n_windows": len(windows),
        "train_days": train_days,
        "test_days": test_days,
        "summary": {
            "mean_oos_precision": round(float(np.mean(precs)), 3) if precs else None,
            "mean_oos_auc": round(float(np.mean(aucs)), 4) if aucs else None,
            "mean_avg_return_pct": round(float(np.mean(rets)), 2) if rets else None,
            "mean_ic": round(float(np.mean(ics)), 4) if ics else None,
            "precision_stability": round(float(np.std(precs)), 3) if len(precs) > 1 else None,
        },
        "windows": windows,
    }
