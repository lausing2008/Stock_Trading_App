"""Trainer — walks the DB for price history, builds features, fits & persists."""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, roc_auc_score
from sklearn.preprocessing import StandardScaler
from sqlalchemy import select

from common.config import get_settings
from common.logging import get_logger
from db import Price, SessionLocal, Stock, TimeFrame

from ..features import build_features
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


def train_model(symbol: str, model_name: str = "xgboost", horizon: int = 5) -> dict:
    df = _load_prices(symbol)
    X, y_dir, _ = build_features(df, horizon=horizon)
    if len(X) < 200:
        raise ValueError(f"Not enough samples for {symbol}: {len(X)}")

    split = int(len(X) * 0.8)
    X_train, X_test = X.iloc[:split], X.iloc[split:]
    y_train, y_test = y_dir.iloc[:split], y_dir.iloc[split:]

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train.values)
    X_test_s = scaler.transform(X_test.values)

    model = get_model(model_name)
    model.fit(X_train_s, y_train.values)

    preds = model.predict_proba(X_test_s)
    if model_name == "lstm":
        preds = preds[-len(y_test) :]  # window alignment
    y_pred = (preds > 0.5).astype(int)

    metrics = {
        "accuracy": float(accuracy_score(y_test, y_pred)),
        "auc": float(roc_auc_score(y_test, preds)) if len(np.unique(y_test)) > 1 else None,
        "n_train": int(len(X_train)),
        "n_test": int(len(X_test)),
    }

    path = _artifact_path(symbol, model_name)
    path.parent.mkdir(parents=True, exist_ok=True)
    import joblib
    joblib.dump({"model": model, "scaler": scaler, "metrics": metrics}, path)

    log.info("train.done", symbol=symbol, model=model_name, **metrics)
    return {"symbol": symbol, "model": model_name, "path": str(path), "metrics": metrics}


def load_trained(symbol: str, model_name: str) -> tuple[BaseModel, StandardScaler, dict]:
    import joblib
    path = _artifact_path(symbol, model_name)
    if not path.exists():
        raise FileNotFoundError(f"No trained model at {path}")
    bundle = joblib.load(path)
    return bundle["model"], bundle["scaler"], bundle["metrics"]


def predict_latest(symbol: str, model_name: str = "xgboost", horizon: int = 5) -> dict:
    model, scaler, _ = load_trained(symbol, model_name)
    df = _load_prices(symbol, lookback_days=400)
    X, _, _ = build_features(df, horizon=horizon)
    if X.empty:
        return {"symbol": symbol, "bullish_probability": 0.5, "confidence": 0}
    Xs = scaler.transform(X.values)
    prob = float(model.predict_proba(Xs)[-1])
    return {
        "symbol": symbol,
        "model": model_name,
        "bullish_probability": prob,
        "direction": "up" if prob > 0.5 else "down",
        "confidence": round(abs(prob - 0.5) * 200, 2),
        "horizon_days": horizon,
    }
