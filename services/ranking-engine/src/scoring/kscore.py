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
    value: float | None       # None when price proxy used (no real fundamentals)
    growth: float | None      # None when price proxy used (no real fundamentals)
    volatility: float
    score: float
    fair_price: float | None = None
    relative_strength: float | None = None


_WEIGHTS = {
    "technical": 0.22,
    "momentum": 0.23,
    "value": 0.13,
    "growth": 0.14,
    "volatility": 0.18,
    "relative_strength": 0.10,
}


def _rsi(close: pd.Series, w: int = 14) -> pd.Series:
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / w, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / w, adjust=False).mean()
    rs = g / l.replace(0, np.nan)
    # When l == 0 (no down days), rs is NaN — treat as RSI=100 (all gains, no losses).
    return (100 - 100 / (1 + rs)).fillna(100)


def _adx_value(df: pd.DataFrame, period: int = 14) -> float:
    """Return ADX scalar. Returns 20.0 (neutral) if insufficient data."""
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    close = df["close"].astype(float)

    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)

    up_move   = high.diff()
    down_move = (-low.diff())
    dm_plus  = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    dm_minus = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    atr      = tr.ewm(alpha=1 / period, adjust=False).mean()
    di_plus  = 100 * dm_plus.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, np.nan)
    di_minus = 100 * dm_minus.ewm(alpha=1 / period, adjust=False).mean() / atr.replace(0, np.nan)

    dx  = 100 * (di_plus - di_minus).abs() / (di_plus + di_minus).replace(0, np.nan)
    adx = dx.ewm(alpha=1 / period, adjust=False).mean().iloc[-1]
    return float(adx) if not pd.isna(adx) else 20.0


def _technical_score(df: pd.DataFrame) -> float:
    close = df["close"]
    sma50  = close.rolling(50).mean().iloc[-1]
    sma200 = close.rolling(200).mean().iloc[-1]
    above_sma50        = 1 if close.iloc[-1] > sma50  else 0
    above_sma200       = 1 if close.iloc[-1] > sma200 else 0
    sma50_above_sma200 = 1 if sma50 > sma200           else 0

    r = _rsi(close).iloc[-1]
    # Asymmetric: optimal zone is 50-70 (bullish momentum). Oversold (<30) and
    # very overbought (>80) penalised. A trending RSI=70 scores higher than RSI=40.
    if r <= 30:
        rsi_score = 50.0
    elif r <= 50:
        rsi_score = 50.0 + (r - 30) * 2.0       # 50→90 as RSI 30→50
    elif r <= 70:
        rsi_score = 90.0 + (r - 50) * 0.5        # 90→100 as RSI 50→70
    else:
        rsi_score = 100.0 - (r - 70) * 2.5       # 100→62.5 as RSI 70→85+

    adx = _adx_value(df)
    # ADX boost: strong trend (>25) lifts score; very weak trend (<15) drags it
    adx_boost = np.clip((adx - 15) / 25, 0, 1) * 10  # 0–10 bonus

    base = (above_sma50 + above_sma200 + sma50_above_sma200) / 3 * 60 + rsi_score * 0.4
    return float(np.clip(base + adx_boost, 0, 100))


def _momentum_score(df: pd.DataFrame) -> float:
    c = df["close"]
    if len(c) < 127:
        return 50.0
    r1m = c.iloc[-1] / c.iloc[-22]  - 1
    r3m = c.iloc[-1] / c.iloc[-64]  - 1
    r6m = c.iloc[-1] / c.iloc[-127] - 1
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
    """Proxy: distance below 52w high, gated by trend direction.

    Falling-knife guard: if both 1m and 3m returns are negative the stock is
    in a downtrend — a deep discount without recovery is a risk, not value.
    Cap score at 25 in that case so it can't drag down the composite K-Score.
    """
    high_52  = df["close"].tail(252).max()
    if not high_52 or pd.isna(high_52) or high_52 <= 0:
        return 50.0
    discount = 1 - df["close"].iloc[-1] / high_52
    raw_score = float(np.clip(discount * 200, 0, 100))

    if len(df) >= 63:
        r1m = df["close"].iloc[-1] / df["close"].iloc[-21] - 1
        r3m = df["close"].iloc[-1] / df["close"].iloc[-63] - 1
        if r1m < -0.05 and r3m < -0.15:
            return min(raw_score, 25.0)

    return raw_score


def _growth_proxy(df: pd.DataFrame) -> float:
    """Proxy: 12-month CAGR."""
    if len(df) < 252:
        return 50.0
    cagr = df["close"].iloc[-1] / df["close"].iloc[-252] - 1
    return float(np.clip(50 + cagr * 120, 0, 100))


def compute_kscore(
    df: pd.DataFrame,
    rs_score: float | None = None,
    value_score: float | None = None,
    growth_score: float | None = None,
) -> KScoreComponents:
    """Compute K-Score composite.

    value_score / growth_score (0-100): when provided, these are sector-relative
    percentile ranks from real fundamental data (PE, PB, EV/EBITDA, revenue growth,
    ROE, etc.) and are returned as-is. When None, the composite score uses price
    proxies internally but value/growth are returned as None (displayed as "—") so
    the UI does not mislead traders with price data labeled as fundamental quality.
    """
    tech = _technical_score(df)
    mom  = _momentum_score(df)
    # Always compute proxy for score calculation even when real data unavailable
    val_for_score = value_score  if value_score  is not None else _value_proxy(df)
    gro_for_score = growth_score if growth_score is not None else _growth_proxy(df)
    vol  = _volatility_score(df)

    if rs_score is not None:
        score = (
            _WEIGHTS["technical"]        * tech
            + _WEIGHTS["momentum"]       * mom
            + _WEIGHTS["value"]          * val_for_score
            + _WEIGHTS["growth"]         * gro_for_score
            + _WEIGHTS["volatility"]     * vol
            + _WEIGHTS["relative_strength"] * rs_score
        )
    else:
        # No RS data: redistribute RS weight proportionally among other factors
        w_sum = 1.0 - _WEIGHTS["relative_strength"]
        score = (
            (_WEIGHTS["technical"]    / w_sum) * tech
            + (_WEIGHTS["momentum"]   / w_sum) * mom
            + (_WEIGHTS["value"]      / w_sum) * val_for_score
            + (_WEIGHTS["growth"]     / w_sum) * gro_for_score
            + (_WEIGHTS["volatility"] / w_sum) * vol
        )

    sma200 = df["close"].rolling(200).mean().iloc[-1]
    fair   = float(sma200) if not pd.isna(sma200) else None

    return KScoreComponents(
        technical=round(tech, 2),
        momentum=round(mom, 2),
        value=round(value_score, 2) if value_score is not None else None,
        growth=round(growth_score, 2) if growth_score is not None else None,
        volatility=round(vol, 2),
        score=round(score, 2),
        fair_price=round(fair, 2) if fair else None,
        relative_strength=round(rs_score, 2) if rs_score is not None else None,
    )
