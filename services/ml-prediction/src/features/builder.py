"""Feature engineering — 62 features (29 stock-specific + 11 macro + 3 sector + 16 fundamental + 3 signal outcome).

29 stock-specific:
  Momentum  : ret_1/5/10/20/60, momentum_12_1
  Volatility: vol_20, vol_60, atr_14_pct, atr_ratio, vol_ratio_5d20d
  Trend     : sma_20_gap, sma_50_gap, sma_100_gap, sma_200_gap
  Oscillators: rsi_14, macd, macd_signal, macd_hist, bb_pct, stoch_k
  Volume    : volume_z, obv_z, cmf_20
  Range     : high_20_pct, dist_52w_high, dist_52w_low
  Weekly    : weekly_rsi, weekly_trend  (SA-29 — longer-term regime context)

11 macro (market-wide context):
  spy_ret_1, spy_ret_5  — S&P 500 short-term direction
  vix_level             — VIX absolute level (fear gauge)
  spy_vol_20            — S&P 500 realized volatility (regime proxy)
  is_bear_market        — 1 if SPY < 200d SMA for US; 1 if HSI < 200d SMA for HK
  vix_spiking           — 1 if VIX > 20d MA × 1.3 (sudden fear spike)
  high_vol_regime       — 1 if spy_vol_20 > 2% annualised daily vol
  market_stress         — 1 if SPY 5d return < -3% AND VIX above its MA
  hsi_ret_1             — HSI 1-day return (HK stocks only; NaN for US)
  hsi_ret_5             — HSI 5-day return (HK stocks only; NaN for US)
  hsi_200d_gap          — (HSI price / HSI 200d SMA) - 1 (HK stocks only; NaN for US)

3 sector (TIER90 — NaN for HK/unmapped sectors; XGBoost handles natively):
  sector_rs_20d         — sector ETF 20d return minus SPY 20d return (positive = outperforming)
  sector_rs_5d          — sector ETF 5d return minus SPY 5d return (short-term momentum)
  sector_in_favor       — 1 if sector_rs_20d > 0

18 fundamental (quarterly company metrics — static per stock, broadcast to all bars):
  revenue_growth        — YoY revenue growth rate
  earnings_growth       — YoY EPS growth rate
  gross_margin          — gross profit margin
  return_on_equity      — ROE (net income / book equity)
  fcf_yield             — free cash flow / market cap (value quality signal)
  short_ratio           — days-to-cover (short interest / avg daily volume)
  short_ratio_delta     — change in short_ratio vs prior snapshot (covering=bullish)
  short_percent_of_float — % of float sold short (T204: squeeze risk / contrarian)
  recommendation_mean   — analyst consensus (1=strong buy … 5=strong sell)
  price_to_book         — P/B ratio (value factor)
  peg_ratio             — PE / forward EPS growth (growth at a reasonable price)
  debt_to_equity        — total debt / total equity (solvency risk signal)
  eps_beat_streak       — consecutive quarters beating EPS estimate (0–4 clipped)
  eps_surprise_avg      — rolling 4-quarter average EPS surprise % (positive=beat)
  days_to_earnings      — days to next expected earnings (0–90, 90=unknown/far)
  avg_post_earnings_return_5d — mean 5d return after past 4 earnings (PEAD signal)
  avg_revenue_surprise_pct    — mean revenue beat/miss % over last 4 quarters
  (eps_revision_direction removed T237-ML2: broadcast today's analyst-recommendation-trend
   to every historical training row with no date bound — the exact lookahead-bias class
   already fixed for recommendation_mean itself via the PIT snapshot join below, but missed
   for this derived feature. Removed rather than reimplemented, matching this session's
   ML-DEAD1 precedent, since a correct point-in-time version needs a nontrivial rolling-window
   join against fundamentals_snapshot.)

3 signal outcome (T206 — look-ahead-safe: only uses outcomes with exit_date ≤ bar_date − 10d):
  sig_acc_30d   — fraction of BUY signals correct in the prior 30-day exit window
  sig_acc_90d   — fraction of BUY signals correct in the prior 90-day exit window
  sig_avg_ret_30d — mean realized pct_return of BUY signals in the prior 30-day exit window

Label: binary BUY / SELL only — rows where |fwd_ret| < label_threshold are
excluded from training (dead zone). This removes noise-level moves that are
essentially unclassifiable and degrade model quality.
"""
from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import pandas as pd

