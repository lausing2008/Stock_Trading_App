"""Feature engineering — one canonical matrix used by every ML head.

Keeping this single-source lets us swap models (RF/XGB/LSTM) without drift
between training and inference features.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


FEATURE_COLUMNS = [
    "ret_1",
    "ret_5",
    "ret_10",
    "ret_20",
    "vol_20",
    "vol_60",
    "sma_20_gap",
    "sma_50_gap",
    "rsi_14",
    "macd",
    "macd_signal",
    "macd_hist",
    "bb_pct",
    "volume_z",
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
    y_return = raw forward return (regression target for vol / magnitude models).
    """
    out = pd.DataFrame(index=df.index)
    c = df["close"].astype(float)

    for w in (1, 5, 10, 20):
        out[f"ret_{w}"] = c.pct_change(w)

    out["vol_20"] = c.pct_change().rolling(20).std()
    out["vol_60"] = c.pct_change().rolling(60).std()

    sma20 = c.rolling(20).mean()
    sma50 = c.rolling(50).mean()
    out["sma_20_gap"] = (c - sma20) / sma20
    out["sma_50_gap"] = (c - sma50) / sma50

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

    vol = df["volume"].astype(float)
    out["volume_z"] = (vol - vol.rolling(20).mean()) / vol.rolling(20).std()

    fwd_ret = c.shift(-horizon) / c - 1
    y_dir = (fwd_ret > 0).astype(int)

    X = out[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)
    mask = X.notna().all(axis=1) & fwd_ret.notna()
    return X[mask], y_dir[mask], fwd_ret[mask]
