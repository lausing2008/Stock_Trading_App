"""Feature engineering — 47 features (29 stock-specific + 8 macro + 10 fundamental).

29 stock-specific:
  Momentum  : ret_1/5/10/20/60, momentum_12_1
  Volatility: vol_20, vol_60, atr_14_pct, atr_ratio, vol_ratio_5d20d
  Trend     : sma_20_gap, sma_50_gap, sma_100_gap, sma_200_gap
  Oscillators: rsi_14, macd, macd_signal, macd_hist, bb_pct, stoch_k
  Volume    : volume_z, obv_z, cmf_20
  Range     : high_20_pct, dist_52w_high, dist_52w_low
  Weekly    : weekly_rsi, weekly_trend  (SA-29 — longer-term regime context)

8 macro (market-wide context):
  spy_ret_1, spy_ret_5  — S&P 500 short-term direction
  vix_level             — VIX absolute level (fear gauge)
  spy_vol_20            — S&P 500 realized volatility (regime proxy)
  is_bear_market        — 1 if SPY < 200d SMA (binary regime flag)
  vix_spiking           — 1 if VIX > 20d MA × 1.3 (sudden fear spike)
  high_vol_regime       — 1 if spy_vol_20 > 2% annualised daily vol
  market_stress         — 1 if SPY 5d return < -3% AND VIX above its MA

10 fundamental (quarterly company metrics — static per stock, broadcast to all bars):
  revenue_growth        — YoY revenue growth rate
  earnings_growth       — YoY EPS growth rate
  gross_margin          — gross profit margin
  return_on_equity      — ROE (net income / book equity)
  fcf_yield             — free cash flow / market cap (value quality signal)
  short_ratio           — days-to-cover (short interest / avg daily volume)
  recommendation_mean   — analyst consensus (1=strong buy … 5=strong sell)
  price_to_book         — P/B ratio (value factor)
  peg_ratio             — PE / forward EPS growth (growth at a reasonable price)
  debt_to_equity        — total debt / total equity (solvency risk signal)

Label: binary BUY / SELL only — rows where |fwd_ret| < label_threshold are
excluded from training (dead zone). This removes noise-level moves that are
essentially unclassifiable and degrade model quality.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd


def _adj_close(df: pd.DataFrame) -> pd.Series:
    """Return adj_close when available (filling gaps with close), else close."""
    ac = df.get("adj_close")
    if ac is not None and not ac.isna().all():
        return ac.fillna(df["close"]).astype(float)
    return df["close"].astype(float)


MACRO_COLUMNS = [
    "spy_ret_1", "spy_ret_5", "vix_level", "spy_vol_20",
    # Regime boolean flags (SA-3)
    "is_bear_market", "vix_spiking", "high_vol_regime", "market_stress",
]

FUNDAMENTAL_COLUMNS = [
    "revenue_growth",       # YoY revenue growth rate
    "earnings_growth",      # YoY EPS growth rate
    "gross_margin",         # gross profit / revenue
    "return_on_equity",     # net income / book equity
    "fcf_yield",            # free cash flow / market cap
    "short_ratio",          # days-to-cover (short interest / avg daily vol)
    "recommendation_mean",  # 1=strong buy … 5=strong sell
    "price_to_book",        # P/B ratio
    "peg_ratio",            # PE / forward EPS growth (Phase 1)
    "debt_to_equity",       # total debt / total equity (Phase 1)
]

# SA-29: Weekly context features — NaN-allowed (like fundamentals) so stocks with
# short history (<15 weekly bars) are not excluded from training entirely.
WEEKLY_COLUMNS = [
    "weekly_rsi",    # 14-week RSI; <40=oversold, >70=overbought on weekly timeframe
    "weekly_trend",  # +1 if price > 10-week SMA by >1%, -1 if below by >1%, else 0
]

FEATURE_COLUMNS = [
    # Momentum
    "ret_1", "ret_5", "ret_10", "ret_20", "ret_60",
    "momentum_12_1",       # 12-month minus 1-month return (classic factor; avoids 1m reversal)
    # Volatility
    "vol_20", "vol_60", "atr_14_pct", "atr_ratio",
    "vol_ratio_5d20d",      # 5-day / 20-day vol ratio — expansion = choppy, compression = breakout setup
    # Trend
    "sma_20_gap", "sma_50_gap", "sma_100_gap",
    "sma_200_gap",         # long-term trend filter; most-watched institutional level
    # Oscillators
    "rsi_14", "macd", "macd_signal", "macd_hist", "bb_pct", "stoch_k",
    # Volume / money flow
    "volume_z", "obv_z", "cmf_20",
    # Range
    "high_20_pct",
    "dist_52w_high",       # breakout proximity; momentum strength signal
    "dist_52w_low",        # bounce proximity; mean-reversion/support signal
    # Weekly context (SA-29) — longer-term regime; NaN-allowed for short histories
    *WEEKLY_COLUMNS,
    # Macro — raw
    "spy_ret_1", "spy_ret_5", "vix_level", "spy_vol_20",
    # Macro — regime boolean flags
    "is_bear_market", "vix_spiking", "high_vol_regime", "market_stress",
    # Fundamentals — static per stock, broadcast to all price rows
    *FUNDAMENTAL_COLUMNS,
]


_MACRO_CACHE_KEY = "stockai:macro_features"
_MACRO_CACHE_TTL = 86_400  # 24 hours — macro data is daily, one fresh fetch per trading day is enough


def _redis_save_macro(macro: pd.DataFrame) -> None:
    try:
        import json, redis as redis_lib
        from common.config import get_settings
        r = redis_lib.Redis.from_url(get_settings().redis_url, decode_responses=True)
        r.setex(_MACRO_CACHE_KEY, _MACRO_CACHE_TTL, macro.to_json(orient="split"))
    except Exception:
        pass


def _redis_load_macro() -> pd.DataFrame:
    try:
        import json, redis as redis_lib
        from common.config import get_settings
        r = redis_lib.Redis.from_url(get_settings().redis_url, decode_responses=True)
        raw = r.get(_MACRO_CACHE_KEY)
        if raw:
            return pd.read_json(raw, orient="split")
    except Exception:
        pass
    return pd.DataFrame()


def fetch_macro_features(start_date: date, end_date: date) -> pd.DataFrame:
    """Download SPY + VIX macro features, indexed by date string ("YYYY-MM-DD").

    On yfinance failure, returns the last successful fetch from Redis rather than
    an empty DataFrame — zero-filling all macro features causes distribution shift
    between training and inference since zero VIX/SPY returns never occur naturally.
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
        return _redis_load_macro()

    if spy.empty or vix.empty:
        return _redis_load_macro()

    # Flatten MultiIndex columns (yfinance ≥0.2)
    if isinstance(spy.columns, pd.MultiIndex):
        spy.columns = [c[0] for c in spy.columns]
    if isinstance(vix.columns, pd.MultiIndex):
        vix.columns = [c[0] for c in vix.columns]

    spy_c = spy["Close"]
    vix_c = vix["Close"].reindex(spy_c.index).ffill()

    macro = pd.DataFrame(index=pd.to_datetime(spy_c.index).strftime("%Y-%m-%d"))
    macro["spy_ret_1"] = spy_c.pct_change(1).values
    macro["spy_ret_5"] = spy_c.pct_change(5).values
    macro["vix_level"] = vix_c.values
    macro["spy_vol_20"] = spy_c.pct_change().rolling(20).std().values

    # ── Regime boolean flags (SA-3) ───────────────────────────────────────────
    spy_200d = spy_c.rolling(200, min_periods=100).mean()
    vix_20d  = vix_c.rolling(20, min_periods=10).mean()

    # np.where produces a numpy array (no index), avoiding DatetimeIndex →
    # string-index misalignment when assigning to the macro DataFrame.
    # NaN is preserved where the rolling window hasn't filled in yet.
    macro["is_bear_market"] = np.where(
        spy_200d.isna(), np.nan, (spy_c < spy_200d).astype(float)
    )
    macro["vix_spiking"] = np.where(
        vix_20d.isna(), np.nan, (vix_c > vix_20d * 1.3).astype(float)
    )
    macro["high_vol_regime"] = np.where(
        macro["spy_vol_20"].isna(), np.nan, (macro["spy_vol_20"] > 0.02).astype(float)
    )
    macro["market_stress"] = np.where(
        vix_20d.isna(), np.nan,
        ((macro["spy_ret_5"] < -0.03).values & (vix_c > vix_20d).values).astype(float),
    )

    result = macro.dropna(how="all")
    _redis_save_macro(result)
    return result


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
    daily_ret = _adj_close(df).pct_change()
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
    fund_data: dict | None = None,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Return (X, y_direction, y_return).

    y_direction = 1 if forward return > label_threshold, 0 if < -label_threshold.
    Rows within ±label_threshold (the dead zone) are excluded from training —
    only clear-signal bars are used.

    inference_mode=True: skip label/dead-zone filtering so the latest bar (no
    known future return) is included in X for real-time prediction.

    fund_data: dict mapping FUNDAMENTAL_COLUMNS names to scalar floats.
    Values are broadcast to every row in X (fundamentals change quarterly;
    using the most-recent snapshot as a static signal is standard practice).
    Missing keys default to NaN — XGBoost handles NaN natively.
    """
    out = pd.DataFrame(index=df.index)
    c = _adj_close(df)
    h = df["high"].astype(float)
    lo = df["low"].astype(float)
    vol = df["volume"].astype(float)

    # --- Momentum ---
    for w in (1, 5, 10, 20, 60):
        out[f"ret_{w}"] = c.pct_change(w)
    # 12-month minus 1-month: avoids short-term reversal; tracks sustained momentum
    out["momentum_12_1"] = c.pct_change(252) - c.pct_change(21)

    # --- Volatility ---
    daily_ret = c.pct_change()
    out["vol_20"] = daily_ret.rolling(20).std()
    out["vol_60"] = daily_ret.rolling(60).std()
    out["vol_ratio_5d20d"] = daily_ret.rolling(5).std() / daily_ret.rolling(20).std().replace(0, np.nan)

    tr = pd.concat([
        h - lo,
        (h - c.shift(1)).abs(),
        (lo - c.shift(1)).abs(),
    ], axis=1).max(axis=1)
    atr14 = tr.rolling(14).mean()
    out["atr_14_pct"] = atr14 / c.replace(0, np.nan)
    out["atr_ratio"] = atr14 / atr14.rolling(20).mean().replace(0, np.nan)

    # --- Trend ---
    sma20  = c.rolling(20).mean()
    sma50  = c.rolling(50).mean()
    sma100 = c.rolling(100, min_periods=60).mean()
    sma200 = c.rolling(200, min_periods=100).mean()
    out["sma_20_gap"]  = (c - sma20)  / sma20.replace(0, np.nan)
    out["sma_50_gap"]  = (c - sma50)  / sma50.replace(0, np.nan)
    out["sma_100_gap"] = (c - sma100) / sma100.replace(0, np.nan)
    out["sma_200_gap"] = (c - sma200) / sma200.replace(0, np.nan)

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
    low20  = lo.rolling(20).min()
    out["high_20_pct"] = (c - low20) / (high20 - low20).replace(0, np.nan)

    high252 = h.rolling(252).max()
    low252  = lo.rolling(252).min()
    out["dist_52w_high"] = (c - high252) / high252.replace(0, np.nan)
    out["dist_52w_low"]  = (c - low252)  / low252.replace(0, np.nan)

    # --- Weekly technicals (SA-29) ---
    # Resample to weekly (Friday closes). Each daily bar forward-fills from the most
    # recent completed week (no look-ahead: Mon–Thu see last Friday's RSI; Friday sees
    # the current Friday's RSI which is end-of-day, so still valid).
    _ts_idx = pd.to_datetime(df["ts"]).dt.normalize()
    _close_ts = pd.Series(c.values, index=_ts_idx)
    _wclose = _close_ts.resample("W-FRI").last().dropna()
    if len(_wclose) >= 15:
        _wrsi     = _rsi(_wclose, w=14)
        _wsma10   = _wclose.rolling(10, min_periods=5).mean()
        _wtrend_r = (_wclose / _wsma10.replace(0, np.nan) - 1)
        _wrsi_d   = _wrsi.reindex(_ts_idx).ffill().values
        _wtrend_d = _wtrend_r.reindex(_ts_idx).ffill().values
        out["weekly_rsi"]   = _wrsi_d
        out["weekly_trend"] = np.where(_wtrend_d > 0.01, 1.0,
                              np.where(_wtrend_d < -0.01, -1.0, 0.0))
    else:
        out["weekly_rsi"]   = np.nan
        out["weekly_trend"] = np.nan

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
            out[col] = out[col].ffill().fillna(0.0)
    else:
        for col in MACRO_COLUMNS:
            out[col] = out[col].fillna(0.0)

    # --- Fundamental features (static per stock — broadcast from most-recent snapshot) ---
    # XGBoost handles NaN natively; models trained before this data exists will
    # see NaN for all fundamental columns and learn to ignore them gracefully.
    for col in FUNDAMENTAL_COLUMNS:
        val = (fund_data or {}).get(col)
        out[col] = float(val) if val is not None else np.nan

    # --- Target ---
    fwd_ret = c.shift(-horizon) / c - 1
    # After dead-zone filtering (below), only |fwd_ret| >= threshold rows remain,
    # so using > 0 cleanly separates up from down moves for all surviving rows.
    y_dir = (fwd_ret > 0).astype(int)  # 1 = up, 0 = down (dead-zone rows excluded by mask)

    X = out[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)

    # Fundamental and weekly columns are NaN-allowed — XGBoost handles NaN natively.
    # Require only the core daily features to be non-null so rows aren't discarded
    # when fundamentals are absent or history is too short for weekly bars.
    _nan_ok = set(FUNDAMENTAL_COLUMNS) | set(WEEKLY_COLUMNS)
    _required = [c for c in FEATURE_COLUMNS if c not in _nan_ok]

    if inference_mode:
        # Keep all rows with valid features; label/dead-zone filtering skipped
        mask = X[_required].notna().all(axis=1)
    else:
        # Training: exclude dead-zone rows (|fwd_ret| < threshold) — only clear signals
        outside_deadzone = fwd_ret.abs() >= label_threshold
        mask = X[_required].notna().all(axis=1) & fwd_ret.notna() & outside_deadzone

    return X[mask], y_dir[mask], fwd_ret[mask]