from common.logging import get_logger
from common.indicators import rsi as _canon_rsi, atr as _canon_atr

log = get_logger("ml-prediction.builder")


def _adj_close(df: pd.DataFrame) -> pd.Series:
    """Return adj_close when available (filling gaps with close), else close."""
    ac = df.get("adj_close")
    if ac is not None and not ac.isna().all():
        return ac.fillna(df["close"]).astype(float)
    return df["close"].astype(float)


# AUD232-054: kept in sync with meta_trainer.py's SECTOR_MAP sector-name coverage — the two
# maps serve different purposes (ETF lookup here vs. ordinal encoding there) so can't be merged
# into one dict, but both must recognize the same real stock.sector values or a stock silently
# gets valid features from one and "unknown" from the other for no reason but drift. Verified
# against production stocks.sector distinct values (2026-07-11).
SECTOR_ETF_MAP: dict[str, str] = {
    "Technology":             "XLK",
    "Financial Services":     "XLF",
    "Financial":              "XLF",
    "Healthcare":             "XLV",
    "Energy":                 "XLE",
    "Consumer Cyclical":      "XLY",
    "Consumer Defensive":     "XLP",
    "Utilities":              "XLU",
    "Industrials":            "XLI",
    "Basic Materials":        "XLB",
    "Communication Services": "XLC",
    "Real Estate":            "XLRE",
}

SECTOR_COLUMNS = [
    "sector_rs_20d",   # sector ETF 20d return vs SPY (positive = sector outperforming)
    "sector_rs_5d",    # sector ETF 5d return vs SPY (short-term momentum context)
    "sector_in_favor", # 1 if sector outperforming SPY over 20d
]


def fetch_sector_features(symbol: str, start_date: date, end_date: date) -> "pd.DataFrame":
    """Compute sector relative strength vs SPY from DB prices (TIER90).

    Returns a DataFrame indexed by "YYYY-MM-DD" strings with columns:
      sector_rs_20d  — sector ETF 20-day return minus SPY 20-day return
      sector_rs_5d   — sector ETF 5-day return minus SPY 5-day return
      sector_in_favor — 1.0 if sector_rs_20d > 0, else 0.0

    Returns an empty DataFrame when the stock has no sector, the sector has
    no ETF mapping, or the ETF/SPY prices are not in the DB.
    Falls back to empty DataFrame on any error — sector RS is NaN-allowed in
    FEATURE_COLUMNS so missing data never breaks model training.
    """
    try:
        from sqlalchemy import select as _select
        from db import Price, SessionLocal, Stock, TimeFrame

        buffer_start = start_date - timedelta(days=60)

        with SessionLocal() as session:
            stock = session.execute(
                _select(Stock).where(Stock.symbol == symbol.upper())
            ).scalar_one_or_none()

            if stock is None or not stock.sector:
                return pd.DataFrame()

            etf_symbol = SECTOR_ETF_MAP.get(stock.sector)
            if not etf_symbol:
                return pd.DataFrame()

            etf_stock = session.execute(
                _select(Stock).where(Stock.symbol == etf_symbol)
            ).scalar_one_or_none()
            spy_stock = session.execute(
                _select(Stock).where(Stock.symbol == "SPY")
            ).scalar_one_or_none()

            if etf_stock is None or spy_stock is None:
                return pd.DataFrame()

            etf_rows = session.execute(
                _select(Price.ts, Price.close).where(
                    Price.stock_id == etf_stock.id,
                    Price.timeframe == TimeFrame.D1,
                    Price.ts >= buffer_start,
                    Price.ts <= end_date,
                ).order_by(Price.ts)
            ).all()

            spy_rows = session.execute(
                _select(Price.ts, Price.close).where(
                    Price.stock_id == spy_stock.id,
                    Price.timeframe == TimeFrame.D1,
                    Price.ts >= buffer_start,
                    Price.ts <= end_date,
                ).order_by(Price.ts)
            ).all()

        if not etf_rows or not spy_rows:
            return pd.DataFrame()

        etf_s = pd.Series({r.ts.strftime("%Y-%m-%d"): float(r.close) for r in etf_rows})
        spy_s = pd.Series({r.ts.strftime("%Y-%m-%d"): float(r.close) for r in spy_rows})

        common = etf_s.index.intersection(spy_s.index)
        if len(common) < 25:
            return pd.DataFrame()

        etf_c, spy_c = etf_s[common], spy_s[common]
        df_out = pd.DataFrame(index=common)
        df_out["sector_rs_20d"]   = etf_c.pct_change(20) - spy_c.pct_change(20)
        df_out["sector_rs_5d"]    = etf_c.pct_change(5)  - spy_c.pct_change(5)
        df_out["sector_in_favor"] = (df_out["sector_rs_20d"] > 0).astype(float)
        return df_out.dropna(how="all")

    except Exception:
        return pd.DataFrame()


