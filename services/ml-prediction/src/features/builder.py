"""Feature engineering — 26 features (22 stock-specific + 4 macro).

22 stock-specific:
  Momentum  : ret_1/5/10/20/60
  Volatility: vol_20, vol_60, atr_14_pct, atr_ratio
  Trend     : sma_20_gap, sma_50_gap, sma_100_gap
  Oscillators: rsi_14, macd, macd_signal, macd_hist, bb_pct, stoch_k
  Volume    : volume_z, obv_z, cmf_20
  Range     : high_20_pct

4 macro (market-wide context):
  spy_ret_1, spy_ret_5  — S&P 500 short-term direction
  vix_level             — VIX absolute level (fear gauge)
  spy_vol_20            — S&P 500 realized volatility (regime proxy)

Label: binary BUY / SELL only — rows where |fwd_ret| < label_threshold are
excluded from training (dead zone). This removes noise-level moves that are
essentially unclassifiable and degrade model quality.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd


MACRO_COLUMNS = ["spy_ret_1", "spy_ret_5", "vix_level", "spy_vol_20"]

FEATURE_COLUMNS = [
    # Momentum
    "ret_1", "ret_5", "ret_10", "ret_20", "ret_60",
    # Volatility
    "vol_20", "vol_60", "atr_14_pct", "atr_ratio",
    # Trend
    "sma_20_gap", "sma_50_gap", "sma_100_gap",
    # Oscillators
    "rsi_14", "macd", "macd_signal", "macd_hist", "bb_pct", "stoch_k",
    # Volume / money flow
    "volume_z", "obv_z", "cmf_20",
    # Range
    "high_20_pct",
    # Macro
    "spy_ret_1", "spy_ret_5", "vix_level", "spy_vol_20",
]


def fetch_macro_features(start_date: date, end_date: date) -> pd.DataFrame:
    """Download SPY + VIX macro features, indexed by date string ("YYYY-MM-DD").

    Returns an empty DataFrame on any failure — build_features handles missing
    macro gracefully by falling back to 0-filled values.
    """
    import yfinance as yf

    buffer_start = start_date - timedelta(days=60)  # extra buffer for rolling calculations
    try:
        spy = yf.download(
            "SPY",
            start=buffer_start.isoformat(),
            end=end_date.isoformat(),
            progress=False,
            auto_adjust=False,
        )
        vix = yf.download(
            "^VIX",
            start=buffer_start.isoformat(),
            end=end_date.isoformat(),
            progress=False,
            auto_adjust=False,
        )
    except Exception:
        return pd.DataFrame()

    if spy.empty or vix.empty:
        return pd.DataFrame()

    # Flatten MultiIndex columns (yfinance ≥0.2)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = [c[0] for c in spy.columns]
    if isinstance(vix.columns, pd.MultiIndex):
        vix.columns = [c[0] for c in vix.columns]

    spy_c = spy["Close"]
    vix_c = vix["Close"].reindex(spy_c.index, method="ffill")

    macro = pd.DataFrame(index=pd.to_datetime(spy_c.index).strftime("%Y-%m-%d"))
    macro["spy_ret_1"] = spy_c.pct_change(1).values
    macro["spy_ret_5"] = spy_c.pct_change(5).values
    macro["vix_level"] = vix_c.values
    macro["spy_vol_20"] = spy_c.pct_change().rolling(20).std().values

    return macro.dropna(how="all")


def compute_label_threshold(df: pd.DataFrame, horizon: int = 5) -> float:
    """Volatility-adjusted dead zone: 0.5 × expected horizon-day move magnitude.

    Higher-volatility stocks get a wider dead zone (more noise to filter out).
    Lower-volatility stocks use a narrower one (small moves are still signal).
    Clamped to [0.5%, 3%] to avoid degenerate cases.

    Examples (daily vol → threshold):
      0.5% daily → 0.56% threshold  (stable dividend stock)
      1.0% daily → 1.12% threshold  (average large-cap)
      2.0% daily → 2.24% threshold  (high-beta / HK small-cap)
    """
    daily_ret = df["close"].astype(float).pct_change()
    vol_series = daily_ret.rolling(20).std().dropna()
    if not vol_series.empty:
        return float(np.clip(0.5 * float(vol_series.median()) * np.sqrt(horizon), 0.005, 0.03))
    return 0.01  # fallback for very short histories


def _rsi(close: pd.Series, w: int = 14) -> pd.Series:
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / w, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / w, adjust=False).mean()
    rs = g / l.replace(0, np.nan)
    return 100 - 100 / (1 + rs)


def build_features(
    df: pd.DataFrame,
    horizon: int = 5,
    macro_df: pd.DataFrame | None = None,
    label_threshold: float = 0.01,
    inference_mode: bool = False,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Return (X, y_direction, y_return).

    y_direction = 1 if forward return > label_threshold, 0 if < -label_threshold.
    Rows within ±label_threshold (the dead zone) are excluded from training —
    only clear-signal bars are used.

    inference_mode=True: skip label/dead-zone filtering so the latest bar (no
    known future return) is included in X for real-time prediction.
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
    # Normalise by price so MACD is comparable across different price levels and over time
    price_norm = c.replace(0, np.nan)
    out["macd"] = macd_line / price_norm
    out["macd_signal"] = sig / price_norm
    out["macd_hist"] = (macd_line - sig) / price_norm

    bb_mid = c.rolling(20).mean()
    bb_std = c.rolling(20).std()
    out["bb_pct"] = (c - (bb_mid - 2 * bb_std)) / (4 * bb_std).replace(0, np.nan)

    low14 = lo.rolling(14).min()
    high14 = h.rolling(14).max()
    out["stoch_k"] = (c - low14) / (high14 - low14).replace(0, np.nan) * 100

    # --- Volume / money flow ---
    out["volume_z"] = (vol - vol.rolling(20).mean()) / vol.rolling(20).std().replace(0, np.nan)

    obv = (np.sign(c.diff()) * vol).fillna(0).cumsum()
    # Use 20-day OBV change (recent flow) rather than cumulative level z-score,
    # which is dominated by the long-term trend and not sensitive to recent momentum shifts.
    obv_change = obv.diff(20)
    obv_change_std = obv_change.rolling(60).std().replace(0, np.nan)
    out["obv_z"] = obv_change / obv_change_std

    mf_mult = ((c - lo) - (h - c)) / (h - lo).replace(0, np.nan)
    mf_vol = mf_mult * vol
    vol_sum = vol.rolling(20).sum().replace(0, np.nan)
    out["cmf_20"] = mf_vol.rolling(20).sum() / vol_sum

    # --- Range position ---
    high20 = h.rolling(20).max()
    low20 = lo.rolling(20).min()
    out["high_20_pct"] = (c - low20) / (high20 - low20).replace(0, np.nan)

    # --- Macro features (market-wide context) ---
    # Date keys are "YYYY-MM-DD" strings to avoid timezone issues
    dates = pd.to_datetime(df["ts"]).dt.strftime("%Y-%m-%d")
    if macro_df is not None and not macro_df.empty:
        for col in MACRO_COLUMNS:
            if col in macro_df.columns:
                out[col] = dates.map(macro_df[col])
            else:
                out[col] = np.nan
    else:
        for col in MACRO_COLUMNS:
            out[col] = np.nan

    # Forward-fill macro gaps (weekends, holidays, early-day partial data)
    for col in MACRO_COLUMNS:
        out[col] = out[col].ffill()
    # Zero-fill any remaining NaN (e.g. yfinance download failure, leading rows)
    # In inference mode also backward-fill so the latest bar is never NaN
    if inference_mode:
        for col in MACRO_COLUMNS:
            out[col] = out[col].bfill().fillna(0.0)
    else:
        for col in MACRO_COLUMNS:
            out[col] = out[col].fillna(0.0)

    # --- Target ---
    fwd_ret = c.shift(-horizon) / c - 1
    # After dead-zone filtering (below), only |fwd_ret| >= threshold rows remain,
    # so using > 0 cleanly separates up from down moves for all surviving rows.
    y_dir = (fwd_ret > 0).astype(int)  # 1 = up, 0 = down (dead-zone rows excluded by mask)

    X = out[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)

    if inference_mode:
        # Keep all rows with valid features; label/dead-zone filtering skipped
        mask = X.notna().all(axis=1)
    else:
        # Training: exclude dead-zone rows (|fwd_ret| < threshold) — only clear signals
        outside_deadzone = fwd_ret.abs() >= label_threshold
        mask = X.notna().all(axis=1) & fwd_ret.notna() & outside_deadzone

    return X[mask], y_dir[mask], fwd_ret[mask]
