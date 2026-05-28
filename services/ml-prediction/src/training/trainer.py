"""Trainer — walks the DB for price history, builds features, fits & persists."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, roc_auc_score, precision_score, recall_score, f1_score
from sklearn.model_selection import TimeSeriesSplit
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select

from common.config import get_settings
from common.logging import get_logger
from db import Price, SessionLocal, Stock, TimeFrame

from ..features import build_features, FEATURE_COLUMNS
from ..models import BaseModel, get_model

log = get_logger("trainer")
_settings = get_settings()


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


def train_model(
    symbol: str,
    model_name: str = "xgboost",
    horizon: int = 5,
    hyperparams: dict | None = None,
) -> dict:
    try:
        df = _load_prices(symbol)
    except ValueError as exc:
        log.warning("train.skipped", symbol=symbol, reason=str(exc))
        return {"symbol": symbol, "skipped": True, "reason": str(exc)}

    X, y_dir, _ = build_features(df, horizon=horizon)
    if len(X) < 200:
        log.warning("train.skipped", symbol=symbol, reason=f"only {len(X)} samples")
        return {"symbol": symbol, "skipped": True, "reason": f"only {len(X)} samples"}

    # --- Hyperparams: passed > saved tuned > defaults ---
    if hyperparams is None and model_name == "xgboost":
        hyperparams = _load_best_params(symbol)

    # --- Walk-forward CV metrics (5-fold, no data leak) ---
    cv_aucs: list[float] = []
    cv_accs: list[float] = []
    tscv = TimeSeriesSplit(n_splits=5)
    for tr_idx, val_idx in tscv.split(X):
        X_cv_tr, X_cv_val = X.iloc[tr_idx].values, X.iloc[val_idx].values
        y_cv_tr, y_cv_val = y_dir.iloc[tr_idx].values, y_dir.iloc[val_idx].values
        sc = StandardScaler()
        X_cv_tr_s = sc.fit_transform(X_cv_tr)
        X_cv_val_s = sc.transform(X_cv_val)
        cv_model = get_model(model_name, **(hyperparams or {}))
        cv_model.fit(X_cv_tr_s, y_cv_tr)
        preds = cv_model.predict_proba(X_cv_val_s)
        if len(np.unique(y_cv_val)) > 1:
            cv_aucs.append(roc_auc_score(y_cv_val, preds))
        cv_accs.append(accuracy_score(y_cv_val, (preds > 0.5).astype(int)))

    # --- Final holdout split (last 20%) ---
    split = int(len(X) * 0.8)
    X_train, X_test = X.iloc[:split], X.iloc[split:]
    y_train, y_test = y_dir.iloc[:split], y_dir.iloc[split:]

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train.values)
    X_test_s = scaler.transform(X_test.values)

    # XGBoost early stopping on holdout eval set
    model = get_model(model_name, early_stopping_rounds=50, **(hyperparams or {}))
    if model_name == "xgboost":
        model.fit(
            X_train_s, y_train.values,
            eval_set=[(X_test_s, y_test.values)],
            verbose=False,
        )
    else:
        model.fit(X_train_s, y_train.values)

    preds = model.predict_proba(X_test_s)
    if model_name == "lstm":
        preds = preds[-len(y_test):]
    y_pred = (preds > 0.5).astype(int)

    # --- Feature importance (XGBoost only) ---
    feature_importance: dict[str, float] = {}
    if model_name == "xgboost" and hasattr(model.clf, "feature_importances_"):
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
        "cv_auc_mean": float(np.mean(cv_aucs)) if cv_aucs else None,
        "cv_auc_std": float(np.std(cv_aucs)) if cv_aucs else None,
        "cv_acc_mean": float(np.mean(cv_accs)) if cv_accs else None,
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
        "n_features": len(FEATURE_COLUMNS),
    }

    path = _artifact_path(symbol, model_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    import joblib
    joblib.dump({
        "model": model,
        "scaler": scaler,
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

    df = _load_prices(symbol, lookback_days=400)
    X, _, _ = build_features(df, horizon=horizon)
    if X.empty:
        return {"symbol": symbol, "bullish_probability": 0.5, "confidence": 0}

    # Use feature columns from artifact to handle version mismatches
    saved_cols = bundle.get("feature_columns", list(X.columns))
    X_aligned = X.reindex(columns=saved_cols, fill_value=0.0)

    Xs = scaler.transform(X_aligned.values)
    prob = float(model.predict_proba(Xs)[-1])
    return {
        "symbol": symbol,
        "model": model_name,
        "bullish_probability": prob,
        "direction": "up" if prob > 0.5 else "down",
        "confidence": round(abs(prob - 0.5) * 200, 2),
        "horizon_days": horizon,
        "metrics": bundle.get("metrics", {}),
    }