def fetch_signal_outcome_features(symbol: str, start_date: date, end_date: date) -> "pd.DataFrame":
    """Compute rolling BUY signal accuracy from signal_outcomes (T206).

    Returns a DataFrame indexed by "YYYY-MM-DD" strings with columns:
      sig_acc_30d    — fraction of BUY outcomes correct in 30-day exit window
      sig_acc_90d    — fraction of BUY outcomes correct in 90-day exit window
      sig_avg_ret_30d — mean realized pct_return in 30-day exit window

    Look-ahead-safe: each bar date D uses only outcomes with exit_date <= D - 10 days,
    so there is no leakage of future results into training. Requires min 5 outcomes per
    window — otherwise NaN. Falls back to empty DataFrame on any error.
    """
    try:
        from sqlalchemy import select as _select
        from db import SignalOutcome, Stock, SessionLocal

        # Fetch outcomes going back far enough to compute 90-day windows for start_date
        fetch_from = start_date - timedelta(days=110)

        with SessionLocal() as session:
            stock = session.execute(
                _select(Stock).where(Stock.symbol == symbol.upper())
            ).scalar_one_or_none()
            if stock is None:
                return pd.DataFrame()

            rows = session.execute(
                _select(
                    SignalOutcome.exit_date,
                    SignalOutcome.is_correct,
                    SignalOutcome.pct_return,
                ).where(
                    SignalOutcome.stock_id == stock.id,
                    SignalOutcome.signal_direction == "BUY",
                    SignalOutcome.is_correct.is_not(None),
                    SignalOutcome.exit_date.is_not(None),
                    SignalOutcome.exit_date >= fetch_from,
                    SignalOutcome.exit_date <= end_date,
                )
            ).all()

        if not rows:
            return pd.DataFrame()

        # Build a DataFrame keyed by exit_date
        out_df = pd.DataFrame(
            [(r.exit_date, int(r.is_correct), float(r.pct_return or 0)) for r in rows],
            columns=["exit_date", "is_correct", "pct_return"],
        )
        out_df["exit_date"] = pd.to_datetime(out_df["exit_date"])

        # One outcome per exit_date (average if multiple close same day)
        daily_correct = out_df.groupby("exit_date")["is_correct"].mean()
        daily_ret = out_df.groupby("exit_date")["pct_return"].mean()
        daily_count = out_df.groupby("exit_date")["is_correct"].count()

        full_range = pd.date_range(start=fetch_from, end=end_date, freq="D")
        daily_correct = daily_correct.reindex(full_range)
        daily_ret = daily_ret.reindex(full_range)
        daily_count = daily_count.reindex(full_range, fill_value=0)

        # Rolling windows (min_periods=5 enforces minimum sample requirement)
        acc_30 = daily_correct.rolling("30D", min_periods=5).mean()
        acc_90 = daily_correct.rolling("90D", min_periods=5).mean()
        cnt_30 = daily_count.rolling("30D", min_periods=1).sum()
        cnt_90 = daily_count.rolling("90D", min_periods=1).sum()
        ret_30 = daily_ret.rolling("30D", min_periods=5).mean()

        # Shift 10 days: bar_date D gets the value rolled up to D-10 (look-ahead buffer)
        bar_range = pd.date_range(start=start_date, end=end_date, freq="D")
        df_out = pd.DataFrame(index=bar_range)
        df_out["sig_acc_30d"] = acc_30.shift(10).reindex(bar_range)
        df_out["sig_acc_90d"] = acc_90.shift(10).reindex(bar_range)
        df_out["sig_avg_ret_30d"] = ret_30.shift(10).reindex(bar_range)

        # Enforce min count — NaN where fewer than 5 outcomes in window
        df_out.loc[cnt_30.shift(10).reindex(bar_range).fillna(0) < 5, ["sig_acc_30d", "sig_avg_ret_30d"]] = np.nan
        df_out.loc[cnt_90.shift(10).reindex(bar_range).fillna(0) < 5, "sig_acc_90d"] = np.nan

        df_out.index = df_out.index.strftime("%Y-%m-%d")
        return df_out.dropna(how="all")

    except Exception:
        return pd.DataFrame()


