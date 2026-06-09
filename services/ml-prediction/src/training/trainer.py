"""Trainer — walks the DB for price history, builds features, fits & persists."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from scipy.stats import spearmanr
from sklearn.isotonic import IsotonicRegression
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
    """ML-FIX-2: Blend recency weights with balanced class weights.

    Combines two sources of importance:
      - Recency: recent bars reflect current regime better than old bars.
      - Class balance: minority-class samples (typically bullish) should not
        be drowned out by the majority class in imbalanced datasets.

    Element-wise product then renormalise to mean=1 so total effective sample
    size is preserved (consistent with the rest of the training pipeline).
    """
    class_w = compute_sample_weight("balanced", y)
    combined = recency_w * class_w
    return combined / combined.mean()


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

    X, y_dir, y_ret = build_features(
        df, horizon=horizon, macro_df=macro_df, label_threshold=label_threshold
    )
    if len(X) < 200:
        log.warning("train.skipped", symbol=symbol, reason=f"only {len(X)} clean samples")
        return {"symbol": symbol, "skipped": True, "reason": f"only {len(X)} clean samples"}

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
    tscv = TimeSeriesSplit(n_splits=5)
    for tr_idx, val_idx in tscv.split(X):
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

        # IC: Spearman corr between predicted probability and actual return
        ret_cv_val = y_ret.iloc[val_idx].values
        if len(ret_cv_val) >= 5:
            ic, _ = spearmanr(preds_proba, ret_cv_val)
            if not np.isnan(ic):
                oos_ics.append(float(ic))

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

    # ML-FIX-2: recency + balanced class weights blended for final training
    _recency_w = _recency_weights(len(X_train), newest_to_oldest_ratio=5.0)
    train_weights = _blend_weights(y_train.values, _recency_w)

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
    oos_suppressed = oos_acc_mean is not None and oos_acc_mean < 0.52
    if oos_suppressed:
        log.warning(
            "train.oos_suppressed",
            symbol=symbol,
            oos_acc=round(oos_acc_mean, 4),
            note="model OOS accuracy < 52%; live predictions will be held at 0.5 (neutral)",
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
        "oos_suppressed": oos_suppressed,
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

    X_aligned = X.reindex(columns=saved_cols, fill_value=0.0).fillna(0.0)
    Xs = scaler.transform(X_aligned.values)
    # Positive-class probability for the latest bar (calibrator expects 1D input).
    # XGBModel.predict_proba returns 1D (n_samples,); XGBClassifier returns 2D (n_samples, 2).
    proba = model.predict_proba(Xs)
    raw_prob = float(proba[-1, 1] if proba.ndim == 2 else proba[-1])

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


def predict_latest_ensemble_three(symbol: str, horizon: int = 5) -> dict:
    """XGBoost (40%) + LightGBM (35%) + RandomForest (25%) weighted ensemble.

    Agreement logic (SA-8):
    - Unanimous (all 3 same direction): nudge probability 0.05 toward that direction.
    - Split (2-1): compress 0.05 toward 0.5 to reflect the disagreement.
    Per-model probabilities and agreement status are stored in the response for
    inclusion in Signal.reasons.

    Falls back gracefully: if LightGBM or RF is not trained, falls back to
    predict_latest_ensemble (XGBoost + RF), then to XGBoost-only.
    """
    xgb = predict_latest(symbol, "xgboost", horizon)

    lgb_path = _artifact_path(symbol, "lightgbm")
    rf_path  = _artifact_path(symbol, "random_forest")

    lgb_res = None
    if lgb_path.exists():
        try:
            lgb_res = predict_latest(symbol, "lightgbm", horizon)
        except Exception:
            lgb_res = None

    rf_res = None
    if rf_path.exists():
        try:
            rf_res = predict_latest(symbol, "random_forest", horizon)
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

    # Use XGBoost buy_threshold as the ensemble threshold (primary model)
    buy_threshold = float((xgb.get("metrics") or {}).get("buy_threshold") or 0.5)

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
            "test_auc_mean": round(mean_auc, 4),
            "cv_auc_mean": round(mean_auc, 4),
            "buy_threshold": buy_threshold,
        },
    }
