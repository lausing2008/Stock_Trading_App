"""Vectorized indicators — pure pandas/numpy, no TA-Lib dependency."""
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
    return rsi_val.fillna(100)  # avg_loss=0 → RSI=100 per Wilder's spec


def macd(
    close: pd.Series,
    fast: int = 12,
    slow: int = 26,
    signal: int = 9,
) -> pd.DataFrame:
    ema_fast = close.ewm(span=fast, adjust=False).mean()
    ema_slow = close.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    signal_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def bollinger_bands(close: pd.Series, window: int = 20, n_std: float = 2.0) -> pd.DataFrame:
    mid = close.rolling(window, min_periods=window).mean()
    std = close.rolling(window, min_periods=window).std(ddof=0)
    return pd.DataFrame(
        {"bb_mid": mid, "bb_upper": mid + n_std * std, "bb_lower": mid - n_std * std}
    )


def vwap(high: pd.Series, low: pd.Series, close: pd.Series, volume: pd.Series) -> pd.Series:
    typical = (high + low + close) / 3
    cum_vol = volume.cumsum().replace(0, np.nan)
    return (typical * volume).cumsum() / cum_vol


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's ATR (same formula as in signal-engine's _adx helper)."""
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / period, adjust=False).mean()


def supertrend(
    df: pd.DataFrame,
    period: int = 10,
    multiplier: float = 3.0,
) -> pd.DataFrame:
    """Supertrend indicator — trend-following overlay using ATR bands.

    Returns a DataFrame with columns:
      supertrend   : the line value (lower band when bullish, upper when bearish)
      trend        : +1 (bullish, price above supertrend) or -1 (bearish, below)
      cross_up     : True on the bar where trend flips from -1 → +1
      cross_down   : True on the bar where trend flips from +1 → -1

    period=10, multiplier=3.0 are the standard default settings used by most
    charting platforms (TradingView default).
    """
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    close = df["close"].astype(float)
    n = len(close)

    atr_s = atr(high, low, close, period)
    hl2   = (high + low) / 2
    basic_upper = (hl2 + multiplier * atr_s).values
    basic_lower = (hl2 - multiplier * atr_s).values
    close_v = close.values

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    trend  = np.ones(n, dtype=float)

    for i in range(1, n):
        if np.isnan(basic_upper[i]) or np.isnan(basic_lower[i]):
            trend[i] = trend[i - 1]
            continue
        final_upper[i] = basic_upper[i] if (basic_upper[i] < final_upper[i - 1] or close_v[i - 1] > final_upper[i - 1]) else final_upper[i - 1]
        final_lower[i] = basic_lower[i] if (basic_lower[i] > final_lower[i - 1] or close_v[i - 1] < final_lower[i - 1]) else final_lower[i - 1]
        if trend[i - 1] == -1:
            trend[i] = 1.0 if close_v[i] > final_upper[i] else -1.0
        else:
            trend[i] = -1.0 if close_v[i] < final_lower[i] else 1.0

    st_line = np.where(trend == 1, final_lower, final_upper)
    trend_s = pd.Series(trend, index=close.index)
    return pd.DataFrame({
        "supertrend": pd.Series(st_line, index=close.index),
        "trend":      trend_s,
        "cross_up":   (trend_s == 1) & (trend_s.shift(1) == -1),
        "cross_down": (trend_s == -1) & (trend_s.shift(1) == 1),
    })


def fibonacci_retracement(high: float, low: float) -> dict[str, float]:
    """Standard Fib levels between a swing high and swing low."""
    diff = high - low
    return {
        "0.0": high,
        "0.236": high - 0.236 * diff,
        "0.382": high - 0.382 * diff,
        "0.5": high - 0.5 * diff,
        "0.618": high - 0.618 * diff,
        "0.786": high - 0.786 * diff,
        "1.0": low,
    }
