"""Signal generator — fuses TA indicators, ML probability, volume into a
BUY/SELL/HOLD call with an AI Confidence Score (0-100).

Inputs come from other microservices over HTTP. Each source is optional —
if the ML service is unreachable we fall back to TA-only signals.
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
import numpy as np
import pandas as pd

from common.config import get_settings
from common.logging import get_logger

log = get_logger("signal-generator")
_settings = get_settings()


@dataclass
class AIConfidence:
    signal: str           # BUY / SELL / HOLD
    horizon: str          # SHORT / SWING / LONG
    confidence: float     # 0-100
    bullish_probability: float  # 0-1
    reasons: dict


def _fetch_prices(symbol: str) -> pd.DataFrame:
    url = f"{_settings.market_data_url}/stocks/{symbol}/prices?timeframe=1d&limit=400"
    with httpx.Client(timeout=15) as c:
        r = c.get(url)
        r.raise_for_status()
    data = r.json()
    return pd.DataFrame(data)


def _fetch_ml_probability(symbol: str) -> float | None:
    try:
        with httpx.Client(timeout=10) as c:
            r = c.post(
                f"{_settings.ml_prediction_url}/ml/predict",
                json={"symbol": symbol, "model": "xgboost"},
            )
            if r.status_code == 200:
                return float(r.json().get("bullish_probability", 0.5))
    except Exception as exc:
        log.warning("ml.fetch_failed", symbol=symbol, error=str(exc))
    return None


def _ta_score(df: pd.DataFrame) -> tuple[float, dict]:
    close = df["close"].astype(float)
    reasons = {}

    sma50 = close.rolling(50).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1]
    above_50 = close.iloc[-1] > sma50
    golden = sma50 > sma200
    reasons["trend_above_sma50"] = bool(above_50)
    reasons["golden_cross"] = bool(golden)

    # RSI
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rs = g / l.replace(0, np.nan)
    rsi = 100 - 100 / (1 + rs)
    reasons["rsi"] = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else None

    # MACD histogram sign
    macd = close.ewm(span=12).mean() - close.ewm(span=26).mean()
    sig = macd.ewm(span=9).mean()
    hist = macd - sig
    reasons["macd_hist"] = float(hist.iloc[-1])

    # Volume expansion
    vol = df["volume"].astype(float)
    vol_z = (vol.iloc[-1] - vol.rolling(20).mean().iloc[-1]) / vol.rolling(20).std().iloc[-1]
    reasons["volume_z"] = float(vol_z) if not pd.isna(vol_z) else None

    score = 0.0
    if above_50:
        score += 0.25
    if golden:
        score += 0.20
    if reasons["rsi"] is not None and 40 < reasons["rsi"] < 70:
        score += 0.20
    if reasons["macd_hist"] and reasons["macd_hist"] > 0:
        score += 0.20
    if reasons["volume_z"] and reasons["volume_z"] > 0.5:
        score += 0.15
    return float(np.clip(score, 0, 1)), reasons


def _decide(fused_prob: float) -> tuple[str, str]:
    if fused_prob > 0.65:
        return "BUY", "SWING"
    if fused_prob < 0.35:
        return "SELL", "SWING"
    return "HOLD", "SWING"


def generate_signal(symbol: str) -> AIConfidence:
    df = _fetch_prices(symbol)
    if df.empty:
        raise ValueError(f"No price data for {symbol}")

    ta_prob, reasons = _ta_score(df)
    ml_prob = _fetch_ml_probability(symbol)

    # Fuse: 60% ML if available, else 100% TA
    if ml_prob is not None:
        fused = 0.6 * ml_prob + 0.4 * ta_prob
        reasons["ml_probability"] = ml_prob
    else:
        fused = ta_prob
        reasons["ml_probability"] = None

    reasons["ta_score"] = ta_prob
    signal, horizon = _decide(fused)
    confidence = round(abs(fused - 0.5) * 200, 2)

    return AIConfidence(
        signal=signal,
        horizon=horizon,
        confidence=confidence,
        bullish_probability=round(fused, 4),
        reasons=reasons,
    )
