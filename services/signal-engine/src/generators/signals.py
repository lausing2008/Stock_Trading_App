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
"""
from __future__ import annotations

from dataclasses import dataclass

import httpx
import numpy as np
import pandas as pd

from common.config import get_settings
from common.logging import get_logger

log = get_logger("signal-generator")
_settings = get_settings()


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


def _fetch_ml_probability(symbol: str) -> float | None:
    try:
        with httpx.Client(timeout=10) as c:
            r = c.post(
                f"{_settings.ml_prediction_url}/ml/predict",
                json={"symbol": symbol, "model": "xgboost"},
            )
            if r.status_code == 200:
                return float(r.json().get("bullish_probability", 0.5))
    except Exception as exc:
        log.warning("ml.fetch_failed", symbol=symbol, error=str(exc))
    return None


def _fetch_market_regime() -> str:
    """Returns 'bull', 'bear', or 'unknown'. Uses Redis-cached fear_greed endpoint."""
    try:
        with httpx.Client(timeout=5) as c:
            r = c.get(f"{_settings.market_data_url}/stocks/fear_greed")
            if r.status_code == 200:
                return r.json().get("sp500_regime", "unknown")
    except Exception:
        pass
    return "unknown"


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

    return (
        float(adx.iloc[-1])      if not pd.isna(adx.iloc[-1])      else 20.0,
        float(di_plus.iloc[-1])  if not pd.isna(di_plus.iloc[-1])  else 0.0,
        float(di_minus.iloc[-1]) if not pd.isna(di_minus.iloc[-1]) else 0.0,
    )


def _stoch_rsi(rsi: pd.Series, period: int = 14, smooth_k: int = 3, smooth_d: int = 3) -> tuple[float, float]:
    """Stochastic RSI — normalises RSI into 0-1 range, then smooths.

    Returns (%K, %D) where:
      < 0.20 = oversold  (potential buy zone)
      > 0.80 = overbought (potential sell zone)
    """
    rsi_min = rsi.rolling(period).min()
    rsi_max = rsi.rolling(period).max()
    rng = rsi_max - rsi_min
    raw_k = (rsi - rsi_min) / rng.replace(0, np.nan)
    k = raw_k.rolling(smooth_k).mean()
    d = k.rolling(smooth_d).mean()
    k_val = float(k.iloc[-1]) if not pd.isna(k.iloc[-1]) else 0.5
    d_val = float(d.iloc[-1]) if not pd.isna(d.iloc[-1]) else 0.5
    return k_val, d_val


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
    stoch_k, stoch_d = _stoch_rsi(rsi)
    stoch_oversold   = stoch_k < 0.20
    stoch_overbought = stoch_k > 0.80
    # Fresh recovery: %K just crossed up through the oversold threshold
    stoch_cross_up = False
    k_series = rsi.rolling(14).apply(lambda x: (x.iloc[-1] - x.min()) / (x.max() - x.min()) if x.max() != x.min() else 0.5, raw=False)
    k_smooth = k_series.rolling(3).mean()
    if len(k_smooth.dropna()) >= 2:
        stoch_cross_up = bool(k_smooth.iloc[-1] > 0.20 and k_smooth.iloc[-2] <= 0.20)

    reasons["stoch_rsi_k"]         = round(stoch_k, 3)
    reasons["stoch_rsi_d"]         = round(stoch_d, 3)
    reasons["stoch_rsi_oversold"]  = stoch_oversold
    reasons["stoch_rsi_overbought"]= stoch_overbought
    reasons["stoch_rsi_cross_up"]  = stoch_cross_up

    # ── RSI divergence (10-bar lookback) ─────────────────────────────────
    rsi_divergence = "none"
    if len(rsi.dropna()) >= 11 and len(close) >= 11:
        price_higher = bool(close.iloc[-1] > close.iloc[-11])
        rsi_higher   = bool(rsi.iloc[-1]   > rsi.iloc[-11])
        if price_higher and not rsi_higher:
            rsi_divergence = "bearish"   # price up, momentum fading
        elif not price_higher and rsi_higher:
            rsi_divergence = "bullish"   # price down, momentum recovering
    reasons["rsi_divergence"] = rsi_divergence

    # ── MACD histogram + zero-line crossover ──────────────────────────────
    macd_line = close.ewm(span=12).mean() - close.ewm(span=26).mean()
    hist = macd_line - macd_line.ewm(span=9).mean()
    macd_hist  = float(hist.iloc[-1])
    macd_rising = bool(hist.iloc[-1] > hist.iloc[-2]) if len(hist) >= 2 else False
    # MACD line crossing above zero is a stronger trend-direction signal
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

    # ── ADX — trend strength ──────────────────────────────────────────────
    adx_val, di_plus, di_minus = _adx(df)
    trending      = adx_val > 25
    bullish_trend = trending and di_plus > di_minus
    reasons["adx"]          = round(adx_val, 1)
    reasons["adx_trending"] = trending
    reasons["adx_bullish"]  = bullish_trend

    # ── OBV trend (volume-confirmed direction) ────────────────────────────
    direction = close.diff().apply(lambda x: 1 if x > 0 else (-1 if x < 0 else 0))
    obv = (volume * direction).cumsum()
    obv_bullish = bool(obv.rolling(10).mean().iloc[-1] > obv.rolling(30).mean().iloc[-1])
    reasons["obv_bullish"] = obv_bullish

    # ── Volume expansion ──────────────────────────────────────────────────
    vol_z = (volume.iloc[-1] - volume.rolling(20).mean().iloc[-1]) / volume.rolling(20).std().iloc[-1]
    reasons["volume_z"] = float(vol_z) if not pd.isna(vol_z) else None

    # ── Score ─────────────────────────────────────────────────────────────
    score = 0.0

    # Trend (unchanged)
    if above_sma50:         score += 0.15
    if sma50_above_sma200:  score += 0.10
    if golden_cross_event:  score += 0.10
    if death_cross_event:   score -= 0.10

    # RSI — tighter ideal range; partial credit for recovery / near-overbought
    if rsi_val is not None:
        if 45 < rsi_val < 65:    score += 0.15   # ideal entry zone
        elif 35 < rsi_val <= 45: score += 0.08   # oversold recovery
        elif 65 <= rsi_val < 72: score += 0.06   # still ok but extended
        # RSI > 72 or < 35: no credit (extreme zones unreliable for entry)

    # Stochastic RSI
    if stoch_oversold:      score += 0.10   # RSI itself is at a low extreme
    elif stoch_overbought:  score -= 0.08   # RSI itself is stretched
    if stoch_cross_up:      score += 0.05   # fresh oversold recovery signal

    # RSI divergence
    if rsi_divergence == "bearish":  score -= 0.10  # price up, momentum fading
    elif rsi_divergence == "bullish": score += 0.08 # price down, RSI recovering

    # MACD
    if macd_hist > 0 and macd_rising:  score += 0.15
    elif macd_hist > 0:                score += 0.08
    if macd_zero_cross_up:             score += 0.05  # MACD just turned positive

    # Bollinger %B (not at extremes)
    if 0.2 < bb_pct_b < 0.8:   score += 0.10

    # ADX / OBV / volume
    if bullish_trend:                                       score += 0.10
    if obv_bullish:                                         score += 0.10
    if reasons["volume_z"] and reasons["volume_z"] > 0.5:  score += 0.05

    return float(np.clip(score, 0, 1)), reasons


def _decide(fused_prob: float, market_regime: str) -> tuple[str, str]:
    """Map fused probability to a signal label.

    In a bear market (S&P 500 below 200MA), individual stock signals are less
    reliable because systemic risk dominates. Raise the BUY threshold to avoid
    false entries during broad market downtrends.
    """
    if market_regime == "bear":
        buy_threshold  = 0.73   # require stronger conviction in bear markets
        hold_threshold = 0.56
    else:
        buy_threshold  = 0.65
        hold_threshold = 0.50

    if fused_prob > buy_threshold:   return "BUY",  "SWING"
    if fused_prob > hold_threshold:  return "HOLD", "SWING"
    if fused_prob >= 0.35:           return "WAIT", "SWING"
    return "SELL", "SWING"


def generate_signal(symbol: str) -> AIConfidence:
    df = _fetch_prices(symbol)
    if df.empty:
        raise ValueError(f"No price data for {symbol}")

    ta_prob, reasons = _ta_score(df)
    ml_prob = _fetch_ml_probability(symbol)
    market_regime = _fetch_market_regime()
    reasons["market_regime"] = market_regime

    # Fuse: 60% ML if available, else 100% TA
    if ml_prob is not None:
        fused = 0.6 * ml_prob + 0.4 * ta_prob
        reasons["ml_probability"] = ml_prob
    else:
        fused = ta_prob
        reasons["ml_probability"] = None

    reasons["ta_score"] = ta_prob
    signal, horizon = _decide(fused, market_regime)
    confidence = round(abs(fused - 0.5) * 200, 2)

    return AIConfidence(
        signal=signal,
        horizon=horizon,
        confidence=confidence,
        bullish_probability=round(fused, 4),
        reasons=reasons,
    )
