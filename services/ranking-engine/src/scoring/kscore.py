"""K-Score: 0-100 composite of Technical / Momentum / Value / Growth / Volatility.

Value and Growth are real sector-relative fundamental percentiles when available
(passed in as value_score/growth_score). When a stock lacks fundamentals data,
those factors are excluded from the weighted composite entirely (T234-RANK-KSCORE-
PROXY-MIXING) rather than backfilled with a price-derived proxy — the composite
score only ever reflects factors it has real data for.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from common.indicators import rsi as _canon_rsi


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
    """T233-ARCH-INDICATOR-DEDUP: now delegates to the canonical Wilder's RSI in
    shared/common/indicators.py instead of a standalone reimplementation.

    T233-KSCORE-RSI1: the old version had no min_periods on its .ewm() calls, so it produced
    numerically real-looking RSI values from bar 0 onward — a stock with only 5 bars of real
    history (a recent IPO/watchlist addition) could already show RSI=96, well before the 14-bar
    window has enough data to mean anything. `.fillna(100)` then conflated that warmup case
    with the genuinely-different "no down days at all" case (both real RSI=100 situations,
    but for entirely different reasons) — same bug class as T232-TA1, already fixed in the
    canonical rsi(), just not previously ported here. The canonical version correctly returns
    NaN during warmup; see _technical_score() below for the explicit NaN handling this requires.
    """
    return _canon_rsi(close, window=w)


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
    _s50_ok  = not pd.isna(sma50)
    _s200_ok = not pd.isna(sma200)
    # Use 0.5 (neutral) when SMA is NaN — stocks with < 50/200 bars of history
    # (IPOs, new additions) otherwise score 0/1 for each missing component,
    # systematically underranking them relative to stocks with full history.
    above_sma50        = (1 if close.iloc[-1] > sma50  else 0) if _s50_ok               else 0.5
    above_sma200       = (1 if close.iloc[-1] > sma200 else 0) if _s200_ok              else 0.5
    sma50_above_sma200 = (1 if sma50 > sma200           else 0) if (_s50_ok and _s200_ok) else 0.5

    r = _rsi(close).iloc[-1]
    # T233-KSCORE-RSI1: canonical rsi() correctly returns NaN during the 14-bar warmup window
    # (a stock with <14 bars of real history) instead of a fabricated real-looking value.
    # Use 75.0 — the midpoint of this function's own output range (50-100, see below) — as the
    # neutral fallback, matching the same intent as the SMA neutral-fallback above: don't treat
    # "we don't have enough data yet" as either bullish or bearish.
    if pd.isna(r):
        rsi_score = 75.0
    # Asymmetric: optimal zone is 50-70 (bullish momentum). Oversold (<30) and
    # very overbought (>80) penalised. A trending RSI=70 scores higher than RSI=40.
    elif r <= 30:
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
    vol  = _volatility_score(df)

    # T234-RANK-KSCORE-PROXY-MIXING: value_score/growth_score used to silently fall back to
    # _value_proxy(df)/_growth_proxy(df) (both monotonic transforms of trailing price return,
    # like _momentum_score) and feed the proxy into the weighted composite as if it were a
    # real fundamental percentile — while KScoreComponents.value/.growth correctly returned
    # None. Two stocks could show an IDENTICAL K-Score while one was fundamentals-grounded and
    # the other a pure momentum artifact wearing a value/growth label internally, and when
    # fundamentals are missing (common for smaller/newer names), close to half the composite
    # became the same underlying signal (recent price action) counted three times under three
    # factor names. Fixed by excluding value/growth from the weighted sum entirely when the
    # real fundamental is unavailable, redistributing their weight to the remaining factors —
    # the same pattern already used just below for a missing rs_score.
    _active_weights = dict(_WEIGHTS)
    if value_score is None:
        del _active_weights["value"]
    if growth_score is None:
        del _active_weights["growth"]
    if rs_score is None:
        del _active_weights["relative_strength"]

    w_sum = sum(_active_weights.values())
    _factor_values = {
        "technical": tech, "momentum": mom, "volatility": vol,
        "value": value_score, "growth": growth_score, "relative_strength": rs_score,
    }
    score = sum(
        (weight / w_sum) * _factor_values[factor]
        for factor, weight in _active_weights.items()
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
