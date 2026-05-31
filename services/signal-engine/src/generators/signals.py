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


def _fetch_ml_data(symbol: str) -> tuple[float | None, float]:
    """Return (bullish_probability, cv_auc_mean).

    Tries the XGBoost+RF ensemble first; falls back to XGBoost-only.
    cv_auc_mean drives the dynamic ML/TA fusion weight — a high-quality model
    (AUC 0.70) earns up to 75% weight; a near-random model (AUC 0.50) gets
    only 40% so the hand-tuned TA rules compensate.
    """
    payload = {"symbol": symbol}
    for endpoint in ("/ml/predict_ensemble", "/ml/predict"):
        try:
            with httpx.Client(timeout=10) as c:
                r = c.post(
                    f"{_settings.ml_prediction_url}{endpoint}",
                    json=payload if endpoint == "/ml/predict_ensemble" else {**payload, "model": "xgboost"},
                )
                if r.status_code == 200:
                    data = r.json()
                    prob = float(data.get("bullish_probability", 0.5))
                    cv_auc = float((data.get("metrics") or {}).get("cv_auc_mean") or 0.55)
                    return prob, cv_auc
        except Exception as exc:
            log.warning("ml.fetch_failed", symbol=symbol, endpoint=endpoint, error=str(exc))
    return None, 0.55


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


def _fetch_news_sentiment(symbol: str) -> float | None:
    """Return a 7-day news sentiment score (0–100, 50 = neutral).

    Uses the sentiment field already computed by the market-data news endpoint
    (yfinance VADER scores, range −1 to +1). Maps to 0–100 and averages the
    last 10 articles (yfinance typically returns 7–10 recent items).
    Returns None if no news available or endpoint unreachable.
    """
    try:
        url = f"{_settings.market_data_url}/stocks/{symbol}/news?sources=yfinance&limit=10"
        with httpx.Client(timeout=8) as c:
            r = c.get(url)
            if r.status_code != 200:
                return None
        articles = r.json()
        if not articles:
            return None
        scores = [
            float(a["sentiment"]) * 50 + 50  # map −1..+1 → 0..100
            for a in articles
            if isinstance(a.get("sentiment"), (int, float))
        ]
        return round(sum(scores) / len(scores), 1) if scores else None
    except Exception:
        return None


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

    return (
        float(adx.iloc[-1])      if not pd.isna(adx.iloc[-1])      else 20.0,
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


def _weekly_ta_score(df: pd.DataFrame) -> float:
    """Simplified weekly TA score for multi-timeframe confirmation. Returns 0-1."""
    if df.empty or len(df) < 26:
        return 0.5
    close = df["close"].astype(float)

    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / 14, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / 14, adjust=False).mean()
    rsi = 100 - 100 / (1 + g / l.replace(0, np.nan))
    rsi_val = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else None

    sma20 = close.rolling(20).mean()
    above_sma20 = bool(close.iloc[-1] > sma20.iloc[-1]) if not pd.isna(sma20.iloc[-1]) else False

    macd_line = close.ewm(span=12).mean() - close.ewm(span=26).mean()
    hist = macd_line - macd_line.ewm(span=9).mean()
    macd_positive = bool(hist.iloc[-1] > 0)
    macd_rising = bool(hist.iloc[-1] > hist.iloc[-2]) if len(hist) >= 2 else False

    score = 0.35
    if rsi_val is not None:
        if 40 < rsi_val < 68:
            score += 0.20
        elif rsi_val <= 40:
            score += 0.10
    if above_sma20:
        score += 0.25
    if macd_positive and macd_rising:
        score += 0.20
    elif macd_positive:
        score += 0.10

    return float(np.clip(score, 0, 1))


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
        if name in BULLISH:
            adj += 0.08 * confidence * recency
            active.append(name)
        elif name in BEARISH:
            adj -= 0.08 * confidence * recency
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

    # ── RSI divergence (10-bar lookback) ─────────────────────────────────
    rsi_divergence = "none"
    if len(rsi.dropna()) >= 11 and len(close) >= 11:
        price_higher = bool(close.iloc[-1] > close.iloc[-11])
        rsi_higher   = bool(rsi.iloc[-1]   > rsi.iloc[-11])
        if price_higher and not rsi_higher:
            rsi_divergence = "bearish"
        elif not price_higher and rsi_higher:
            rsi_divergence = "bullish"
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

    if above_sma50:         score += 0.15
    if sma50_above_sma200:  score += 0.10
    if golden_cross_event:  score += 0.10
    if death_cross_event:   score -= 0.10

    if rsi_val is not None:
        if 45 < rsi_val < 65:    score += 0.15
        elif 35 < rsi_val <= 45: score += 0.08
        elif 65 <= rsi_val < 72: score += 0.06

    if stoch_oversold:      score += 0.10
    elif stoch_overbought:  score -= 0.08
    if stoch_cross_up:      score += 0.05

    if rsi_divergence == "bearish":   score -= 0.10
    elif rsi_divergence == "bullish": score += 0.08

    if macd_hist > 0 and macd_rising:  score += 0.15
    elif macd_hist > 0:                score += 0.08
    if macd_zero_cross_up:             score += 0.05

    if 0.2 < bb_pct_b < 0.8:   score += 0.10

    if price_above_vwap is True:   score += 0.08
    elif price_above_vwap is False: score -= 0.05

    if bullish_trend:                                       score += 0.10
    if obv_bullish:                                         score += 0.10
    if reasons["volume_z"] and reasons["volume_z"] > 0.5:  score += 0.05

    # Normalise by theoretical max (1.36) so the score is a true [0,1] probability.
    # Without normalisation, stocks triggering all bullish conditions would saturate
    # at 1.0 before clipping, losing the relative differentiation from ML fusion.
    _TA_MAX_SCORE = 1.36
    return float(np.clip(score / _TA_MAX_SCORE, 0.0, 1.0)), reasons


