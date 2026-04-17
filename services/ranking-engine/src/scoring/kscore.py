"""K-Score: 0-100 composite of Technical / Momentum / Value / Growth / Volatility.

Each sub-score is derived from price history where possible. Value + Growth are
proxies until we wire fundamentals — plug replacements in by swapping the
functions below, no schema change needed.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class KScoreComponents:
    technical: float
    momentum: float
    value: float
    growth: float
    volatility: float
    score: float
    fair_price: float | None = None


_WEIGHTS = {
    "technical": 0.25,
    "momentum": 0.25,
    "value": 0.15,
    "growth": 0.15,
    "volatility": 0.20,
}


def _rsi(close: pd.Series, w: int = 14) -> pd.Series:
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / w, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / w, adjust=False).mean()
    rs = g / l.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def _technical_score(df: pd.DataFrame) -> float:
    close = df["close"]
    sma50 = close.rolling(50).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1]
    above_50 = 1 if close.iloc[-1] > sma50 else 0
    above_200 = 1 if close.iloc[-1] > sma200 else 0
    golden = 1 if sma50 > sma200 else 0
    r = _rsi(close).iloc[-1]
    rsi_score = 100 - abs(r - 55)  # peak at 55 (bullish but not overbought)
    return np.clip((above_50 + above_200 + golden) / 3 * 60 + rsi_score * 0.4, 0, 100)


def _momentum_score(df: pd.DataFrame) -> float:
    c = df["close"]
    if len(c) < 126:
        return 50.0
    r1m = c.iloc[-1] / c.iloc[-21] - 1
    r3m = c.iloc[-1] / c.iloc[-63] - 1
    r6m = c.iloc[-1] / c.iloc[-126] - 1
    raw = 0.5 * r3m + 0.3 * r6m + 0.2 * r1m
    return float(np.clip(50 + raw * 150, 0, 100))


def _volatility_score(df: pd.DataFrame) -> float:
    """Lower realized vol → higher score."""
    ret = df["close"].pct_change()
    vol = ret.rolling(60).std().iloc[-1]
    if pd.isna(vol):
        return 50.0
    return float(np.clip(100 - vol * 1500, 0, 100))


def _value_proxy(df: pd.DataFrame) -> float:
    """Proxy: distance below 52w high. Deep discount → higher value."""
    high_52 = df["close"].tail(252).max()
    discount = 1 - df["close"].iloc[-1] / high_52
    return float(np.clip(discount * 200, 0, 100))


def _growth_proxy(df: pd.DataFrame) -> float:
    """Proxy: 12-month CAGR."""
    if len(df) < 252:
        return 50.0
    cagr = df["close"].iloc[-1] / df["close"].iloc[-252] - 1
    return float(np.clip(50 + cagr * 120, 0, 100))


def compute_kscore(df: pd.DataFrame) -> KScoreComponents:
    tech = _technical_score(df)
    mom = _momentum_score(df)
    val = _value_proxy(df)
    gro = _growth_proxy(df)
    vol = _volatility_score(df)

    score = (
        _WEIGHTS["technical"] * tech
        + _WEIGHTS["momentum"] * mom
        + _WEIGHTS["value"] * val
        + _WEIGHTS["growth"] * gro
        + _WEIGHTS["volatility"] * vol
    )
    sma200 = df["close"].rolling(200).mean().iloc[-1]
    fair = float(sma200) if not pd.isna(sma200) else None

    return KScoreComponents(
        technical=round(tech, 2),
        momentum=round(mom, 2),
        value=round(val, 2),
        growth=round(gro, 2),
        volatility=round(vol, 2),
        score=round(score, 2),
        fair_price=round(fair, 2) if fair else None,
    )
