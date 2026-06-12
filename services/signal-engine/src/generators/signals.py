"""Signal generator — fuses TA indicators, ML probability, volume into a
BUY/SELL/HOLD call with an AI Confidence Score (0-100).

Inputs come from other microservices over HTTP. Each source is optional —
if the ML service is unreachable we fall back to TA-only signals.

Accuracy improvements (v2):
  - Stochastic RSI (%K/%D): catches oversold entries that raw RSI misses
  - RSI divergence: penalises price-up / RSI-down (fading momentum)
  - Market regime filter: raises BUY threshold in S&P 500 bear markets
  - MACD zero-line crossover: extra credit for trend-direction confirmation
  - Tighter RSI scoring: RSI 45-65 = full credit, flanks = partial
  - Death cross exposed in reasons for UI/email display

Accuracy improvements (v3):
  - Multi-timeframe confirmation: weekly TA alignment boosts/compresses signal
  - Rolling 20-day VWAP: price above VWAP = institutional support
  - Earnings proximity penalty: compresses signal when earnings < 10 days away
  - Chart pattern fusion: bull_flag/cup_and_handle/double_bottom boost signal;
    head_and_shoulders/double_top/bear_flag reduce it

Accuracy improvements (v4):
  - ML probability soft-cap [0.05, 0.95]: prevents XGBoost overconfidence (e.g. 100%)
    from dominating the fused signal when TA tells a different story
  - ML-TA disagreement dampening: when |ml_prob - ta_prob| > 0.25, ML weight is
    scaled back so strong TA evidence isn't simply overridden
  - ADX choppy market compression: ADX < 20 (directionless market) compresses
    the signal 10% toward neutral, reducing false BUY/SELL in range-bound stocks
  - 4-state regime detection: bull / high_vol (fear&greed < 30) / bear / unknown;
    high_vol raises the BUY threshold to 0.70 and compresses all signals 15%

Accuracy improvements (v5):
  - Market breadth: % of US universe stocks above their 200-day SMA, fetched from
    market-data and cached in Redis (4 h). When breadth < 40% (most stocks below
    their long-term trend line) during a nominally-bull regime, the signal is
    additionally compressed 10% toward neutral. A BUY signal in a market where
    most stocks are below their 200MA carries much higher false-positive risk
    regardless of the S&P 500 price. Breadth stored in reasons as breadth_pct.

Signal accuracy improvements (SA-1 through SA-7):
  SA-1: ML/TA disagreement dampening band lowered (0.25–0.35 = 25% weight cut)
  SA-2: Style-aware ML precision targets (SHORT=70%, SWING=60%, LONG=50%)
  SA-3: 4 macro regime boolean features added to ML feature set
  SA-4: Weekly alignment min bars 26→15 with partial confidence scaling
  SA-5: Data-driven TA weights via ta_weights.json (POST /signals/calibrate_ta_weights)
  SA-6: Filter interaction audit endpoint (GET /signals/filter_audit)
  SA-7: Regime-aware earnings compression — bull+beater≥70%: skip, +3% boost; bull+50-70%: halve; bear/hv: tighten×0.85
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from common.config import get_settings
from common.logging import get_logger

log = get_logger("signal-generator")
_settings = get_settings()

# ── TA component weights (SA-5) ───────────────────────────────────────────────
# Defaults are the hand-tuned values used since launch.
# Run POST /signals/calibrate_ta_weights to compute logistic-regression-derived
# weights from your actual price history and save them here.
# Convention: penalty keys end in "_penalty" and store positive magnitudes.
# The score section subtracts penalty weights; normalisation excludes them.
_TA_WEIGHTS_DEFAULT: dict[str, float] = {
    "above_sma50":              0.15,
    "sma50_above_sma200":       0.10,
    "golden_cross_event":       0.10,
    "death_cross_penalty":      0.10,
    "rsi_sweet_spot":           0.15,
    "rsi_mild_oversold":        0.08,
    "rsi_mild_overbought":      0.06,
    "stoch_oversold":           0.10,
    "stoch_overbought_penalty": 0.08,
    "stoch_cross_up":           0.05,
    "rsi_divergence_bearish_penalty": 0.10,
    "rsi_divergence_bullish":   0.08,
    "macd_strong":              0.15,
    "macd_positive":            0.08,
    "macd_zero_cross_up":       0.05,
    "bb_mid_zone":              0.10,
    "price_above_vwap":         0.08,
    "price_below_vwap_penalty": 0.05,
    "bullish_trend":            0.10,
    "obv_bullish":              0.10,
    "volume_surge":             0.05,
}
_TA_WEIGHTS_PATH = Path(_settings.model_dir) / "ta_weights.json"
_ML_WEIGHT_OVERRIDE_PATH = Path(_settings.model_dir) / "ml_weight_override.json"
_CONVICTION_WEIGHTS_PATH = Path(_settings.model_dir) / "conviction_weights.json"

# Global ML weight cap override: None means use the per-style profile default.
# Set by calibrate_ml_weight(); loaded at import time from disk if present.
_ml_weight_global_cap: float | None = None


def _load_ml_weight_override() -> float | None:
    """Load calibrated ML weight cap from disk, or return None (use profile default)."""
    try:
        if _ML_WEIGHT_OVERRIDE_PATH.exists():
            with open(_ML_WEIGHT_OVERRIDE_PATH) as f:
                d = json.load(f)
            v = d.get("ml_weight_cap")
            if isinstance(v, (int, float)) and 0.0 <= v <= 1.0:
                return float(v)
    except Exception:
        pass
    return None


def set_ml_weight_global_cap(cap: float | None) -> None:
    """Update the in-process ML weight cap override and persist to disk."""
    global _ml_weight_global_cap
    _ml_weight_global_cap = cap
    try:
        Path(_ML_WEIGHT_OVERRIDE_PATH).parent.mkdir(parents=True, exist_ok=True)
        if cap is None:
            Path(_ML_WEIGHT_OVERRIDE_PATH).unlink(missing_ok=True)
        else:
            Path(_ML_WEIGHT_OVERRIDE_PATH).write_text(json.dumps({"ml_weight_cap": cap}, indent=2))
    except Exception:
        pass


# Load on module import
_ml_weight_global_cap = _load_ml_weight_override()


def _load_ta_weights() -> dict[str, float]:
    """Load calibrated TA weights from disk, falling back to defaults."""
    try:
        if _TA_WEIGHTS_PATH.exists():
            with open(_TA_WEIGHTS_PATH) as f:
                saved = json.load(f)
            # Merge: saved values override defaults; new keys in defaults keep their value
            return {**_TA_WEIGHTS_DEFAULT, **saved}
    except Exception:
        pass
    return dict(_TA_WEIGHTS_DEFAULT)


def load_conviction_weights() -> dict[str, float]:
    """Load calibrated conviction layer weights from disk (AL-3).

    Returns a dict of {reason_flag: accuracy_vs_baseline} where values > 0 mean the
    flag is more common in winning trades.  Returns empty dict if not yet calibrated.
    """
    try:
        if _CONVICTION_WEIGHTS_PATH.exists():
            with open(_CONVICTION_WEIGHTS_PATH) as f:
                return json.load(f).get("edge_pct", {})
    except Exception:
        pass
    return {}


@dataclass
class AIConfidence:
    signal: str           # BUY / SELL / HOLD
    horizon: str          # SHORT / SWING / LONG
    confidence: float     # 0-100
    bullish_probability: float  # 0-1
    reasons: dict


def _fetch_prices(symbol: str) -> pd.DataFrame:
    url = f"{_settings.market_data_url}/stocks/{symbol}/prices?timeframe=1d&limit=400"
    with httpx.Client(timeout=15) as c:
        r = c.get(url)
        r.raise_for_status()
    data = r.json()
    return pd.DataFrame(data)


def _fetch_weekly_prices(symbol: str) -> pd.DataFrame:
    try:
        url = f"{_settings.market_data_url}/stocks/{symbol}/prices?timeframe=1w&limit=100"
        with httpx.Client(timeout=10) as c:
            r = c.get(url)
            if r.status_code == 200:
                return pd.DataFrame(r.json())
    except Exception as exc:
        log.debug("weekly_prices.fetch_failed", symbol=symbol, error=str(exc))
    return pd.DataFrame()


def _fetch_ml_data(symbol: str) -> tuple[float | None, float, dict]:
    """Return (bullish_probability, test_auc, ml_meta).

    SA-8: tries the 3-model ensemble (XGBoost+LightGBM+RF) first, then 2-model,
    then XGBoost-only. ml_meta carries per-model probabilities and agreement status
    for storage in Signal.reasons.

    test_auc drives the dynamic ML/TA fusion weight — a high-quality model (AUC 0.70)
    earns up to 75% weight; a near-random model (AUC < 0.52) gets 0% weight.
    """
    payload = {"symbol": symbol}
    endpoints = [
        ("/ml/predict_ensemble_three", payload),
        ("/ml/predict_ensemble",       payload),
        ("/ml/predict",                {**payload, "model": "xgboost"}),
    ]
    for endpoint, body in endpoints:
        try:
            with httpx.Client(timeout=10) as c:
                r = c.post(f"{_settings.ml_prediction_url}{endpoint}", json=body)
                if r.status_code == 200:
                    data = r.json()
                    prob = float(data.get("bullish_probability", 0.5))
                    m = data.get("metrics") or {}
                    test_auc = float(m.get("test_auc_mean") or m.get("auc") or m.get("cv_auc_mean") or 0.55)
                    ml_meta = {
                        "ml_model": data.get("model", "xgboost"),
                        "ml_agreement": data.get("ensemble_agreement"),
                        "ml_model_probs": data.get("model_probabilities"),
                        "ml_oos_suppressed": bool(data.get("oos_suppressed", False)),
                    }
                    return prob, test_auc, ml_meta
        except Exception as exc:
            log.warning("ml.fetch_failed", symbol=symbol, endpoint=endpoint, error=str(exc))
    return None, 0.55, {}


def _fetch_market_regime() -> tuple[str, float | None]:
    """Returns (regime, fear_greed_score).

    Regime is one of: 'bull', 'high_vol', 'bear', 'unknown'.
    - 'bear'     : S&P 500 below its 200-day MA
    - 'high_vol' : S&P 500 in bull territory but Fear & Greed score < 30
                   (market stress despite price holding — elevated crash risk)
    - 'bull'     : S&P 500 above 200-day MA, fear & greed >= 30
    """
    try:
        with httpx.Client(timeout=5) as c:
            r = c.get(f"{_settings.market_data_url}/stocks/fear_greed")
            if r.status_code == 200:
                data = r.json()
                sp500_regime = data.get("sp500_regime", "unknown")
                fg_score = data.get("score")
                if sp500_regime == "bear":
                    return "bear", fg_score
                if fg_score is not None and fg_score < 30:
                    return "high_vol", fg_score
                return "bull", fg_score
    except Exception:
        pass
    return "unknown", None


def _fetch_market_breadth() -> float | None:
    """Return % of US stocks above their 200-day SMA, or None on failure."""
    try:
        with httpx.Client(timeout=5) as c:
            r = c.get(f"{_settings.market_data_url}/stocks/market_breadth")
            if r.status_code == 200:
                return r.json().get("breadth_pct")
    except Exception:
        pass
    return None


def _fetch_earnings_proximity(symbol: str) -> int | None:
    """Return days_to_earnings, or None if unavailable."""
    try:
        url = f"{_settings.market_data_url}/stocks/{symbol}/fundamentals"
        with httpx.Client(timeout=8) as c:
            r = c.get(url)
            if r.status_code == 200:
                return r.json().get("days_to_earnings")
    except Exception:
        pass
    return None


def _fetch_earnings_beat_rate(symbol: str) -> float | None:
    """Return historical EPS beat rate (0-1) from market-data fundamentals.

    The market-data service computes eps_beat_rate from the last 8 quarters of
    earnings surprises and caches it. This avoids needing yfinance in signal-engine.

    Returns None on failure (treated as neutral — no beat-rate compression adjustment).
    """
    try:
        url = f"{_settings.market_data_url}/stocks/{symbol}/fundamentals"
        with httpx.Client(timeout=8) as c:
            r = c.get(url)
            if r.status_code == 200:
                val = r.json().get("eps_beat_rate")
                return float(val) if val is not None else None
    except Exception:
        pass
    return None


def _fetch_relative_strength(symbol: str) -> tuple[float | None, float | None, bool | None, float | None]:
    """Return (rs_score 0-100, rs_rank, sector_etf_above_sma50, stock_20d_ret) vs the stock's sector ETF.

    Fetches the stock's sector from market-data, maps to the matching SPDR ETF
    (or ^HSI for HK stocks), then computes the 20-day return ratio and whether
    the ETF is currently above its 50-day SMA (SA-16 sector trend filter).
    Returns (None, None, None, None) on any failure.
    """
    _SECTOR_ETF = {
        "Technology": "XLK", "Health Care": "XLV", "Healthcare": "XLV",
        "Financials": "XLF", "Financial Services": "XLF",
        "Consumer Discretionary": "XLY", "Consumer Staples": "XLP",
        "Energy": "XLE", "Utilities": "XLU", "Materials": "XLB",
        "Industrials": "XLI", "Real Estate": "XLRE",
        "Communication Services": "XLC", "Telecommunications": "XLC",
    }
    try:
        import yfinance as yf

        url = f"{_settings.market_data_url}/stocks/{symbol}"
        with httpx.Client(timeout=8) as c:
            r = c.get(url)
            if r.status_code != 200:
                return None, None, None, None
        info = r.json()
        market = info.get("market", "US")
        sector = info.get("sector") or ""
        etf_ticker = "^HSI" if str(market).upper() == "HK" else _SECTOR_ETF.get(sector, "SPY")

        stock_hist = yf.Ticker(symbol).history(period="3mo")
        etf_hist   = yf.Ticker(etf_ticker).history(period="3mo")
        if stock_hist.empty or len(stock_hist) < 21:
            return None, None, None, None
        if etf_hist.empty or len(etf_hist) < 21:
            return None, None, None, None

        stock_ret = float(stock_hist["Close"].iloc[-1] / stock_hist["Close"].iloc[-21] - 1)
        etf_ret   = float(etf_hist["Close"].iloc[-1] / etf_hist["Close"].iloc[-21] - 1)
        if abs(1 + etf_ret) < 0.01:
            return None, None, None, None
        rs_rank = (1 + stock_ret) / (1 + etf_ret)
        rs_score = float(np.clip(50 + (rs_rank - 1.0) * 100, 0, 100))
        # SA-23: store absolute stock 20d return in reasons for floor check in compression gate
        reasons_stock_ret = round(stock_ret * 100, 2)  # stored in reasons as stock_20d_return_pct

        # SA-16: sector ETF trend — above 50-day SMA means sector is in an uptrend
        etf_close = etf_hist["Close"]
        etf_sma50 = etf_close.rolling(50).mean().iloc[-1] if len(etf_close) >= 50 else None
        sector_etf_above_sma50: bool | None = None
        if etf_sma50 is not None and not pd.isna(etf_sma50):
            sector_etf_above_sma50 = bool(etf_close.iloc[-1] > etf_sma50)

        return round(rs_score, 1), round(rs_rank, 4), sector_etf_above_sma50, round(stock_ret, 4)
    except Exception:
        return None, None, None, None


def _fetch_news_sentiment(symbol: str) -> float | None:
    """Return aggregate news sentiment score (0-100, 50=neutral).

    Calls the dedicated /news/sentiment endpoint which uses Claude Haiku
    (when ANTHROPIC_API_KEY is configured in market-data) or enhanced VADER
    with financial-domain lexicon corrections as fallback.
    """
    try:
        url = f"{_settings.market_data_url}/stocks/{symbol}/news/sentiment"
        with httpx.Client(timeout=10) as c:
            r = c.get(url)
            if r.status_code == 200:
                val = r.json().get("score")
                return float(val) if val is not None else None
    except Exception:
        pass
    return None


def _fetch_options_flow(symbol: str) -> tuple[str | None, float | None]:
    """Return (sentiment, cp_ratio) from the options-flow endpoint.

    sentiment: 'strongly_bullish' | 'bullish' | 'neutral' | 'slightly_bearish' | 'bearish'
    cp_ratio:  call volume / put volume for the nearest two expiries
    Returns (None, None) for HK stocks, unavailable symbols, or endpoint errors.
    """
    try:
        url = f"{_settings.market_data_url}/stocks/{symbol}/options-flow"
        with httpx.Client(timeout=10) as c:
            r = c.get(url)
            if r.status_code != 200:
                return None, None
        data = r.json()
        if not data.get("available"):
            return None, None
        return data.get("sentiment"), data.get("cp_ratio")
    except Exception:
        return None, None


def _fetch_patterns_from_ta(symbol: str) -> list[dict]:
    """Fetch recent chart patterns from the TA service."""
    try:
        url = f"{_settings.technical_analysis_url}/ta/{symbol}/patterns"
        with httpx.Client(timeout=8) as c:
            r = c.get(url)
            if r.status_code == 200:
                return r.json().get("patterns", [])
    except Exception:
        pass
    return []


def _adx(df: pd.DataFrame, period: int = 14) -> tuple[float, float, float]:
    """Return (ADX, +DI, -DI). ADX > 25 = trending, > 40 = strong trend."""
    high = df["high"].astype(float)
    low  = df["low"].astype(float)
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
    adx = dx.ewm(alpha=1 / period, adjust=False).mean()

    # C3 FIX: return None (not 20.0) when ADX is NaN. A 20.0 fallback silently passed
    # adx_min=25 compression check on all short-history stocks (20 < 25 → always compressed),
    # while also never granting bullish_trend (adx > 25 → never True). Return None so
    # downstream callers can explicitly skip ADX-gated logic rather than silently misfiring.
    return (
        float(adx.iloc[-1])      if not pd.isna(adx.iloc[-1])      else None,
        float(di_plus.iloc[-1])  if not pd.isna(di_plus.iloc[-1])  else 0.0,
        float(di_minus.iloc[-1]) if not pd.isna(di_minus.iloc[-1]) else 0.0,
    )


def _stoch_rsi(rsi: pd.Series, period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> tuple[float, float, pd.Series]:
    """Stochastic RSI — normalises RSI into 0-1 range, then smooths.

    Returns (%K scalar, %D scalar, k_series) where:
      < 0.20 = oversold  (potential buy zone)
      > 0.80 = overbought (potential sell zone)
    k_series is returned so callers can reuse it (e.g. for cross-up detection)
    without recomputing.
    """
    rsi_min = rsi.rolling(period).min()
    rsi_max = rsi.rolling(period).max()
    rng = rsi_max - rsi_min
    raw_k = (rsi - rsi_min) / rng.replace(0, np.nan)
    k = raw_k.rolling(smooth_k).mean()
    d = k.rolling(smooth_d).mean()
    k_val = float(k.iloc[-1]) if not pd.isna(k.iloc[-1]) else 0.5
    d_val = float(d.iloc[-1]) if not pd.isna(d.iloc[-1]) else 0.5
    return k_val, d_val, k


def _resample_to_weekly(df: pd.DataFrame) -> pd.DataFrame:
    """Resample a daily OHLCV DataFrame to weekly bars (Monday-anchored).

    Uses the 'ts' column as the date index. Returns empty DataFrame if
    fewer than 10 weekly bars can be formed (not enough history).
    """
    if df.empty or len(df) < 10:
        return pd.DataFrame()
    try:
        d = df.copy()
        d["ts"] = pd.to_datetime(d["ts"])
        d = d.set_index("ts").sort_index()
        weekly = d.resample("W-MON", label="left", closed="left").agg({
            "open":   "first",
            "high":   "max",
            "low":    "min",
            "close":  "last",
            "volume": "sum",
        }).dropna(subset=["close"])
        weekly = weekly.reset_index()
        weekly.rename(columns={"ts": "ts"}, inplace=True)
        # Drop the current (latest) week if it started within the past 4 days —
        # a partial week bar has too few sessions to be a reliable signal.
        if not weekly.empty:
            from datetime import date as _date, timedelta as _td
            latest_week_start = pd.to_datetime(weekly["ts"].iloc[-1]).date()
            if (_date.today() - latest_week_start) < _td(days=4):
                weekly = weekly.iloc[:-1]
        return weekly if len(weekly) >= 10 else pd.DataFrame()
    except Exception:
        return pd.DataFrame()


def _weekly_technicals(df: pd.DataFrame) -> dict:
    """Weekly TA indicators for multi-timeframe confirmation.

    Returns dict with weekly_rsi (float|None), weekly_trend ('up'|'down'|'neutral'),
    weekly_macd_bull (bool), and weekly_score (float 0-1 composite).
    Uses 10-week SMA for trend (vs 20-week previously) — more responsive to medium-term turns.
    """
    _neutral: dict = {
        "weekly_rsi": None,
        "weekly_trend": "neutral",
        "weekly_macd_bull": False,
        "weekly_score": 0.5,
        "weekly_confidence": 0.0,  # 0.0 = no weekly data; alignment filter is skipped
    }
    if df.empty or len(df) < 15:
        return _neutral
    # Partial confidence for 15–25 bars (3–6 months): scales from 0.70→1.0 linearly.
    # Full confidence (1.0) requires 26+ bars (6 months).
    weekly_confidence = min(1.0, 0.70 + (len(df) - 15) / (26 - 15) * 0.30) if len(df) < 26 else 1.0
    close = df["close"].astype(float)

    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + g / l.replace(0, np.nan))
    rsi_val = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else None

    sma10 = close.rolling(10).mean()
    weekly_trend = "neutral"
    if not pd.isna(sma10.iloc[-1]):
        pct = (close.iloc[-1] - float(sma10.iloc[-1])) / float(sma10.iloc[-1])
        if pct > 0.01:
            weekly_trend = "up"
        elif pct < -0.01:
            weekly_trend = "down"

    macd_line = close.ewm(span=12).mean() - close.ewm(span=26).mean()
    hist = macd_line - macd_line.ewm(span=9).mean()
    macd_positive = bool(hist.iloc[-1] > 0)
    macd_rising = bool(hist.iloc[-1] > hist.iloc[-2]) if len(hist) >= 2 else False
    weekly_macd_bull = macd_positive and macd_rising

    score = 0.35
    if rsi_val is not None:
        if 40 < rsi_val < 68:
            score += 0.20
        elif rsi_val <= 40:
            score += 0.10
    if weekly_trend == "up":
        score += 0.25
    if macd_positive and macd_rising:
        score += 0.20
    elif macd_positive:
        score += 0.10

    return {
        "weekly_rsi": round(rsi_val, 1) if rsi_val is not None else None,
        "weekly_trend": weekly_trend,
        "weekly_macd_bull": weekly_macd_bull,
        "weekly_score": float(np.clip(score, 0, 1)),
        "weekly_confidence": weekly_confidence,
    }


def _sr_context(df: pd.DataFrame) -> dict:
    """Detect price position relative to key support/resistance levels.

    Uses swing high/low pivots from the last 60 bars plus 52-week high/low.
    Returns sr_context: 'breakout' | 'at_resistance' | 'at_support' | 'neutral'.
    """
    close = df["close"].astype(float)
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    current = float(close.iloc[-1])
    prev    = float(close.iloc[-2]) if len(close) >= 2 else current

    # 52-week high/low from historical bars (excluding today to avoid look-ahead)
    hist_len = min(252, len(close) - 1)
    hist_high = float(high.iloc[-hist_len - 1:-1].max()) if hist_len > 0 else float(high.max())
    hist_low  = float(low.iloc[-hist_len - 1:-1].min())  if hist_len > 0 else float(low.min())

    # Swing pivot detection on last 60 bars (local max/min with 3-bar window each side)
    n = min(60, len(high))
    pw = 3
    r_h = high.iloc[-n:].reset_index(drop=True)
    r_l = low.iloc[-n:].reset_index(drop=True)

    resistances: list[float] = [hist_high]
    supports: list[float] = []
    for i in range(pw, len(r_h) - pw):
        val = float(r_h.iloc[i])
        if all(val >= float(r_h.iloc[i + j]) for j in range(-pw, pw + 1) if j != 0):
            resistances.append(val)
    for i in range(pw, len(r_l) - pw):
        val = float(r_l.iloc[i])
        if all(val <= float(r_l.iloc[i + j]) for j in range(-pw, pw + 1) if j != 0):
            supports.append(val)

    nearest_res = min((r for r in resistances if r > current), default=None)
    nearest_sup = max((s for s in supports if s < current), default=None)

    thr = 0.015  # 1.5% proximity threshold
    sr_context = "neutral"

    if nearest_res is not None:
        if current >= nearest_res:
            # Price already cleared the level — always a breakout
            sr_context = "breakout"
        else:
            dist = (nearest_res - current) / nearest_res
            if dist <= thr:
                # Approaching resistance: breakout if prev bar was clearly below
                if prev < nearest_res * (1.0 - thr):
                    sr_context = "breakout"
                else:
                    sr_context = "at_resistance"
    if sr_context == "neutral" and nearest_sup is not None:
        dist = (current - nearest_sup) / current
        if dist <= thr:
            sr_context = "at_support"

    return {
        "sr_context": sr_context,
        "sr_nearest_resistance": round(nearest_res, 2) if nearest_res is not None else None,
        "sr_nearest_support": round(nearest_sup, 2) if nearest_sup is not None else None,
        "sr_52w_high": round(hist_high, 2),
        "sr_52w_low": round(hist_low, 2),
    }


def _pullback_recovery(df: pd.DataFrame) -> tuple[float, dict]:
    """SA-14: Detect healthy pullback + recovery patterns.

    A bullish pullback-recovery requires:
      1. Price pulled back 5–25 % below its 20-day high (healthy dip, not broken).
      2. At least 2 consecutive green closes (recovery momentum confirmed).
      3. Volume on the most recent bar ≥ 110 % of 20-day average (conviction).

    Returns (score_delta, reasons_dict). Delta is 0.04–0.07 added to the
    normalised TA score when all conditions are met.
    """
    close  = df["close"].astype(float)
    volume = df["volume"].astype(float)
    reasons: dict = {}

    if len(close) < 22:
        reasons["pullback_recovery"] = None
        return 0.0, reasons

    # Use yesterday's rolling high to avoid look-ahead on today's bar
    high_20d = close.iloc[:-1].rolling(20).max().iloc[-1]
    current  = close.iloc[-1]

    if pd.isna(high_20d) or high_20d <= 0:
        reasons["pullback_recovery"] = None
        return 0.0, reasons

    pullback_pct = float((high_20d - current) / high_20d)
    reasons["pullback_depth_pct"] = round(pullback_pct * 100, 1)

    # Condition 1: meaningful pullback (5–25 %)
    if not (0.05 <= pullback_pct <= 0.25):
        reasons["pullback_recovery"] = None
        return 0.0, reasons

    # Condition 2: 2+ consecutive green closes
    consecutive_green = 0
    for i in range(-1, -5, -1):
        try:
            if close.iloc[i] > close.iloc[i - 1]:
                consecutive_green += 1
            else:
                break
        except IndexError:
            break

    reasons["pullback_green_days"] = consecutive_green

    if consecutive_green < 2:
        reasons["pullback_recovery"] = "no_recovery_yet"
        return 0.0, reasons

    # Condition 3: volume expansion on recovery
    vol_avg = volume.rolling(20).mean().iloc[-1]
    vol_confirms = bool(
        not pd.isna(vol_avg) and vol_avg > 0 and volume.iloc[-1] > vol_avg * 1.10
    )
    reasons["pullback_vol_confirms"] = vol_confirms

    delta = 0.07 if vol_confirms else 0.04
    reasons["pullback_recovery"] = "confirmed_vol" if vol_confirms else "confirmed"
    return delta, reasons


def _pattern_score_adjustment(patterns: list[dict], df_len: int) -> tuple[float, list[str]]:
    """Returns (probability adjustment, list of active pattern names).

    Adjustment is in range -0.15 to +0.15. Recency decays patterns older
    than 20 bars to zero.
    """
    BULLISH = {"double_bottom", "ascending_triangle", "bull_flag", "cup_and_handle"}
    BEARISH = {"head_and_shoulders", "double_top", "descending_triangle", "bear_flag"}

    adj = 0.0
    active: list[str] = []
    for p in patterns:
        end_idx = p.get("end_idx", 0)
        confidence = float(p.get("confidence", 0.5))
        recency = max(0.0, 1.0 - (df_len - 1 - end_idx) / 20.0)
        if recency < 0.1:
            continue
        name = p.get("name", "")
        meta = p.get("meta", {})
        if name in BULLISH:
            base = 0.08
            # double_bottom with confirmed neckline break + volume = much stronger signal
            if name == "double_bottom" and meta.get("neckline_broken") and meta.get("vol_confirmed"):
                base = 0.15  # neckline breakout on volume = highest-conviction reversal
            elif name == "double_bottom" and meta.get("neckline_broken"):
                base = 0.12
            adj += base * confidence * recency
            active.append(name)
        elif name in BEARISH:
            base = 0.08
            # double_top with confirmed neckline break = strong suppression signal
            if name == "double_top" and meta.get("neckline_broken"):
                base = 0.13
            adj -= base * confidence * recency
            active.append(name)

    return float(np.clip(adj, -0.15, 0.15)), active


def _ta_score(df: pd.DataFrame) -> tuple[float, dict]:
    close  = df["close"].astype(float)
    volume = df["volume"].astype(float)
    reasons: dict = {}

    # ── Trend: SMA50 / SMA200 ─────────────────────────────────────────────
    sma50_s  = close.rolling(50).mean()
    sma200_s = close.rolling(200).mean()
    sma50  = sma50_s.iloc[-1]
    sma200 = sma200_s.iloc[-1]

    above_sma50        = bool(close.iloc[-1] > sma50)
    sma50_above_sma200 = bool(sma50 > sma200)

    golden_cross_event = False
    death_cross_event  = False
    if len(sma50_s.dropna()) >= 2 and len(sma200_s.dropna()) >= 2:
        prev50, prev200 = sma50_s.iloc[-2], sma200_s.iloc[-2]
        golden_cross_event = bool(prev50 <= prev200 and sma50 > sma200)
        death_cross_event  = bool(prev50 >= prev200 and sma50 < sma200)

    reasons["trend_above_sma50"]    = above_sma50
    reasons["sma50_above_sma200"]   = sma50_above_sma200
    reasons["golden_cross_event"]   = golden_cross_event
    reasons["death_cross_event"]    = death_cross_event

    # ── RSI (full series — needed for StochRSI and divergence) ────────────
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + g / l.replace(0, np.nan))
    rsi_val = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else None
    reasons["rsi"] = rsi_val

    # ── Stochastic RSI (%K, %D) ───────────────────────────────────────────
    stoch_k, stoch_d, k_smooth = _stoch_rsi(rsi)
    stoch_oversold   = stoch_k < 0.20
    stoch_overbought = stoch_k > 0.80
    stoch_cross_up = False
    if len(k_smooth.dropna()) >= 2:
        stoch_cross_up = bool(k_smooth.iloc[-1] > 0.20 and k_smooth.iloc[-2] <= 0.20)

    reasons["stoch_rsi_k"]          = round(stoch_k, 3)
    reasons["stoch_rsi_d"]          = round(stoch_d, 3)
    reasons["stoch_rsi_oversold"]   = stoch_oversold
    reasons["stoch_rsi_overbought"] = stoch_overbought
    reasons["stoch_rsi_cross_up"]   = stoch_cross_up

    # ── RSI divergence (peak-to-peak, 20-bar window) ─────────────────────
    # H3/H4 DISABLED: RSI divergence was comparing peak *timing* (argmax index position)
    # rather than peak *levels*. A stock ripping to new highs with rising RSI (healthy
    # momentum) was mislabeled "bearish" simply because price argmax landed later than RSI
    # argmax in the window. The volume-inversion (0.5× on high-volume "confirmation") is
    # also unbacktested and contested. Both the penalty and the bullish bonus are zeroed
    # until the logic is rewritten to compare RSI values at the two price peaks.
    rsi_divergence = "none"
    reasons["rsi_divergence"] = rsi_divergence

    # ── MACD histogram + zero-line crossover ──────────────────────────────
    macd_line = close.ewm(span=12).mean() - close.ewm(span=26).mean()
    hist = macd_line - macd_line.ewm(span=9).mean()
    macd_hist  = float(hist.iloc[-1])
    macd_rising = bool(hist.iloc[-1] > hist.iloc[-2]) if len(hist) >= 2 else False
    macd_zero_cross_up = False
    if len(macd_line.dropna()) >= 2:
        macd_zero_cross_up = bool(macd_line.iloc[-1] > 0 and macd_line.iloc[-2] <= 0)
    reasons["macd_hist"]          = macd_hist
    reasons["macd_rising"]        = macd_rising
    reasons["macd_zero_cross_up"] = macd_zero_cross_up

    # ── Bollinger Bands %B ────────────────────────────────────────────────
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20
    band_width = bb_upper.iloc[-1] - bb_lower.iloc[-1]
    bb_pct_b = float((close.iloc[-1] - bb_lower.iloc[-1]) / band_width) if band_width > 0 else 0.5
    reasons["bb_pct_b"] = round(bb_pct_b, 3)

    # ── Rolling 20-day VWAP ───────────────────────────────────────────────
    typical_price = (df["high"].astype(float) + df["low"].astype(float) + close) / 3
    vwap_20 = (typical_price * volume).rolling(20).sum() / volume.rolling(20).sum()
    vwap_val = vwap_20.iloc[-1]
    price_above_vwap: bool | None = None
    if not pd.isna(vwap_val) and vwap_val > 0:
        price_above_vwap = bool(close.iloc[-1] > vwap_val)
    reasons["price_above_vwap"] = price_above_vwap
    reasons["vwap_20"] = float(vwap_val) if not pd.isna(vwap_val) else None

    # ── ADX — trend strength ──────────────────────────────────────────────
    adx_val, di_plus, di_minus = _adx(df)
    # C3 FIX: adx_val is now None when insufficient data — guard all comparisons
    trending      = (adx_val is not None) and adx_val > 25
    bullish_trend = trending and di_plus > di_minus
    reasons["adx"]          = round(adx_val, 1) if adx_val is not None else None
    reasons["adx_trending"] = trending
    reasons["adx_bullish"]  = bullish_trend

    # ── OBV trend (volume-confirmed direction) ────────────────────────────
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv = (volume * direction).cumsum()
    obv_bullish = bool(obv.rolling(10).mean().iloc[-1] > obv.rolling(30).mean().iloc[-1])
    reasons["obv_bullish"] = obv_bullish

    # ── Volume expansion ──────────────────────────────────────────────────
    vol_std = volume.rolling(20).std().iloc[-1]
    vol_z = (volume.iloc[-1] - volume.rolling(20).mean().iloc[-1]) / vol_std if vol_std and vol_std > 0 else 0.0
    reasons["volume_z"] = float(vol_z) if not pd.isna(vol_z) else None

    # ── Score (data-driven weights via ta_weights.json, SA-5) ─────────────
    w = _load_ta_weights()
    score = 0.0

    # SA-15: volume_z used for confirmation weighting (safe float)
    _vz = float(reasons.get("volume_z") or 0.0)

    if above_sma50:        score += w["above_sma50"]
    if sma50_above_sma200: score += w["sma50_above_sma200"]
    # SA-15: golden cross on shrinking volume is unreliable — half credit
    if golden_cross_event: score += w["golden_cross_event"] * (1.0 if _vz > 0.5 else 0.5)
    if death_cross_event:  score -= w["death_cross_penalty"]

    if rsi_val is not None:
        if 45 < rsi_val < 65:    score += w["rsi_sweet_spot"]
        elif 35 < rsi_val <= 45: score += w["rsi_mild_oversold"]
        elif 65 <= rsi_val < 72: score += w["rsi_mild_overbought"]

    if stoch_oversold:      score += w["stoch_oversold"]
    elif stoch_overbought:  score -= w["stoch_overbought_penalty"]
    if stoch_cross_up:      score += w["stoch_cross_up"]

    # SA-15: divergence weighted by volume confirmation
    if rsi_divergence == "bearish":
        # High volume on a divergence day CONFIRMS price move, making bearish divergence LESS reliable
        # Low/declining volume = divergence is more meaningful → full penalty
        score -= w["rsi_divergence_bearish_penalty"] * (0.5 if _vz > 0.3 else 1.0)
    elif rsi_divergence == "bullish":
        # Bullish divergence requires volume to be credible
        score += w["rsi_divergence_bullish"] * (1.0 if _vz > 0.3 else 0.5)

    if macd_hist > 0 and macd_rising:  score += w["macd_strong"]
    elif macd_hist > 0:                score += w["macd_positive"]
    # SA-17: MACD zero-cross gets full credit only when price is above SMA50
    if macd_zero_cross_up:
        score += w["macd_zero_cross_up"] * (1.0 if above_sma50 else 0.4)

    if 0.2 < bb_pct_b < 0.8:   score += w["bb_mid_zone"]

    if price_above_vwap is True:    score += w["price_above_vwap"]
    elif price_above_vwap is False: score -= w["price_below_vwap_penalty"]

    if bullish_trend:                                       score += w["bullish_trend"]
    if obv_bullish:                                         score += w["obv_bullish"]
    if reasons["volume_z"] and reasons["volume_z"] > 0.5:  score += w["volume_surge"]

    # Normalise by sum of all positive weights so score stays in [0,1].
    _TA_MAX_SCORE = sum(v for k, v in w.items() if not k.endswith("_penalty"))
    if _TA_MAX_SCORE <= 0:
        return 0.5, reasons  # degenerate calibration; return neutral
    base = float(np.clip(score / _TA_MAX_SCORE, 0.0, 1.0))

    # SA-14: pullback + recovery boost applied after normalisation
    pr_delta, pr_reasons = _pullback_recovery(df)
    reasons.update(pr_reasons)

    return float(np.clip(base + pr_delta, 0.0, 1.0)), reasons


# ── Trading Style Profiles ────────────────────────────────────────────────────
# Each profile controls how filters, weights, and thresholds behave for that
# trading horizon. All compression multipliers follow the same convention as the
# rest of this file: fused = 0.5 + (fused - 0.5) × multiplier.
#
# Key design decisions per style:
#   SHORT  — Pure momentum. Earnings = potential catalyst, not risk. No news noise.
#            ML weight capped low (ML targets 20-day returns, not 1-5 day moves).
#            Needs a confirmed trend (ADX > 25) but lighter macro filters.
#   SWING  — Balanced. Fixed: earnings compression was 0.25× (impossible to BUY
#            with earnings in ≤2 days). Now 0.50× — still strong but achievable.
#            Stacked filters capped at 45% total compression.
#   LONG   — Fundamentals (K-Score) boost the signal. Ignores short-term noise
#            (earnings in 10 days, daily news sentiment). Weekly alignment is the
#            most important filter. ML weight capped (20d-trained, less useful).
_STYLE_PROFILES: dict[str, dict] = {
    "SHORT": {
        "ml_weight_cap": 0.30,
        "buy_threshold":  {"bull": 0.60, "high_vol": 0.65, "bear": 0.68, "unknown": 0.62},
        "hold_threshold": {"bull": 0.46, "high_vol": 0.50, "bear": 0.52, "unknown": 0.47},
        "adx_min": 25, "adx_compression": 0.85,
        "high_vol_compression": 0.92,
        "breadth_compression": None,
        "weekly_boost": 1.08, "weekly_compress": 0.93,
        "earnings_compression": None,
        "news_compression": None,
        "rs_compression": 0.90,
        "kscore_boost": False,
        "max_compress_ratio": 0.70,
    },
    "SWING": {
        "ml_weight_cap": 0.75,
        # SA-12: keep bull/neutral thresholds unchanged; only tighten bear/high_vol.
        # fused_prob ≈ 0.75×ml + 0.25×ta; 0.72 ≈ ML>0.78 target for stressed regimes.
        "buy_threshold":  {"bull": 0.62, "high_vol": 0.72, "bear": 0.72, "unknown": 0.62},
        "hold_threshold": {"bull": 0.50, "high_vol": 0.54, "bear": 0.56, "unknown": 0.50},
        "adx_min": 15, "adx_compression": 0.90,
        "high_vol_compression": 0.85,
        "breadth_compression": 0.90,
        "weekly_boost": 1.12, "weekly_compress": 0.85,
        # Fixed: was {2: 0.25, 5: 0.55, 10: 0.80}. The 0.25× meant a stock needed
        # fused_prob ≈ 1.10 to fire a BUY with earnings in ≤2 days — impossible.
        "earnings_compression": {2: 0.50, 5: 0.75, 10: 0.90},
        "news_compression": {25: 0.75, 35: 0.85},
        "rs_compression": 0.85,
        "kscore_boost": False,
        "max_compress_ratio": 0.55,
    },
    "LONG": {
        "ml_weight_cap": 0.45,
        "buy_threshold":  {"bull": 0.60, "high_vol": 0.65, "bear": 0.70, "unknown": 0.62},
        "hold_threshold": {"bull": 0.46, "high_vol": 0.50, "bear": 0.54, "unknown": 0.46},
        "adx_min": None, "adx_compression": None,
        "high_vol_compression": 0.90,
        "breadth_compression": 0.92,
        "weekly_boost": 1.18, "weekly_compress": 0.80,
        "earnings_compression": None,
        "news_compression": None,
        "rs_compression": 0.80,
        "kscore_boost": True,
        "max_compress_ratio": 0.65,
    },
    # SA-13: Growth/Momentum style for high-volatility, high-return stocks (AI, tech hypergrowth).
    # Key differences from SWING:
    #   - No SMA50>SMA200 requirement: growth stocks consolidate below 200MA for months
    #   - Wider RSI window (38-80): momentum names run "overbought" by traditional standards
    #   - Lower ML bar (0.60 bull): higher-variance names need ML confidence, not structural perfection
    #   - No RS compression: growth stocks often lag their sector before explosive moves
    #   - No weekly BUY gate: weekly RSI can be high without being a sell signal for growth names
    "GROWTH": {
        "ml_weight_cap": 0.70,
        "buy_threshold":  {"bull": 0.57, "high_vol": 0.65, "bear": 0.68, "unknown": 0.57},
        "hold_threshold": {"bull": 0.45, "high_vol": 0.50, "bear": 0.52, "unknown": 0.45},
        "adx_min": 12, "adx_compression": 0.92,
        "high_vol_compression": 0.88,
        "breadth_compression": 0.95,
        "weekly_boost": 1.08, "weekly_compress": 0.92,
        "earnings_compression": {2: 0.60, 5: 0.80, 10: 0.92},
        "news_compression": {25: 0.80, 35: 0.90},
        "rs_compression": None,
        "kscore_boost": False,
        "max_compress_ratio": 0.60,
        "skip_weekly_gate": True,
    },
}


def _growth_ta_adjustment(df: pd.DataFrame, base_reasons: dict) -> float:
    """Additive delta on top of the base TA score for the GROWTH style.

    Growth/momentum stocks are penalised by the base score for having RSI in
    momentum territory (>65) and for lacking the SMA50>SMA200 golden-cross setup.
    This function corrects for those biases so high-growth names are not
    systematically ranked below their true signal strength.
    """
    close = df["close"].astype(float)
    delta = 0.0
    sma20 = close.rolling(20).mean().iloc[-1]
    sma50 = close.rolling(50).mean().iloc[-1]
    if not pd.isna(sma20) and not pd.isna(sma50) and sma20 > sma50:
        delta += 0.10  # replaces SMA50>SMA200 at equal weight — growth names live above SMA20>SMA50
    rsi_val = base_reasons.get("rsi")
    if rsi_val is not None:
        if 72 <= rsi_val <= 85:
            delta += 0.04  # momentum territory — valid for growth, not overbought
        elif 65 <= rsi_val < 72:
            delta += 0.02  # base gives mild credit here; small extra for growth
    return float(np.clip(delta, -0.10, 0.10))


def _fetch_kscore(symbol: str) -> float | None:
    """Fetch the latest K-Score (0-100) from the ranking engine, or None on failure."""
    try:
        with httpx.Client(timeout=8) as c:
            r = c.get(f"{_settings.ranking_engine_url}/rankings/{symbol}")
            if r.status_code == 200:
                return r.json().get("score")
    except Exception:
        pass
    return None


def _fetch_short_interest(symbol: str) -> tuple[float | None, float | None]:
    """Return (short_percent_of_float, short_ratio) from market-data fundamentals, or (None, None)."""
    try:
        url = f"{_settings.market_data_url}/stocks/{symbol}/fundamentals"
        with httpx.Client(timeout=6) as c:
            r = c.get(url)
            if r.status_code == 200:
                d = r.json()
                spf = d.get("short_percent_of_float")
                sr = d.get("short_ratio")
                return spf, sr
    except Exception:
        pass
    return None, None


def _fetch_analyst_momentum(symbol: str) -> tuple[int, int]:
    """Return (upgrades_7d, downgrades_7d) from market-data analyst_actions (last 7 days).

    Uses the already-cached fundamentals endpoint so no extra yfinance call is made.
    action values from yfinance: "up" / "down" / "main" / "init" / "reit".
    "init" (initiated coverage) counts as an upgrade if to_grade is positive.
    Returns (0, 0) on any failure.
    """
    _UP_ACTIONS = {"up", "upgrade", "init", "initiated"}
    _DOWN_ACTIONS = {"down", "downgrade"}
    try:
        from datetime import date as _adate, timedelta as _td
        cutoff = (_adate.today() - _td(days=7)).isoformat()
        url = f"{_settings.market_data_url}/stocks/{symbol}/fundamentals"
        with httpx.Client(timeout=6) as c:
            r = c.get(url)
            if r.status_code == 200:
                actions = r.json().get("analyst_actions", [])
                ups = sum(
                    1 for a in actions
                    if a.get("date", "") >= cutoff
                    and a.get("action", "").lower() in _UP_ACTIONS
                )
                downs = sum(
                    1 for a in actions
                    if a.get("date", "") >= cutoff
                    and a.get("action", "").lower() in _DOWN_ACTIONS
                )
                return ups, downs
    except Exception:
        pass
    return 0, 0


def _decide_style(fused_prob: float, style_key: str, market_regime: str) -> tuple[str, str, str]:
    """Map fused probability to a BUY/HOLD/WAIT/SELL label using style thresholds.

    Returns (signal, style_key, threshold_tier).
    """
    p = _STYLE_PROFILES[style_key]
    reg = market_regime if market_regime in ("bull", "high_vol", "bear") else "unknown"
    buy_t  = p["buy_threshold"][reg]
    hold_t = p["hold_threshold"][reg]
    tier = "bull" if reg == "bull" else ("bear" if reg in ("bear", "high_vol") else "neutral")
    if fused_prob > buy_t:   return "BUY",  style_key, tier
    if fused_prob > hold_t:  return "HOLD", style_key, tier
    if fused_prob >= 0.35:   return "WAIT", style_key, tier
    return "SELL", style_key, tier


def _apply_style_signal(
    ta_prob: float,
    ml_prob: float | None,
    ml_test_auc: float,
    style_key: str,
    market_regime: str,
    adx_val: float,
    weekly_tech: dict,
    pattern_adj: float,
    days_to_earnings: int | None,
    news_sentiment: float | None,
    rs_rank: float | None,
    options_sentiment: str | None,
    cp_ratio: float | None,
    kscore: float | None,
    is_stale: bool,
    base_reasons: dict,
    earnings_beat_rate: float | None = None,
    sector_etf_above_sma50: bool | None = None,
    short_pct_float: float | None = None,
    analyst_upgrades_7d: int = 0,
    analyst_downgrades_7d: int = 0,
    ml_oos_suppressed: bool = False,
) -> "AIConfidence":
    """Apply style-specific fusion and filters from shared base values.

    All expensive data fetching has already been done. This function takes the
    shared pre-computed values and applies the profile-specific weights,
    compression multipliers, and thresholds for the given trading style.
    """
    p = _STYLE_PROFILES[style_key]
    reasons = dict(base_reasons)

    # ── ML / TA fusion with style-specific ML weight cap ─────────────────────
    if ml_prob is not None:
        ml_prob_c = float(np.clip(ml_prob, 0.05, 0.95))
        if ml_test_auc < 0.50:
            # Truly random or inverse model — zero weight
            raw_w = 0.0
        elif ml_test_auc < 0.55:
            # Ramp from 0 at AUC=0.50 to 0.20 at AUC=0.55 — weak model gets low influence
            raw_w = float((ml_test_auc - 0.50) / 0.05 * 0.20)
        else:
            # Ramp from 0.20 at AUC=0.55 up to 0.75 at AUC=0.70+
            raw_w = float(np.clip(0.20 + (ml_test_auc - 0.55) / 0.15 * 0.55, 0.20, 0.75))
        eff_cap = _ml_weight_global_cap if _ml_weight_global_cap is not None else p["ml_weight_cap"]
        ml_w  = min(raw_w, eff_cap)
        gap = abs(ml_prob_c - ta_prob)
        if gap > 0.35:
            # Graduated from 25% cut (at gap=0.35) to 50% cut (at gap=0.65).
            # Continues monotonically from the intermediate band instead of restarting at 0%.
            extra = 0.25 * min((gap - 0.35) / 0.30, 1.0)
            ml_w *= (0.75 - extra)
            reasons["ml_ta_conflict"] = True
        elif gap > 0.25:
            ml_w *= 0.75  # flat 25% cut for intermediate disagreement
            reasons["ml_ta_conflict"] = True
        else:
            reasons["ml_ta_conflict"] = False
        fused = ml_w * ml_prob_c + (1.0 - ml_w) * ta_prob
        reasons["ml_weight"] = round(ml_w, 2)
    else:
        fused = ta_prob
        reasons["ml_ta_conflict"] = False
        reasons["ml_weight"] = 0.0

    fused = float(np.clip(fused, 0.0, 1.0))

    # ── SA-18: Blend weekly TA score into fused probability (SWING/LONG only) ─
    # Daily signals can fire on short-term noise that contradicts the medium-term
    # weekly picture. For SWING/LONG, shift 15% weight to the weekly TA composite
    # score so the fused probability always reflects both timeframes.
    # weekly_score of 0.5 = neutral; the blend gently pulls toward weekly bias.
    # GROWTH and SHORT are excluded: SHORT is pure daily momentum; GROWTH uses
    # a different weekly gate (skip_weekly_gate=True).
    weekly_score_blend = weekly_tech.get("weekly_score", 0.5)
    if style_key in ("SWING", "LONG") and weekly_tech.get("weekly_rsi") is not None:
        wc = weekly_tech.get("weekly_confidence", 1.0)
        blend_weight = 0.15 * wc  # scale blend by data confidence (SA-4 convention)
        fused = fused * (1.0 - blend_weight) + weekly_score_blend * blend_weight
        reasons["weekly_blend_applied"] = True
    else:
        reasons["weekly_blend_applied"] = False
    fused = float(np.clip(fused, 0.0, 1.0))

    fused_before_filters = fused  # snapshot before compression — used for cap enforcement

    # ── Weekly multi-timeframe alignment ──────────────────────────────────────
    weekly_score = weekly_tech.get("weekly_score", 0.5)
    weekly_rsi   = weekly_tech.get("weekly_rsi")
    weekly_trend = weekly_tech.get("weekly_trend", "neutral")
    daily_dir  = fused - 0.5
    weekly_dir = weekly_score - 0.5
    # Only apply alignment boost/compress when weekly data is actually available.
    # weekly_rsi is None when < 15 weekly bars exist. For 15–25 bars a partial
    # confidence factor (0.70–1.0) scales the boost/compress toward neutral so
    # data-sparse stocks get a softer nudge rather than the full filter weight.
    weekly_confidence = weekly_tech.get("weekly_confidence", 1.0)
    reasons["weekly_confidence"] = round(weekly_confidence, 3)
    if weekly_rsi is None:
        # No weekly history — skip alignment filter entirely, treat as neutral
        reasons["weekly_alignment"] = None
    elif daily_dir * weekly_dir > 0:
        boosted = float(np.clip(0.5 + daily_dir * p["weekly_boost"], 0.0, 1.0))
        fused = fused * (1.0 - weekly_confidence) + boosted * weekly_confidence
        reasons["weekly_alignment"] = True
    else:
        compressed = float(np.clip(0.5 + daily_dir * p["weekly_compress"], 0.0, 1.0))
        fused = fused * (1.0 - weekly_confidence) + compressed * weekly_confidence
        reasons["weekly_alignment"] = False

    # ── ADX choppy-market compression ────────────────────────────────────────
    adx_min  = p.get("adx_min")
    adx_comp = p.get("adx_compression")
    # C3 FIX: skip compression if adx_val is None (insufficient history) — don't penalise
    if adx_min is not None and adx_comp is not None and adx_val is not None and adx_val < adx_min:
        fused = 0.5 + (fused - 0.5) * adx_comp
    reasons["adx_compression"] = (adx_min is not None and adx_val is not None and adx_val < adx_min)

    # ── High-volatility regime compression ───────────────────────────────────
    hv_comp = p.get("high_vol_compression")
    if hv_comp is not None and market_regime == "high_vol":
        fused = 0.5 + (fused - 0.5) * hv_comp
    reasons["high_vol_compression"] = (hv_comp is not None and market_regime == "high_vol")

    # ── Market breadth compression ────────────────────────────────────────────
    breadth_pct = base_reasons.get("breadth_pct")
    bc = p.get("breadth_compression")
    breadth_fired = False
    if bc is not None and breadth_pct is not None and breadth_pct < 40:
        fused = 0.5 + (fused - 0.5) * bc
        breadth_fired = True
    reasons["breadth_compression"] = breadth_fired
    fused = float(np.clip(fused, 0.0, 1.0))

    # ── Chart pattern adjustment ──────────────────────────────────────────────
    fused = float(np.clip(fused + pattern_adj, 0.0, 1.0))

    # Double-top neckline break — override: strong BUY suppression when breakdown confirmed
    if base_reasons.get("double_top_neckline_broken") and "double_top" in (base_reasons.get("active_patterns") or []):
        fused = 0.5 + (fused - 0.5) * 0.35  # collapse toward 0.50 — this is a SELL setup, not BUY
        reasons["double_top_breakdown"] = True

    # ── Earnings proximity (style-specific, SA-7 regime-aware) ───────────────
    # Bull + reliable beater (≥70%): skip compression, +3% boost — earnings
    #   beats in bull markets tend to gap up; compression hurts win rate.
    # Bull + moderate beater (50–70%): halve compression (beat_scale = 2.0).
    # Bear / high_vol: tighten compression (beat_scale = 0.75–1.0 based on rate).
    # Unknown / no history: original ±20% beat_rate adjustment.
    ec = p.get("earnings_compression")

    # SA-25: SHORT style binary-event guard — even though earnings_compression=None (earnings treated
    # as catalyst), DTE≤2 is a coin-flip event that can wipe a 5-day trade; apply hard compress.
    if style_key == "SHORT" and days_to_earnings is not None and 0 <= days_to_earnings <= 2:
        fused = 0.5 + (fused - 0.5) * 0.40
        reasons["earnings_warning"] = "short_imminent_event"
        reasons["earnings_beat_rate"] = round(earnings_beat_rate, 2) if earnings_beat_rate is not None else None

    if ec is not None and days_to_earnings is not None:
        ebr = earnings_beat_rate
        reasons["earnings_beat_rate"] = round(ebr, 2) if ebr is not None else None

        if market_regime == "bull" and ebr is not None and ebr >= 0.70:
            # Reliable beater in bull: remove compression, small conviction boost
            fused = float(np.clip(fused + 0.03, 0.0, 1.0))
            reasons["earnings_warning"] = "bull_beater"
        else:
            if market_regime == "bull" and ebr is not None and ebr >= 0.50:
                beat_scale = 2.0  # halve compression for moderate bull beater
            elif market_regime in ("bear", "high_vol"):
                # Tighten: low beat_rate → stronger compression; clamp [0.75, 1.0]
                beat_scale = 0.85 if ebr is None else float(np.clip(0.75 + 0.25 * ebr, 0.75, 1.0))
            else:
                # Unknown/default: original ±20% beat_rate adjustment
                beat_scale = 1.0 if ebr is None else float(np.clip(1.0 + 0.20 * (ebr - 0.50) / 0.50, 0.80, 1.20))

            if 0 <= days_to_earnings <= 2:
                adj_mult = float(np.clip(ec[2] * beat_scale, 0.0, 1.0))
                fused = 0.5 + (fused - 0.5) * adj_mult
                reasons["earnings_warning"] = "caution"
            elif days_to_earnings <= 5:
                adj_mult = float(np.clip(ec[5] * beat_scale, 0.0, 1.0))
                fused = 0.5 + (fused - 0.5) * adj_mult
                reasons["earnings_warning"] = "note"
            elif days_to_earnings <= 10:
                adj_mult = float(np.clip(ec[10] * beat_scale, 0.0, 1.0))
                fused = 0.5 + (fused - 0.5) * adj_mult
                reasons["earnings_warning"] = "watch"
            else:
                reasons.setdefault("earnings_warning", None)
    else:
        reasons["earnings_beat_rate"] = round(earnings_beat_rate, 2) if earnings_beat_rate is not None else None
        reasons.setdefault("earnings_warning", None)

    # ── News sentiment compression (style-specific) ───────────────────────────
    nc = p.get("news_compression")
    if nc is not None and news_sentiment is not None:
        if news_sentiment < 25:
            fused = 0.5 + (fused - 0.5) * nc[25]
            reasons["news_sentiment_flag"] = "strongly_negative"
        elif news_sentiment < 35:
            fused = 0.5 + (fused - 0.5) * nc[35]
            reasons["news_sentiment_flag"] = "negative"
        else:
            reasons["news_sentiment_flag"] = "neutral_or_positive"
    fused = float(np.clip(fused, 0.0, 1.0))

    # ── Relative strength vs sector ───────────────────────────────────────────
    # SA-23: threshold changed 0.80 → 0.70; absolute return floor: never compress if stock
    # itself is up > 5% in 20 days (it's not a true laggard, just a hot-sector context).
    rs_comp = p.get("rs_compression")
    stock_20d_ret_pct = base_reasons.get("stock_20d_return_pct")
    rs_absolute_floor = stock_20d_ret_pct is not None and stock_20d_ret_pct > 5.0
    if rs_comp is not None and rs_rank is not None and rs_rank < 0.70 and not rs_absolute_floor:
        fused = 0.5 + (fused - 0.5) * rs_comp
        reasons["rs_flag"] = "lagging_sector"
    elif rs_absolute_floor and rs_rank is not None and rs_rank < 0.70:
        reasons["rs_flag"] = "lagging_sector_floor_applied"  # lagging but absolute return positive
    elif rs_rank is not None:
        reasons["rs_flag"] = "in_line_or_leading"
    fused = float(np.clip(fused, 0.0, 1.0))

    # ── SA-16: Sector ETF trend filter (SWING/LONG only) ──────────────────────
    # When the stock's sector ETF is below its 50-day SMA the whole sector is
    # in a structural downtrend. A stock bucking that trend alone faces higher
    # mean-reversion risk, so we compress the signal 15% toward neutral.
    # GROWTH and SHORT are exempt: growth names lead their sector, and SHORT is
    # purely momentum-driven and unaffected by sector-level trend.
    if style_key in ("SWING", "LONG") and sector_etf_above_sma50 is False:
        fused = 0.5 + (fused - 0.5) * 0.85
        reasons["sector_headwind"] = True
    else:
        reasons["sector_headwind"] = False
    fused = float(np.clip(fused, 0.0, 1.0))

    # ── Options flow ──────────────────────────────────────────────────────────
    if options_sentiment == "strongly_bullish":
        fused = float(np.clip(fused + 0.04, 0.0, 1.0))
        reasons["options_flag"] = "unusual_call_activity"
    elif options_sentiment == "bullish":
        fused = float(np.clip(fused + 0.02, 0.0, 1.0))
        reasons["options_flag"] = "elevated_call_volume"
    elif options_sentiment == "bearish":
        fused = float(np.clip(0.5 + (fused - 0.5) * 0.92, 0.0, 1.0))
        reasons["options_flag"] = "elevated_put_volume"
    elif options_sentiment == "slightly_bearish":
        fused = float(np.clip(0.5 + (fused - 0.5) * 0.96, 0.0, 1.0))
        reasons["options_flag"] = "slightly_elevated_puts"
    elif options_sentiment is not None:
        reasons["options_flag"] = "neutral"
    else:
        reasons["options_flag"] = "no_data"

    # ── S/R zone context ──────────────────────────────────────────────────────
    sr_ctx = base_reasons.get("sr_context", "neutral")
    if sr_ctx == "at_resistance":
        fused = 0.5 + (fused - 0.5) * 0.85   # compress 15% — price stalling at ceiling
        reasons["sr_flag"] = "at_resistance"
    elif sr_ctx == "breakout":
        fused = float(np.clip(fused + 0.05, 0.0, 1.0))  # boost — confirmed level break
        reasons["sr_flag"] = "breakout_confirmed"
    elif sr_ctx == "at_support":
        fused = float(np.clip(fused + 0.03, 0.0, 1.0))  # small boost — near demand zone
        reasons["sr_flag"] = "at_support"
    else:
        reasons["sr_flag"] = "neutral"
    fused = float(np.clip(fused, 0.0, 1.0))

    # ── Short interest: squeeze potential boost (SWING/GROWTH) ───────────────
    # High short interest (≥20%) heading into a bullish signal raises squeeze
    # risk for shorts — a small confidence boost for BUY direction.
    # Applies to SWING and GROWTH only; LONG ignores short-term positioning.
    if style_key in ("SWING", "GROWTH") and short_pct_float is not None:
        if short_pct_float >= 0.30 and fused > 0.5:
            fused = float(np.clip(fused + 0.04, 0.0, 1.0))
            reasons["short_interest_flag"] = "very_high_squeeze_potential"
        elif short_pct_float >= 0.20 and fused > 0.5:
            fused = float(np.clip(fused + 0.02, 0.0, 1.0))
            reasons["short_interest_flag"] = "elevated_short_interest"
        else:
            reasons["short_interest_flag"] = "normal"
    else:
        reasons["short_interest_flag"] = "no_data" if short_pct_float is None else "normal"
    fused = float(np.clip(fused, 0.0, 1.0))

    # ── Analyst upgrade/downgrade momentum ───────────────────────────────────
    # Recent (last 7 days) analyst actions shift probability toward the consensus.
    # Upgrades from multiple firms = institutional buy pressure; downgrades = exit signal.
    # Not applied to SHORT (too short-horizon to care about analyst coverage cycles).
    if style_key != "SHORT":
        net = analyst_upgrades_7d - analyst_downgrades_7d
        if analyst_upgrades_7d >= 2 and net >= 2:
            adj = min(analyst_upgrades_7d * 0.025, 0.05)
            fused = float(np.clip(fused + adj, 0.0, 1.0))
            reasons["analyst_momentum"] = "strong_upgrade"
            reasons["analyst_momentum_adj"] = round(adj, 3)
        elif analyst_upgrades_7d >= 1 and net >= 1:
            fused = float(np.clip(fused + 0.02, 0.0, 1.0))
            reasons["analyst_momentum"] = "mild_upgrade"
            reasons["analyst_momentum_adj"] = 0.02
        elif analyst_downgrades_7d >= 2 and net <= -2:
            adj = min(analyst_downgrades_7d * 0.04, 0.08)
            fused = float(np.clip(fused - adj, 0.0, 1.0))
            reasons["analyst_momentum"] = "strong_downgrade"
            reasons["analyst_momentum_adj"] = round(-adj, 3)
        elif analyst_downgrades_7d >= 1 and net <= -1:
            fused = float(np.clip(fused - 0.03, 0.0, 1.0))
            reasons["analyst_momentum"] = "mild_downgrade"
            reasons["analyst_momentum_adj"] = -0.03
        else:
            reasons["analyst_momentum"] = "neutral"
            reasons["analyst_momentum_adj"] = 0.0
    else:
        reasons["analyst_momentum"] = "n/a"
        reasons["analyst_momentum_adj"] = 0.0
    reasons["analyst_upgrades_7d"] = analyst_upgrades_7d
    reasons["analyst_downgrades_7d"] = analyst_downgrades_7d
    fused = float(np.clip(fused, 0.0, 1.0))

    # ── K-Score fundamental boost (LONG only) ────────────────────────────────
    if p.get("kscore_boost") and kscore is not None:
        if kscore >= 70:
            fused = float(np.clip(fused + 0.08, 0.0, 1.0))
        elif kscore >= 55:
            fused = float(np.clip(fused + 0.04, 0.0, 1.0))
        elif kscore < 35:
            fused = float(np.clip(fused - 0.06, 0.0, 1.0))
        reasons["kscore_used"] = kscore

    # ── Stale price penalty ───────────────────────────────────────────────────
    if is_stale:
        fused = 0.5 + (fused - 0.5) * 0.6
        reasons["stale_price_warning"] = True

    # ── Insufficient history penalty ──────────────────────────────────────────
    # < 50 bars means SMA200, ADX, and RSI are unreliable; compress toward neutral.
    if base_reasons.get("insufficient_history"):
        fused = 0.5 + (fused - 0.5) * 0.5
        reasons["insufficient_history_warning"] = True

    # ── SA-27: Low OOS accuracy penalty ───────────────────────────────────────
    # When the ML model's cross-val accuracy < 52% (coin-flip territory), the ML
    # service returns bullish_probability=0.5 (neutralised). TA can still push fused
    # above threshold, but we apply a 0.6× compression so a low-confidence model
    # doesn't unduly amplify bullish TA noise into a full BUY.
    # Absent flag = new/untuned symbol → do not penalise.
    if ml_oos_suppressed:
        fused = 0.5 + (fused - 0.5) * 0.6
        reasons["low_oos_accuracy"] = True
    else:
        reasons["low_oos_accuracy"] = False

    fused = float(np.clip(fused, 0.0, 1.0))

    # ── Compression cap ───────────────────────────────────────────────────────
    # Stacked filters can over-suppress signals when multiple risks coexist.
    # If the cumulative compression has squeezed the signal below the max_compress_ratio
    # floor, restore it so a genuinely bullish base signal can still fire a BUY.
    max_ratio = p.get("max_compress_ratio", 0.50)
    orig_dist = fused_before_filters - 0.5
    curr_dist = fused - 0.5
    if orig_dist != 0 and abs(curr_dist) < abs(orig_dist) * max_ratio:
        fused = 0.5 + float(np.sign(orig_dist)) * abs(orig_dist) * max_ratio
        fused = float(np.clip(fused, 0.0, 1.0))
        reasons["compression_cap_applied"] = True
    else:
        reasons["compression_cap_applied"] = False

    # ── Weekly BUY gate — applied AFTER compression cap so it cannot be overridden ──
    # Bearish weekly structure (RSI < 40 AND trend down) is a confirmed downtrend, not a dip.
    # The 0.40× compression is intentionally exempt from max_compress_ratio so a SWING/LONG
    # BUY truly cannot fire in this condition without an overwhelming daily signal (≥0.90+).
    # GROWTH style skips this gate: growth names often have abnormal weekly RSI readings.
    if style_key in ("SWING", "LONG") and not p.get("skip_weekly_gate") and weekly_rsi is not None and weekly_rsi < 40 and weekly_trend == "down":
        fused = 0.5 + (fused - 0.5) * 0.40
        fused = float(np.clip(fused, 0.0, 1.0))
        reasons["weekly_gate_fired"] = True
    else:
        reasons["weekly_gate_fired"] = False

    signal, horizon, threshold_tier = _decide_style(fused, style_key, market_regime)
    reasons["threshold_tier"] = threshold_tier  # SA-12: log which regime threshold was applied
    confidence = round(abs(fused - 0.5) * 200, 2)
    return AIConfidence(
        signal=signal,
        horizon=horizon,
        confidence=confidence,
        bullish_probability=round(fused, 4),
        reasons=reasons,
    )


def _check_price_staleness(df: pd.DataFrame, symbol: str) -> bool:
    """Return True (and log) if the most recent price bar is older than 3 calendar days.

    Stale data causes the signal to reflect an outdated market picture. The
    ingest scheduler should keep data fresh, so staleness indicates a pipeline
    gap rather than normal operation.
    """
    try:
        last_ts = pd.to_datetime(df["ts"]).max()
        # C2 FIX: normalize to UTC date before comparing — server-local date.today() can be
        # off by one vs tz-aware bars near midnight UTC, causing valid data to appear stale.
        if hasattr(last_ts, "tz_localize") and last_ts.tzinfo is None:
            last_ts_utc = last_ts
        else:
            last_ts_utc = last_ts.tz_convert("UTC") if last_ts.tzinfo else last_ts
        from datetime import timezone as _tz
        today_utc = __import__("datetime").datetime.now(_tz.utc).date()
        days_old = (today_utc - last_ts_utc.date()).days
        if days_old > 3:
            log.warning(
                "signal.stale_price_data",
                symbol=symbol,
                last_bar=last_ts.strftime("%Y-%m-%d"),
                days_old=days_old,
            )
            return True
    except Exception:
        pass
    return False


def generate_all_signals(symbol: str) -> dict[str, "AIConfidence"]:
    """Generate SHORT, SWING, and LONG signals in a single data-fetch pass.

    Returns a dict keyed by horizon string: {'SHORT': ..., 'SWING': ..., 'LONG': ...}.
    All expensive I/O (prices, ML, market regime, etc.) happens once; each style
    then applies its own profile to the shared base values.
    """
    df = _fetch_prices(symbol)
    if df.empty:
        raise ValueError(f"No price data for {symbol}")

    # Fewer than 50 bars means SMA200, ADX, and RSI are all unreliable.
    # Flag the signal as low-confidence rather than silently serving defaults.
    insufficient_history = len(df) < 50

    is_stale = _check_price_staleness(df, symbol)
    ta_prob, reasons = _ta_score(df)
    sr_data = _sr_context(df)
    ml_prob, ml_test_auc, ml_meta = _fetch_ml_data(symbol)
    market_regime, fg_score = _fetch_market_regime()
    breadth_pct = _fetch_market_breadth()
    days_to_earnings = _fetch_earnings_proximity(symbol)
    earnings_beat_rate = _fetch_earnings_beat_rate(symbol)
    news_sentiment = _fetch_news_sentiment(symbol)
    rs_score, rs_rank, sector_etf_above_sma50, stock_20d_ret = _fetch_relative_strength(symbol)
    options_sentiment, cp_ratio = _fetch_options_flow(symbol)
    patterns = _fetch_patterns_from_ta(symbol)
    pattern_adj, active_patterns = _pattern_score_adjustment(patterns, len(df))
    df_weekly = _resample_to_weekly(df)
    weekly_tech = _weekly_technicals(df_weekly)
    weekly_score = weekly_tech["weekly_score"]
    kscore = _fetch_kscore(symbol)
    short_pct_float, short_ratio = _fetch_short_interest(symbol)
    analyst_upgrades_7d, analyst_downgrades_7d = _fetch_analyst_momentum(symbol)

    # Populate shared base reasons (written into every style's output)
    reasons["market_regime"]      = market_regime
    reasons["fear_greed_score"]   = fg_score
    reasons["breadth_pct"]        = breadth_pct
    reasons["ta_score"]           = ta_prob
    reasons["ml_probability"]     = ml_prob
    reasons["ml_test_auc"]        = ml_test_auc
    reasons["ml_model"]           = ml_meta.get("ml_model")
    reasons["ml_agreement"]       = ml_meta.get("ml_agreement")
    reasons["ml_model_probs"]     = ml_meta.get("ml_model_probs")
    reasons["ml_oos_suppressed"]  = ml_meta.get("ml_oos_suppressed", False)
    reasons["weekly_ta_score"]    = round(weekly_score, 3)
    reasons["weekly_rsi"]         = weekly_tech["weekly_rsi"]
    reasons["weekly_trend"]       = weekly_tech["weekly_trend"]
    reasons["weekly_macd_bull"]   = weekly_tech["weekly_macd_bull"]
    reasons["active_patterns"]    = active_patterns
    reasons["pattern_adjustment"] = round(pattern_adj, 3)

    # Extract double_bottom / double_top metadata for conviction gate and paper trading
    for p in patterns:
        pname = p.get("name", "")
        meta  = p.get("meta", {})
        if pname == "double_bottom":
            reasons["double_bottom_neckline"]        = meta.get("neckline")
            reasons["double_bottom_target"]          = meta.get("target")
            reasons["double_bottom_stop"]            = meta.get("stop")
            reasons["double_bottom_neckline_broken"] = bool(meta.get("neckline_broken"))
            reasons["double_bottom_vol_confirmed"]   = bool(meta.get("vol_confirmed"))
        elif pname == "double_top":
            reasons["double_top_neckline"]        = meta.get("neckline")
            reasons["double_top_target"]          = meta.get("target")
            reasons["double_top_neckline_broken"] = bool(meta.get("neckline_broken"))
    reasons["days_to_earnings"]   = days_to_earnings
    reasons["news_sentiment"]     = news_sentiment
    reasons["rs_score"]                = rs_score
    reasons["rs_rank"]                 = rs_rank
    reasons["sector_etf_above_sma50"]  = sector_etf_above_sma50
    reasons["stock_20d_return_pct"]    = round(stock_20d_ret * 100, 2) if stock_20d_ret is not None else None
    reasons["options_sentiment"]  = options_sentiment
    reasons["options_cp_ratio"]   = round(cp_ratio, 2) if cp_ratio is not None else None
    reasons["kscore"]             = kscore
    reasons["short_pct_float"]    = round(short_pct_float * 100, 1) if short_pct_float is not None else None
    reasons["short_ratio"]        = round(short_ratio, 1) if short_ratio is not None else None
    reasons["insufficient_history"] = insufficient_history
    reasons["bar_count"]          = len(df)
    reasons["sr_context"]           = sr_data["sr_context"]
    reasons["sr_nearest_resistance"] = sr_data["sr_nearest_resistance"]
    reasons["sr_nearest_support"]   = sr_data["sr_nearest_support"]
    reasons["sr_52w_high"]          = sr_data["sr_52w_high"]
    reasons["sr_52w_low"]           = sr_data["sr_52w_low"]

    # SA-13: GROWTH style uses an adjusted TA score that de-penalises momentum RSI and
    # substitutes SMA20>SMA50 for the SMA50>SMA200 structural requirement.
    ta_prob_growth = float(np.clip(ta_prob + _growth_ta_adjustment(df, reasons), 0.0, 1.0))

    def _make_signal(style_key: str) -> "AIConfidence":
        tp = ta_prob_growth if style_key == "GROWTH" else ta_prob
        return _apply_style_signal(
            ta_prob=tp,
            ml_prob=ml_prob,
            ml_test_auc=ml_test_auc,
            style_key=style_key,
            market_regime=market_regime,
            adx_val=float(reasons.get("adx") or 0.0),
            weekly_tech=weekly_tech,
            pattern_adj=pattern_adj,
            days_to_earnings=days_to_earnings,
            news_sentiment=news_sentiment,
            rs_rank=rs_rank,
            options_sentiment=options_sentiment,
            cp_ratio=cp_ratio,
            kscore=kscore,
            is_stale=is_stale,
            base_reasons=reasons,
            earnings_beat_rate=earnings_beat_rate,
            sector_etf_above_sma50=sector_etf_above_sma50,
            short_pct_float=short_pct_float,
            analyst_upgrades_7d=analyst_upgrades_7d,
            analyst_downgrades_7d=analyst_downgrades_7d,
            ml_oos_suppressed=ml_meta.get("ml_oos_suppressed", False),
        )

    return {k: _make_signal(k) for k in ("SHORT", "SWING", "LONG", "GROWTH")}


def generate_signal(symbol: str) -> AIConfidence:
    """Generate the SWING signal for a symbol — backwards-compatible wrapper."""
    return generate_all_signals(symbol)["SWING"]
