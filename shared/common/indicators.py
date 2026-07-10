"""Canonical technical indicators — shared across services.

T233-ARCH-INDICATOR-DEDUP (pilot): RSI/MACD/ATR/SMA/EMA were independently reimplemented in
6+ places across the codebase (ranking-engine, ml-prediction, market-data x2, research-engine,
signal-engine), with real formula drift between them — e.g. research-engine's standalone RSI
used a simple rolling mean for gain/loss instead of Wilder's smoothing, producing a mean
absolute difference of ~7.4 RSI points (max ~26 points) against the canonical formula on real
data. This module is the single source of truth going forward, ported verbatim from
services/technical-analysis/src/indicators/core.py (the service explicitly built to be
canonical, per T233-ARCH-INDICATOR-DEDUP's original finding).

Piloted on research-engine first (not on the trading hot path) per the tracker's recommended
approach — validate parity before touching any correctness-critical trading surface (signal-engine,
ranking-engine, decision-engine). Do NOT swap those services to this module without the same
parity-validation discipline used for the research-engine pilot; any drift there changes live
trading signals, not just report display values.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def sma(close: pd.Series, window: int = 20) -> pd.Series:
    return close.rolling(window, min_periods=window).mean()


def ema(close: pd.Series, window: int = 20) -> pd.Series:
    return close.ewm(span=window, adjust=False, min_periods=window).mean()


def rsi(close: pd.Series, window: int = 14) -> pd.Series:
    """Wilder's RSI."""
    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    avg_loss = loss.ewm(alpha=1 / window, adjust=False, min_periods=window).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi_val = 100 - (100 / (1 + rs))
    # `avg_loss.replace(0, np.nan)` makes `rs`/`rsi_val` NaN in two distinct cases — the genuine
    # avg_loss==0 case (Wilder's spec: RSI=100) AND the warmup window before min_periods bars
    # exist. Only fill where avg_loss is a real, computed zero — leave true warmup NaN as NaN.
    return rsi_val.mask(avg_loss.notna() & avg_loss.eq(0), 100.0)


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    ema_fast = close.ewm(span=fast, adjust=False, min_periods=fast).mean()
    ema_slow = close.ewm(span=slow, adjust=False, min_periods=slow).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def bollinger_bands(close: pd.Series, window: int = 20, n_std: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(window, min_periods=window).mean()
    std = close.rolling(window, min_periods=window).std(ddof=1)
    return pd.DataFrame(
        {"bb_mid": mid, "bb_upper": mid + n_std * std, "bb_lower": mid - n_std * std}
    )


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's ATR."""
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