def _decide(fused_prob: float, market_regime: str) -> tuple[str, str]:
    """Map fused probability to a signal label.

    In a bear market (S&P 500 below 200MA), raise the BUY threshold to avoid
    false entries during broad market downtrends.
    """
    if market_regime == "bear":
        buy_threshold  = 0.73
        hold_threshold = 0.56
    else:
        buy_threshold  = 0.65
        hold_threshold = 0.50

    if fused_prob > buy_threshold:   return "BUY",  "SWING"
    if fused_prob > hold_threshold:  return "HOLD", "SWING"
    if fused_prob >= 0.35:           return "WAIT", "SWING"
    return "SELL", "SWING"


def _check_price_staleness(df: pd.DataFrame, symbol: str) -> None:
    """Log a warning if the most recent price bar is older than 3 calendar days.

    Stale data causes the signal to reflect an outdated market picture. The
    ingest scheduler should keep data fresh, so staleness indicates a pipeline
    gap rather than normal operation.
    """
    from datetime import date as _date, timedelta
    try:
        last_ts = pd.to_datetime(df["ts"]).max()
        days_old = (_date.today() - last_ts.date()).days
        if days_old > 3:
            log.warning(
                "signal.stale_price_data",
                symbol=symbol,
                last_bar=last_ts.strftime("%Y-%m-%d"),
                days_old=days_old,
            )
    except Exception:
        pass


