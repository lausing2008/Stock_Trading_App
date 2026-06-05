"""Trainer — walks the DB for price history, builds features, fits & persists."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.isotonic import IsotonicRegression
from sklearn.metrics import (
    accuracy_score, f1_score, precision_recall_curve,
    precision_score, recall_score, roc_auc_score,
)
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select

from common.config import get_settings
from common.logging import get_logger
from db import Price, SessionLocal, Stock, TimeFrame

from ..features import build_features, compute_label_threshold, fetch_macro_features, FEATURE_COLUMNS
from ..models import BaseModel, get_model

log = get_logger("trainer")
_settings = get_settings()

_MIN_PRECISION = 0.60  # fallback precision floor (SWING)

# SHORT trades have little time to recover from false entries — require tighter precision.
# LONG trades can absorb more noise over a 90-day hold — accept a lower floor.
_PRECISION_BY_STYLE: dict[str, float] = {
    "SHORT": 0.70,
    "SWING": 0.60,
    "LONG":  0.50,
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


def _artifact_path(symbol: str, model_name: str) -> Path:
    return Path(_settings.model_dir) / model_name / f"{symbol}.joblib"


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
) -> float:
    """Find the lowest threshold where precision >= min_precision and recall >= 5%.

    For trading we care about precision (when we say BUY, we're right) more than
    recall (we don't need to catch every winner).  Falls back to 0.5 if no
    threshold achieves the precision target on the test set.
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
    return float(min(valid)) if valid else 0.5


def _recency_weights(n: int, newest_to_oldest_ratio: float = 3.0) -> np.ndarray:
    """Exponential weights so most-recent bar has ~ratio× the weight of oldest.

    Normalised so the mean weight equals 1 (total weight ≈ n, consistent with
    an unweighted dataset of the same size).
    """
    w = np.exp(np.log(newest_to_oldest_ratio) * np.arange(n) / max(n - 1, 1))
    return w / w.mean()


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

    # Per-symbol volatility-adjusted dead zone (0.5 × expected N-day move)
    label_threshold = compute_label_threshold(df, horizon)

    X, y_dir, _ = build_features(
        df, horizon=horizon, macro_df=macro_df, label_threshold=label_threshold
    )
    if len(X) < 200:
        log.warning("train.skipped", symbol=symbol, reason=f"only {len(X)} clean samples")
        return {"symbol": symbol, "skipped": True, "reason": f"only {len(X)} clean samples"}

    # --- Hyperparams: passed > saved tuned > defaults ---
    if hyperparams is None and model_name == "xgboost":
        hyperparams = _load_best_params(symbol)

    # --- Walk-forward CV metrics (5-fold, no data leakage) ---
    cv_aucs: list[float] = []
    cv_accs: list[float] = []
    tscv = TimeSeriesSplit(n_splits=5)
    for tr_idx, val_idx in tscv.split(X):
        X_cv_tr, X_cv_val = X.iloc[tr_idx].values, X.iloc[val_idx].values
        y_cv_tr, y_cv_val = y_dir.iloc[tr_idx].values, y_dir.iloc[val_idx].values
        sc = StandardScaler()
        X_cv_tr_s = sc.fit_transform(X_cv_tr)
        X_cv_val_s = sc.transform(X_cv_val)

        # Recency-weighted training: recent bars matter more
        cv_weights = _recency_weights(len(tr_idx))
        cv_model = get_model(model_name, **(hyperparams or {}))
        cv_model.fit(X_cv_tr_s, y_cv_tr, sample_weight=cv_weights)

        preds_proba = cv_model.predict_proba(X_cv_val_s)[:, 1]  # positive-class only, shape (n,)
        if len(np.unique(y_cv_val)) > 1:
            cv_aucs.append(roc_auc_score(y_cv_val, preds_proba))
        cv_accs.append(accuracy_score(y_cv_val, (preds_proba > 0.5).astype(int)))

    # --- Three-way split: train / calibration / threshold evaluation ---
    # Separating calibration and threshold sets prevents double-dipping:
    # the calibrator is fit on X_cal (unseen by threshold search), and the
    # buy_threshold is optimised on X_test (unseen by calibrator).
    split_train = int(len(X) * 0.70)
    split_cal   = int(len(X) * 0.85)
    X_train = X.iloc[:split_train]
    X_cal   = X.iloc[split_train:split_cal]
    X_test  = X.iloc[split_cal:]
    y_train = y_dir.iloc[:split_train]
    y_cal   = y_dir.iloc[split_train:split_cal]
    y_test  = y_dir.iloc[split_cal:]

    if len(np.unique(y_train)) < 2:
        log.warning("train.skipped", symbol=symbol, reason="degenerate labels — all same class after dead-zone filter")
        return {"symbol": symbol, "skipped": True, "reason": "degenerate labels after dead-zone filter"}

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train.values)
    X_cal_s   = scaler.transform(X_cal.values)
    X_test_s  = scaler.transform(X_test.values)

    # Recency weights for final training
    train_weights = _recency_weights(len(X_train))

    # XGBoost early stopping on calibration set (separate from threshold eval set)
    model = get_model(model_name, early_stopping_rounds=50, **(hyperparams or {}))
    if model_name == "xgboost":
        model.fit(
            X_train_s, y_train.values,
            sample_weight=train_weights,
            eval_set=[(X_cal_s, y_cal.values)],
            verbose=False,
        )
    else:
        model.fit(X_train_s, y_train.values, sample_weight=train_weights)

    # --- Probability calibration (isotonic regression on calibration set) ---
    # Use positive-class probabilities only (shape (n,)) — IsotonicRegression expects 1D input.
    raw_cal_probs = model.predict_proba(X_cal_s)[:, 1]
    calibrator: IsotonicRegression | None = None
    if len(np.unique(y_cal)) > 1 and len(y_cal) >= 20:
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(raw_cal_probs, y_cal.values)

    # --- Precision-optimised BUY threshold (on held-out test set) ---
    raw_test_probs = model.predict_proba(X_test_s)[:, 1]  # shape (n,)
    preds = calibrator.predict(raw_test_probs) if calibrator is not None else raw_test_probs
    min_prec = _PRECISION_BY_STYLE.get(style.upper(), _MIN_PRECISION)
    buy_threshold = _precision_threshold(y_test.values, preds, min_precision=min_prec)

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

    metrics = {
        "accuracy": float(accuracy_score(y_test, y_pred)),
        "auc": float(roc_auc_score(y_test, preds)) if len(np.unique(y_test)) > 1 else None,
        "precision": float(precision_score(y_test, y_pred, zero_division=0)),
        "recall": float(recall_score(y_test, y_pred, zero_division=0)),
        "f1": float(f1_score(y_test, y_pred, zero_division=0)),
        "buy_threshold": float(buy_threshold),
        "cv_auc_mean": float(np.mean(cv_aucs)) if cv_aucs else None,
        "cv_auc_std": float(np.std(cv_aucs)) if cv_aucs else None,
        "cv_acc_mean": float(np.mean(cv_accs)) if cv_accs else None,
        "n_train": int(len(X_train)),
        "n_cal": int(len(X_cal)),
        "n_test": int(len(X_test)),
        "n_features": len(FEATURE_COLUMNS),
        "label_threshold": label_threshold,
    }

    path = _artifact_path(symbol, model_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    import joblib
    joblib.dump({
        "model": model,
        "scaler": scaler,
        "calibrator": calibrator,
        "buy_threshold": buy_threshold,
        "label_threshold": label_threshold,
        "metrics": metrics,
        "feature_columns": list(FEATURE_COLUMNS),
        "feature_importance": feature_importance,
    }, path)

    log.info("train.done", symbol=symbol, model=model_name, **{k: v for k, v in metrics.items() if v is not None})
    return {"symbol": symbol, "model": model_name, "path": str(path), "metrics": metrics}


def load_trained(symbol: str, model_name: str) -> tuple[BaseModel, StandardScaler, dict]:
    import joblib
    path = _artifact_path(symbol, model_name)
    if not path.exists():
        raise FileNotFoundError(f"No trained model at {path}")
    bundle = joblib.load(path)
    return bundle["model"], bundle["scaler"], bundle["metrics"]


def predict_latest(symbol: str, model_name: str = "xgboost", horizon: int = 5) -> dict:
    import joblib
    path = _artifact_path(symbol, model_name)
    if not path.exists():
        raise FileNotFoundError(f"No trained model at {path}")
    bundle = joblib.load(path)
    model = bundle["model"]
    scaler = bundle["scaler"]
    calibrator = bundle.get("calibrator")
    buy_threshold = bundle.get("buy_threshold", 0.5)
    saved_cols = bundle.get("feature_columns", list(FEATURE_COLUMNS))

    df = _load_prices(symbol, lookback_days=400)

    # Fetch macro features aligned to the stock's price dates
    macro_df = None
    try:
        start_date = pd.to_datetime(df["ts"]).min().date()
        macro_df = fetch_macro_features(start_date, date.today() + timedelta(days=1))
    except Exception:
        pass

    # inference_mode=True: keeps the latest bar even without a known future return
    X, _, _ = build_features(
        df, horizon=horizon, macro_df=macro_df,
        label_threshold=0.0, inference_mode=True,
    )
    if X.empty:
        return {"symbol": symbol, "bullish_probability": 0.5, "confidence": 0}

    X_aligned = X.reindex(columns=saved_cols, fill_value=0.0).fillna(0.0)
    Xs = scaler.transform(X_aligned.values)
    # Positive-class probability for the latest bar (calibrator expects 1D input).
    raw_prob = float(model.predict_proba(Xs)[-1, 1])

    # Apply calibration if the model was trained with it
    prob = float(calibrator.predict([raw_prob])[0]) if calibrator is not None else raw_prob

    return {
        "symbol": symbol,
        "model": model_name,
        "bullish_probability": prob,
        "direction": "up" if prob > buy_threshold else "down",
        "confidence": round(abs(prob - 0.5) * 200, 2),
        "horizon_days": horizon,
        "metrics": bundle.get("metrics", {}),
    }


def predict_latest_ensemble(symbol: str, horizon: int = 5) -> dict:
    """Average XGBoost + RandomForest predictions, weighted by each model's CV AUC.

    Falls back to XGBoost-only if the RF model has not been trained for this symbol.
    Using two structurally different models (gradient boosting vs. bagging) reduces
    variance and smooths out individual model over-reactions to recent noise.
    """
    xgb = predict_latest(symbol, "xgboost", horizon)

    rf_path = _artifact_path(symbol, "random_forest")
    if not rf_path.exists():
        return {**xgb, "ensemble": False, "model": "xgboost"}

    try:
        rf = predict_latest(symbol, "random_forest", horizon)
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
            "test_auc_mean": round((xgb_auc + rf_auc) / 2, 4),
            "cv_auc_mean": round(
                (((xgb.get("metrics") or {}).get("cv_auc_mean") or xgb_auc) +
                 ((rf.get("metrics") or {}).get("cv_auc_mean") or rf_auc)) / 2, 4
            ),
            "buy_threshold": buy_threshold,
        },
    }
