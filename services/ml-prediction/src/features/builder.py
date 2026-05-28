"""Feature engineering — one canonical matrix used by every ML head.

Keeping this single-source lets us swap models (RF/XGB/LSTM) without drift
between training and inference features.

22 features (14 original + 8 new):
  Momentum : ret_1/5/10/20/60
  Volatility: vol_20, vol_60, atr_14_pct, atr_ratio
  Trend     : sma_20_gap, sma_50_gap, sma_100_gap
  Oscillators: rsi_14, macd, macd_signal, macd_hist, bb_pct, stoch_k
  Volume    : volume_z, obv_z, cmf_20
  Range     : high_20_pct
"""
from __future__ import annotations

import numpy as np
import pandas as pd


FEATURE_COLUMNS = [
    # Momentum
    "ret_1",
    "ret_5",
    "ret_10",
    "ret_20",
    "ret_60",
    # Volatility
    "vol_20",
    "vol_60",
    "atr_14_pct",
    "atr_ratio",
    # Trend
    "sma_20_gap",
    "sma_50_gap",
    "sma_100_gap",
    # Oscillators
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_pct",
    "stoch_k",
    # Volume / money flow
    "volume_z",
    "obv_z",
    "cmf_20",
    # Range
    "high_20_pct",
]


def _rsi(close: pd.Series, w: int = 14) -> pd.Series:
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / w, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / w, adjust=False).mean()
    rs = g / l.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def build_features(df: pd.DataFrame, horizon: int = 5) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Return (X, y_direction, y_return).

    y_direction = sign of forward `horizon`-bar return (binary classification).
    y_return    = raw forward return (regression target for magnitude models).
    """
    out = pd.DataFrame(index=df.index)
    c = df["close"].astype(float)
    h = df["high"].astype(float)
    lo = df["low"].astype(float)
    vol = df["volume"].astype(float)

    # --- Momentum ---
    for w in (1, 5, 10, 20, 60):
        out[f"ret_{w}"] = c.pct_change(w)

    # --- Volatility ---
    daily_ret = c.pct_change()
    out["vol_20"] = daily_ret.rolling(20).std()
    out["vol_60"] = daily_ret.rolling(60).std()

    tr = pd.concat([
        h - lo,
        (h - c.shift(1)).abs(),
        (lo - c.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    out["atr_14_pct"] = atr14 / c.replace(0, np.nan)
    out["atr_ratio"] = atr14 / atr14.rolling(20).mean().replace(0, np.nan)

    # --- Trend ---
    sma20 = c.rolling(20).mean()
    sma50 = c.rolling(50).mean()
    sma100 = c.rolling(100, min_periods=60).mean()
    out["sma_20_gap"] = (c - sma20) / sma20.replace(0, np.nan)
    out["sma_50_gap"] = (c - sma50) / sma50.replace(0, np.nan)
    out["sma_100_gap"] = (c - sma100) / sma100.replace(0, np.nan)

    # --- Oscillators ---
    out["rsi_14"] = _rsi(c)

    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    macd_line = ema12 - ema26
    sig = macd_line.ewm(span=9, adjust=False).mean()
    out["macd"] = macd_line
    out["macd_signal"] = sig
    out["macd_hist"] = macd_line - sig

    bb_mid = c.rolling(20).mean()
    bb_std = c.rolling(20).std()
    out["bb_pct"] = (c - (bb_mid - 2 * bb_std)) / (4 * bb_std).replace(0, np.nan)

    low14 = lo.rolling(14).min()
    high14 = h.rolling(14).max()
    out["stoch_k"] = (c - low14) / (high14 - low14).replace(0, np.nan) * 100

    # --- Volume / money flow ---
    out["volume_z"] = (vol - vol.rolling(20).mean()) / vol.rolling(20).std().replace(0, np.nan)

    obv = (np.sign(c.diff()) * vol).fillna(0).cumsum()
    obv_std = obv.rolling(20).std().replace(0, np.nan)
    out["obv_z"] = (obv - obv.rolling(20).mean()) / obv_std

    mf_mult = ((c - lo) - (h - c)) / (h - lo).replace(0, np.nan)
    mf_vol = mf_mult * vol
    vol_sum = vol.rolling(20).sum().replace(0, np.nan)
    out["cmf_20"] = mf_vol.rolling(20).sum() / vol_sum

    # --- Range position ---
    high20 = h.rolling(20).max()
    low20 = lo.rolling(20).min()
    out["high_20_pct"] = (c - low20) / (high20 - low20).replace(0, np.nan)

    # --- Target ---
    fwd_ret = c.shift(-horizon) / c - 1
    y_dir = (fwd_ret > 0).astype(int)

    X = out[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
    mask = X.notna().all(axis=1) & fwd_ret.notna()
    return X[mask], y_dir[mask], fwd_ret[mask]