def generate_signal(symbol: str) -> AIConfidence:
    df = _fetch_prices(symbol)
    if df.empty:
        raise ValueError(f"No price data for {symbol}")

    _check_price_staleness(df, symbol)

    ta_prob, reasons = _ta_score(df)
    ml_prob, ml_cv_auc = _fetch_ml_data(symbol)
    market_regime = _fetch_market_regime()
    reasons["market_regime"] = market_regime

    # Dynamic fusion: ML weight scales with model quality (CV AUC).
    # AUC 0.50 (random) → 40% ML; AUC 0.70+ (excellent) → 75% ML.
    # This prevents a poorly-fit symbol model from overriding the TA rules.
    if ml_prob is not None:
        ml_weight = float(np.clip(0.40 + (ml_cv_auc - 0.50) / 0.20 * 0.35, 0.40, 0.75))
        ta_weight = 1.0 - ml_weight
        fused = ml_weight * ml_prob + ta_weight * ta_prob
        reasons["ml_probability"] = ml_prob
        reasons["ml_weight"] = round(ml_weight, 2)
    else:
        fused = ta_prob
        reasons["ml_probability"] = None
        reasons["ml_weight"] = 0.0
    reasons["ta_score"] = ta_prob

    # ── Multi-timeframe confirmation (weekly) ─────────────────────────────
    df_weekly = _fetch_weekly_prices(symbol)
    weekly_score = _weekly_ta_score(df_weekly)
    reasons["weekly_ta_score"] = round(weekly_score, 3)

    daily_direction  = fused - 0.5
    weekly_direction = weekly_score - 0.5
    if daily_direction * weekly_direction > 0:
        # Both timeframes agree → amplify signal by 12%
        fused = 0.5 + daily_direction * 1.12
    else:
        # Timeframes conflict → compress signal toward neutral by 15%
        fused = 0.5 + daily_direction * 0.85
    fused = float(np.clip(fused, 0.0, 1.0))
    reasons["weekly_alignment"] = (daily_direction * weekly_direction) > 0

    # ── Chart pattern fusion ───────────────────────────────────────────────
    patterns = _fetch_patterns_from_ta(symbol)
    pattern_adj, active_patterns = _pattern_score_adjustment(patterns, len(df))
    fused = float(np.clip(fused + pattern_adj, 0.0, 1.0))
    reasons["active_patterns"] = active_patterns
    reasons["pattern_adjustment"] = round(pattern_adj, 3)

    # ── Earnings proximity penalty ─────────────────────────────────────────
    days_to_earnings = _fetch_earnings_proximity(symbol)
    reasons["days_to_earnings"] = days_to_earnings
    if days_to_earnings is not None:
        if 0 <= days_to_earnings <= 2:
            # Earnings in 0-2 days: extreme uncertainty, compress hard toward 0.5
            fused = 0.5 + (fused - 0.5) * 0.25
            reasons["earnings_warning"] = "critical"
        elif days_to_earnings <= 5:
            fused = 0.5 + (fused - 0.5) * 0.55
            reasons["earnings_warning"] = "caution"
        elif days_to_earnings <= 10:
            fused = 0.5 + (fused - 0.5) * 0.80
            reasons["earnings_warning"] = "note"

    # ── News sentiment filter ──────────────────────────────────────────────
    # Persistently negative news (score < 30) compresses the signal toward
    # neutral. This suppresses BUY signals ahead of regulatory action,
    # leadership crises, or product recalls that technicals won't catch yet.
    news_sentiment = _fetch_news_sentiment(symbol)
    reasons["news_sentiment"] = news_sentiment
    if news_sentiment is not None:
        if news_sentiment < 25:
            fused = 0.5 + (fused - 0.5) * 0.70  # strongly negative — compress 30%
            reasons["news_sentiment_flag"] = "strongly_negative"
        elif news_sentiment < 35:
            fused = 0.5 + (fused - 0.5) * 0.80  # moderately negative — compress 20%
            reasons["news_sentiment_flag"] = "negative"
        else:
            reasons["news_sentiment_flag"] = "neutral_or_positive"
    fused = float(np.clip(fused, 0.0, 1.0))

    signal, horizon = _decide(fused, market_regime)
    confidence = round(abs(fused - 0.5) * 200, 2)

    return AIConfidence(
        signal=signal,
        horizon=horizon,
        confidence=confidence,
        bullish_probability=round(fused, 4),
        reasons=reasons,
    )