MACRO_COLUMNS = [
    "spy_ret_1", "spy_ret_5", "vix_level", "spy_vol_20",
    # Regime boolean flags (SA-3)
    "is_bear_market", "vix_spiking", "high_vol_regime", "market_stress",
    # HSI macro features (HK stocks only; NaN for US — XGBoost handles natively)
    "hsi_ret_1", "hsi_ret_5", "hsi_200d_gap",
]

OUTCOME_COLUMNS = [
    "sig_acc_30d",      # rolling 30-day BUY signal accuracy (look-ahead-safe)
    "sig_acc_90d",      # rolling 90-day BUY signal accuracy
    "sig_avg_ret_30d",  # rolling 30-day mean realized return on BUY signals
]

FUNDAMENTAL_COLUMNS = [
    "revenue_growth",       # YoY revenue growth rate
    "earnings_growth",      # YoY EPS growth rate
    "gross_margin",         # gross profit / revenue
    "return_on_equity",     # net income / book equity
    "fcf_yield",            # free cash flow / market cap
    "short_ratio",          # days-to-cover (short interest / avg daily vol)
    "short_ratio_delta",    # change vs prior fundamental snapshot (negative = short covering = bullish)
    "recommendation_mean",  # 1=strong buy … 5=strong sell
    "price_to_book",        # P/B ratio
    "peg_ratio",            # PE / forward EPS growth (Phase 1)
    "debt_to_equity",       # total debt / total equity (Phase 1)
    "short_percent_of_float",  # % of float sold short (T204: squeeze risk / contrarian signal)
    # Tier 78 — earnings quality features (from EarningsEvent table)
    # NOTE: eps_beat_streak, eps_surprise_avg, days_to_earnings, avg_post_earnings_return_5d,
    # avg_revenue_surprise_pct removed (CRIT-3/4): broadcasted today's value to all historical
    # training bars, introducing lookahead bias. Requires point-in-time joins to restore safely.
    # NOTE: flow_5d_net_hkd, flow_strength removed (CRIT-4): HK Connect flow is a daily time-
    # series — broadcasting today's 5-day flow to all historical bars is lookahead.
    # T217-B: DDM Dividend Discount Model — NaN for non-dividend stocks
    "ddm_discount",         # (div_yield / 0.07) - 1; positive = undervalued on dividend basis
    # T89-B: Piotroski F-Score — 0-9 composite quality metric from existing fundamentals
    "piotroski_score",      # 0=distressed, 9=high quality; NaN when insufficient fundamentals
    # T220-F/T237-ML2: eps_revision_direction removed — broadcast lookahead bias, see module
    # docstring above for the full explanation. Not reimplemented as point-in-time (yet).
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
    # Macro — HSI (HK stocks only; NaN for US — XGBoost handles natively)
    "hsi_ret_1", "hsi_ret_5", "hsi_200d_gap",
    # Sector relative strength (TIER90) — NaN for HK/unmapped stocks; XGBoost handles natively
    *SECTOR_COLUMNS,
    # Signal outcome accuracy (T206) — NaN-allowed; only populated after signal_outcomes accumulate
    *OUTCOME_COLUMNS,
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
    except Exception as exc:
        log.warning("macro_features.redis_save_failed", error=str(exc))


def _redis_load_macro() -> pd.DataFrame:
    try:
        import json, redis as redis_lib
        from common.config import get_settings
        r = redis_lib.Redis.from_url(get_settings().redis_url, decode_responses=True)
        raw = r.get(_MACRO_CACHE_KEY)
        if raw:
            return pd.read_json(raw, orient="split")
        log.warning("macro_features.redis_cache_empty", note="no prior successful fetch cached — returning empty DataFrame")
    except Exception as exc:
        log.warning("macro_features.redis_load_failed", error=str(exc))
    return pd.DataFrame()


def fetch_macro_features(start_date: date, end_date: date, symbol: str = "") -> pd.DataFrame:
    """Download SPY + VIX macro features, indexed by date string ("YYYY-MM-DD").

    For HK symbols (symbol ending with '.HK'), also fetches ^HSI and adds:
      hsi_ret_1    — HSI 1-day return
      hsi_ret_5    — HSI 5-day return
      hsi_200d_gap — (HSI price / HSI 200d SMA) - 1
      is_bear_market is overridden to use HSI < HSI 200d SMA for HK symbols.

    For US symbols, hsi_ret_1/hsi_ret_5/hsi_200d_gap are NaN (XGBoost handles natively).

    On yfinance failure, returns the last successful fetch from Redis rather than
    an empty DataFrame — zero-filling all macro features causes distribution shift
    between training and inference since zero VIX/SPY returns never occur naturally.
    """
    import yfinance as yf

    is_hk = symbol.upper().endswith(".HK")
    buffer_start = start_date - timedelta(days=260)  # extended buffer for 200d SMA rolling calculations

    # ML-MACRO1: both the except branch and the empty-result fallback previously returned
    # silently with zero logging — container logs showed 210+ "possibly delisted" yfinance
    # errors in a single day (2026-07-09, likely transient rate-limiting) with nothing at the
    # application level to surface that macro features were silently falling back to a stale
    # Redis cache. Warn so a prolonged outage (stale cache exhausted, or Redis itself empty)
    # is visible instead of only showing up as degraded model quality with no diagnostic trail.
    try:
        spy = yf.download(
            "SPY",
            start=buffer_start.isoformat(),
            end=end_date.isoformat(),
            progress=False,
        )
        vix = yf.download(
            "^VIX",
            start=buffer_start.isoformat(),
            end=end_date.isoformat(),
            progress=False,
            auto_adjust=False,  # VIX is an index, not a stock — no dividends/splits to adjust
        )
    except Exception as exc:
        log.warning("macro_features.fetch_failed", error=str(exc), note="falling back to Redis cache")
        return _redis_load_macro()

    if spy.empty or vix.empty:
        log.warning("macro_features.empty_result", spy_empty=spy.empty, vix_empty=vix.empty,
                    note="falling back to Redis cache")
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

    # ── HSI macro features (HK stocks only) ──────────────────────────────────
    # For non-HK symbols set columns to NaN so the DataFrame schema is consistent.
    if is_hk:
        try:
            hsi = yf.download(
                "^HSI",
                start=buffer_start.isoformat(),
                end=end_date.isoformat(),
                progress=False,
                auto_adjust=False,
            )
            if not hsi.empty:
                if isinstance(hsi.columns, pd.MultiIndex):
                    hsi.columns = [c[0] for c in hsi.columns]
                hsi_c = hsi["Close"]
                # Reindex to SPY dates (trading calendar alignment); ffill gaps
                hsi_c = hsi_c.reindex(spy_c.index).ffill()
                hsi_200d = hsi_c.rolling(200, min_periods=100).mean()

                macro["hsi_ret_1"]    = hsi_c.pct_change(1).values
                macro["hsi_ret_5"]    = hsi_c.pct_change(5).values
                macro["hsi_200d_gap"] = np.where(
                    hsi_200d.isna(), np.nan,
                    (hsi_c / hsi_200d.replace(0, np.nan) - 1).values,
                )
                # Override is_bear_market to use HSI rather than SPY for HK symbols
                macro["is_bear_market"] = np.where(
                    hsi_200d.isna(), np.nan, (hsi_c < hsi_200d).astype(float)
                )
            else:
                macro["hsi_ret_1"]    = np.nan
                macro["hsi_ret_5"]    = np.nan
                macro["hsi_200d_gap"] = np.nan
        except Exception as exc:
            log.warning("macro_features.hsi_fetch_failed", symbol=symbol, error=str(exc))
            macro["hsi_ret_1"]    = np.nan
            macro["hsi_ret_5"]    = np.nan
            macro["hsi_200d_gap"] = np.nan
    else:
        macro["hsi_ret_1"]    = np.nan
        macro["hsi_ret_5"]    = np.nan
        macro["hsi_200d_gap"] = np.nan

    result = macro.dropna(how="all")
    _redis_save_macro(result)
    return result


def compute_label_threshold(df: pd.DataFrame, horizon: int = 5, symbol: str = "") -> float:
    """Volatility-adjusted dead zone: 0.5 × expected horizon-day move magnitude.

    Higher-volatility stocks get a wider dead zone (more noise to filter out).
    Lower-volatility stocks use a narrower one (small moves are still signal).
    Clamped to [0.5%, 3%] for US stocks, [0.5%, 5%] for HK stocks.
    HK stocks are higher-volatility on average — the wider 5% ceiling prevents
    the dead zone from being clipped before it can capture the natural noise level.

    Examples (daily vol → threshold):
      0.5% daily → 0.56% threshold  (stable dividend stock)
      1.0% daily → 1.12% threshold  (average large-cap)
      2.0% daily → 2.24% threshold  (high-beta / HK small-cap)
    """
    daily_ret = _adj_close(df).pct_change()
    vol_series = daily_ret.rolling(20).std().dropna()
    upper_clamp = 0.05 if symbol.upper().endswith(".HK") else 0.03
    if not vol_series.empty:
        return float(np.clip(0.5 * float(vol_series.median()) * np.sqrt(horizon), 0.005, upper_clamp))
    return 0.01  # fallback for very short histories


def _rsi(close: pd.Series, w: int = 14) -> pd.Series:
    """T233-ARCH-INDICATOR-DEDUP: delegates to shared/common/indicators.py's canonical Wilder's
    RSI (pure refactor — this function's own formula already had min_periods, T237-ML3, and is
    numerically identical to the canonical version; verified byte-for-byte parity on real data
    before deploying, not just assumed from matching source)."""
    return _canon_rsi(close, window=w)


def _compute_piotroski(fund_data: dict) -> float:
    """Piotroski F-Score (0-9). Uses existing fundamentals fields.
    Returns NaN when insufficient data (XGBoost handles natively)."""
    score = 0
    roe = fund_data.get("return_on_equity")
    fcf = fund_data.get("fcf_yield")
    gross_margin = fund_data.get("gross_margin")
    # T247-MLPREDICTION-DEBTEQUITY-DEADFALLBACK: every caller of build_features (trainer.py's
    # _load_fundamentals, tuner.py) populates fund_data with "debt_to_equity", never
    # "debt_equity" — the removed fallback was permanently dead (fund_data.get("debt_equity")
    # was always None, so the `or` always fell through to debt_to_equity anyway), left in as
    # confusing code that looked like a deliberate alternate-source fallback.
    debt_equity = fund_data.get("debt_to_equity")
    rev_growth = fund_data.get("revenue_growth")
    earn_growth = fund_data.get("earnings_growth")
    # We only have current-period data (no YoY delta without history).
    # Use available proxies for the 9 tests:
    # Profitability (4 tests)
    if roe is not None and roe > 0: score += 1                           # ROA proxy > 0
    if fcf is not None and fcf > 0: score += 1                          # operating cash flow > 0
    if earn_growth is not None and earn_growth > 0: score += 1          # improving ROA proxy
    if roe is not None and fcf is not None and fcf > roe * 0.5: score += 1  # accruals: OCF supports earnings
    # Leverage/liquidity (3 tests)
    if debt_equity is not None and debt_equity < 1.0: score += 1        # low leverage
    if rev_growth is not None and rev_growth >= 0: score += 1           # stable/growing revenue
    if (earn_growth is not None and rev_growth is not None and earn_growth > rev_growth) or \
       (rev_growth is None and earn_growth is not None and earn_growth > 0): score += 1  # margin expanding
    # Efficiency (2 tests)
    if gross_margin is not None and gross_margin > 0.2: score += 1      # adequate gross margin
    if rev_growth is not None and earn_growth is not None and earn_growth >= rev_growth: score += 1  # improving asset efficiency proxy
    return float(score) if any(v is not None for v in [roe, fcf, gross_margin]) else float("nan")


def build_features(
    df: pd.DataFrame,
    horizon: int = 5,
    macro_df: pd.DataFrame | None = None,
    label_threshold: float = 0.01,
    inference_mode: bool = False,
    fund_data: dict | None = None,
    sector_df: "pd.DataFrame | None" = None,
    outcome_df: "pd.DataFrame | None" = None,
    up_to_date: str | None = None,
    fund_snapshots: "list[dict] | None" = None,
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

    up_to_date: optional "YYYY-MM-DD" string. When provided, only price rows
    up to and including that date are used. Useful for historical feature
    reconstruction without look-ahead leakage (e.g. meta-model training).
    """
    if up_to_date is not None:
        cutoff = pd.Timestamp(up_to_date)
        df = df[pd.to_datetime(df["ts"]) <= cutoff].copy()
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

    # T233-ARCH-INDICATOR-DEDUP: delegates to shared/common/indicators.py's canonical Wilder's
    # ATR (pure refactor — this was already numerically identical, T237-ML3 min_periods fix
    # already applied; verified byte-for-byte parity on real data before deploying).
    atr14 = _canon_atr(h, lo, c, period=14)
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

    # T237-ML3: min_periods added — see _rsi()'s comment above for the full explanation.
    ema12 = c.ewm(span=12, adjust=False, min_periods=12).mean()
    ema26 = c.ewm(span=26, adjust=False, min_periods=26).mean()
    macd_line = ema12 - ema26
    sig = macd_line.ewm(span=9, adjust=False, min_periods=9).mean()
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

    # --- Sector relative strength features (TIER90) ---
    # Indexed by "YYYY-MM-DD" string — same pattern as macro_df.
    # NaN for HK stocks and any symbol whose sector has no ETF mapping.
    if sector_df is not None and not sector_df.empty:
        for col in SECTOR_COLUMNS:
            if col in sector_df.columns:
                out[col] = dates.map(sector_df[col])
            else:
                out[col] = np.nan
    else:
        for col in SECTOR_COLUMNS:
            out[col] = np.nan

    # Forward-fill sector gaps (weekends / ETF trading calendar mismatches)
    for col in SECTOR_COLUMNS:
        out[col] = out[col].ffill()

    # --- Signal outcome accuracy features (T206) ---
    # Look-ahead-safe: fetch_signal_outcome_features() already shifts values by 10 days.
    # NaN-allowed; XGBoost handles missing values natively.
    if outcome_df is not None and not outcome_df.empty:
        for col in OUTCOME_COLUMNS:
            if col in outcome_df.columns:
                out[col] = dates.map(outcome_df[col])
            else:
                out[col] = np.nan
    else:
        for col in OUTCOME_COLUMNS:
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
    # Piotroski F-Score: computed from existing fundamentals, not a raw DB field
    out["piotroski_score"] = _compute_piotroski(fund_data or {})

    # T228-POINT-IN-TIME-FUNDAMENTALS: override broadcasted values with per-row snapshots.
    # These 4 columns are time-varying — broadcasting today's values to all historical rows
    # creates lookahead bias.  fund_snapshots is a list of dicts with "snapshot_date" + columns.
    #
    # T232-ML1 fix: `out` carries `df`'s RangeIndex (0..n-1), not a DatetimeIndex. The previous
    # `pd.to_datetime(out.index)` therefore interpreted those integers as nanoseconds since
    # epoch, turning every "price date" into 1970-01-01 — merge_asof against real snapshot
    # dates matched nothing and all 4 PIT columns silently became NaN for the entire training
    # set (caught by the blanket except below, so no error surfaced). Use the actual bar dates
    # (`dates`, already computed above from df["ts"] for the macro/sector/outcome joins).
    # T234-ML-FUND-BROADCAST-LEAKAGE: extended from the original 4 to also cover
    # gross_margin/fcf_yield/short_ratio/short_ratio_delta/short_percent_of_float/
    # price_to_book/peg_ratio/debt_to_equity/ddm_discount/piotroski_score — these were
    # previously broadcast unconditionally from today's fundamentals (see the loop above),
    # which is lookahead bias for any historical training row. fundamentals_snapshot only
    # started capturing these columns going forward (see scheduler.py::_snapshot_fundamentals),
    # so rows before that date correctly fall back to NaN here rather than a leaky broadcast
    # value — NaN is XGBoost-safe, a leaky value is not.
    _PIT_COLS = [
        "revenue_growth", "earnings_growth", "return_on_equity", "recommendation_mean",
        "gross_margin", "fcf_yield", "short_ratio", "short_ratio_delta",
        "short_percent_of_float", "price_to_book", "peg_ratio", "debt_to_equity",
        "ddm_discount", "piotroski_score",
    ]
    if not inference_mode and fund_snapshots:
        try:
            _snap_df = pd.DataFrame(fund_snapshots)
            _snap_df["snapshot_date"] = pd.to_datetime(_snap_df["snapshot_date"])
            _snap_df = _snap_df.sort_values("snapshot_date").reset_index(drop=True)
            _price_dates = pd.to_datetime(dates).rename("date")
            _left = pd.DataFrame({"date": _price_dates}, index=out.index)
            _merged = pd.merge_asof(
                _left.reset_index(),
                _snap_df[["snapshot_date"] + [c for c in _PIT_COLS if c in _snap_df.columns]],
                left_on="date",
                right_on="snapshot_date",
                direction="backward",
            )
            _merged = _merged.set_index("index")
            _pit_cols_present = [c for c in _PIT_COLS if c in _merged.columns]
            if _pit_cols_present and _merged[_pit_cols_present].notna().any().any():
                for col in _pit_cols_present:
                    out[col] = _merged[col].values
            else:
                log.warning("builder.pit_join_all_nan", symbol=(fund_data or {}).get("_symbol"))
        except Exception as _pit_exc:
            log.warning("builder.pit_join_failed", error=str(_pit_exc))
            # fall through — broadcast values remain (training still usable, just biased)

    # --- Target ---
    fwd_ret = c.shift(-horizon) / c - 1
    # After dead-zone filtering (below), only |fwd_ret| >= threshold rows remain,
    # so using > 0 cleanly separates up from down moves for all surviving rows.
    y_dir = (fwd_ret > 0).astype(int)  # 1 = up, 0 = down (dead-zone rows excluded by mask)

    X = out[FEATURE_COLUMNS].replace([np.inf, -np.inf], np.nan)

    # Fundamental, weekly, sector, and signal outcome columns are NaN-allowed — XGBoost handles natively.
    # Outcome columns are NaN until enough signal history accumulates (min 5 per window).
    # Require only the core daily features to be non-null so rows aren't discarded.
    _nan_ok = set(FUNDAMENTAL_COLUMNS) | set(WEEKLY_COLUMNS) | set(SECTOR_COLUMNS) | set(OUTCOME_COLUMNS)
    _required = [c for c in FEATURE_COLUMNS if c not in _nan_ok]

    if inference_mode:
        # Keep all rows with valid features; label/dead-zone filtering skipped
        mask = X[_required].notna().all(axis=1)
    else:
        # Training: exclude dead-zone rows (|fwd_ret| < threshold) — only clear signals
        outside_deadzone = fwd_ret.abs() >= label_threshold
        mask = X[_required].notna().all(axis=1) & fwd_ret.notna() & outside_deadzone

    return X[mask], y_dir[mask], fwd_ret[mask]
