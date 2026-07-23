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
  - Rolling 20-day VWMA: price above VWMA = volume-weighted trend filter
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
  SA-28: SWING bull threshold raised 0.62→0.65 (reverts SA-8); GROWTH bull threshold raised 0.57→0.60.
         Weekly overbought gate added (SWING/LONG): weekly_rsi > 75 AND trend UP → ×0.85 compress.
         Prevents chasing extended rallies in bull markets. Mirrors the existing oversold gate.
  SA-30: Minimum pillar requirement per style. SWING and LONG now set min_pillars_for_buy=3.
         When exactly 2 pillars are active (previously no penalty above the SA-19 < 2 gate),
         those styles apply a ×0.70 compress — blocking borderline 2-pillar BUYs while passing
         high-conviction ones (fused ≥ ~0.714 for SWING's 0.65 threshold still clears).
  SA-31: Outcomes-data-driven rebalance (2026-06-18). 60-day signal_outcomes table analysis:
         SWING BUY win rate 27.5% at 10d (conf=65-79 band: 13.3% — WORST despite highest ML
         confidence, indicating ML overconfidence at SWING horizon). SHORT BUY 16.2% (n=37).
         SWING SELL 61.7% — healthy. Changes:
         - SWING ml_weight_cap: 0.75→0.65 (combat overconfidence; high-ML/low-TA signals were
           the worst performers; reduced cap raises TA's relative influence)
         - SWING buy_threshold bull+unknown: 0.65→0.67 (after cap reduction, borderline ML-pushed
           signals that had fused≈0.65-0.67 are now below threshold — these were the weakest cohort)
         - SHORT buy_threshold bull: 0.60→0.63 (TA-dominant style with 16.2% BUY win rate;
           tighter entry requires stronger TA alignment for SHORT buys)
         - SHORT adx_min: 25→27 (SHORT style requires clean directional trend; raises the bar)
"""
from __future__ import annotations

import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from pathlib import Path

import httpx
import numpy as np
import pandas as pd

from common.config import get_settings
from common.logging import get_logger
from common.indicators import rsi as _canon_rsi, macd as _canon_macd, atr as _canon_atr

log = get_logger("signal-generator")
_settings = get_settings()

# Suppress httpx INFO-level request logs — they produce a line per HTTP call
# which fills logs with expected 404s from the ML endpoint cascade.
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


def _adj_close(df: pd.DataFrame) -> pd.Series:
    """Return adj_close when available (filling gaps with close), else close.

    Dividend-adjusted prices prevent false SMA/ATR/MACD signals on ex-dividend
    dates where unadjusted close drops by the dividend amount.
    """
    ac = df.get("adj_close")
    if ac is not None and not ac.isna().all():
        return ac.fillna(df["close"]).astype(float)
    return df["close"].astype(float)

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
    "gc_spread_expanding":      0.06,
    "gc_spread_narrowing":      0.06,
    "rsi_sweet_spot":           0.15,
    "rsi_mild_oversold":        0.08,
    "rsi_mild_overbought":      0.06,
    "stoch_oversold":           0.10,
    "stoch_overbought_penalty": 0.08,
    "stoch_cross_up":           0.05,
    # rsi_divergence keys removed — detection was hard-zeroed (argmax bug); dead weight in denominator
    "macd_strong":              0.15,
    "macd_positive":            0.08,
    "macd_zero_cross_up":       0.05,
    "macd_momentum_fading":     0.08,
    "bb_mid_zone":              0.10,
    "price_above_vwap":         0.08,
    "price_below_vwap_penalty": 0.05,
    "bullish_trend":            0.10,
    "obv_trend_bullish":              0.10,
    "volume_z":                 0.05,  # renamed from volume_surge to match reasons dict key
}
_TA_WEIGHTS_PATH = Path(_settings.model_dir) / "ta_weights.json"
_ML_WEIGHT_OVERRIDE_PATH = Path(_settings.model_dir) / "ml_weight_override.json"
_CONVICTION_WEIGHTS_PATH = Path(_settings.model_dir) / "conviction_weights.json"

# Global ML weight cap override: None means use the per-style profile default.
# Set by calibrate_ml_weight(); loaded at import time from disk if present.
_ml_weight_global_cap: float | None = None
_ml_weight_lock = threading.Lock()
_ML_EXECUTOR = ThreadPoolExecutor(max_workers=4, thread_name_prefix="ml_fetch")

_ml_svc_token_cache: str = ""
_ml_svc_token_exp: float = 0.0  # epoch seconds when the cached token expires


def _ml_service_token() -> str:
    """Return a long-lived service JWT for authenticating signal-engine → ml-prediction calls.

    AUD232-069: duplicates api/routes.py's _service_token() (same sub='signal-engine' JWT
    pattern) rather than sharing one implementation — routes.py imports FROM this module
    (generators/signals.py) at module load time, so signals.py importing back from routes.py
    would be a circular import. Consolidating into one shared helper would need a new module
    location outside both files; deferred as a larger refactor. In the meantime, hardened this
    copy to match routes.py's stronger refresh-window policy: the old version only checked
    truthiness (never refreshed once cached), this now refreshes 7 days before the 365-day
    expiry so the cached token is never used stale, same as _service_token().
    """
    global _ml_svc_token_cache, _ml_svc_token_exp
    import time
    import uuid
    from jose import jwt as _jwt
    from common.config import get_settings as _gs
    if _ml_svc_token_cache and time.time() < _ml_svc_token_exp - 7 * 86400:
        return _ml_svc_token_cache
    s = _gs()
    exp = int(time.time()) + 365 * 86400
    payload = {"sub": "signal-engine", "exp": exp, "jti": str(uuid.uuid4())}
    _ml_svc_token_cache = _jwt.encode(payload, s.jwt_secret, algorithm="HS256")
    _ml_svc_token_exp = float(exp)
    return _ml_svc_token_cache


def _load_ml_weight_override() -> float | None:
    """Load calibrated ML weight cap from Redis (primary), file (fallback)."""
    try:
        from common.redis_client import get_redis as _get_pool_redis
        _rc = _get_pool_redis()
        _val = _rc.get("stockai:ml_weight_cap")
        if _val:
            v = float(_val)
            if 0.0 <= v <= 1.0:
                return v
    except Exception:
        pass
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
    """Update the in-process ML weight cap override and persist to Redis + file."""
    global _ml_weight_global_cap
    with _ml_weight_lock:
        _ml_weight_global_cap = cap
    try:
        from common.redis_client import get_redis as _get_pool_redis
        _rc = _get_pool_redis()
        if cap is None:
            _rc.delete("stockai:ml_weight_cap")
        else:
            _rc.setex("stockai:ml_weight_cap", 90 * 86400, str(round(cap, 4)))
    except Exception:
        pass
    # Legacy file write (backward compat)
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


def _apply_ta_weights_migration(saved: dict) -> dict:
    """Rename legacy key(s) and backfill any missing defaults.

    AUD232-004/045: this used to be a closure inside _load_ta_weights(), so it only ever
    ran on process-start load from Redis/file. calibrate_ta_weights() (routes.py) still
    writes the pre-rename "volume_surge" key when it fits/persists new weights, and
    set_ta_weights() (below) did a raw dict reassign with no migration step — so a freshly
    calibrated volume weight had a dangling "volume_surge" key that _flag_map (which only
    ever looks up "volume_z") never read, silently zeroing that feature's contribution
    until the next restart re-loaded (and migrated) from Redis/file. Hoisted to module
    level so both the load path AND set_ta_weights() apply the same migration.
    """
    if "volume_surge" in saved and "volume_z" not in saved:
        saved["volume_z"] = saved.pop("volume_surge")
    return {**_TA_WEIGHTS_DEFAULT, **saved}


def _load_ta_weights() -> dict[str, float]:
    """Load calibrated TA weights from Redis (primary), file (fallback), then defaults.

    T228: moved from file-only to Redis-primary so Docker rebuilds don't wipe calibration state.
    """
    try:
        from common.redis_client import get_redis as _get_pool_redis
        _rc = _get_pool_redis()
        _raw = _rc.get("stockai:ta_weights")
        if _raw:
            return _apply_ta_weights_migration(json.loads(_raw))
    except Exception:
        pass
    try:
        if _TA_WEIGHTS_PATH.exists():
            with open(_TA_WEIGHTS_PATH) as f:
                saved = json.load(f)
            return _apply_ta_weights_migration(saved)
    except Exception:
        pass
    return dict(_TA_WEIGHTS_DEFAULT)


# STY-001: Load calibrated TA weights; used in _ta_score to blend with pillar score.
# Only the blended path is active when the ta_weights.json file actually exists
# (i.e. after admin runs POST /signals/calibrate_ta_weights). Default weights are
# loaded for all cases so the dict is always populated.
_ta_weights: dict[str, float] = _load_ta_weights()
_ta_weights_calibrated: bool = _TA_WEIGHTS_PATH.exists()
_ta_weights_lock = threading.Lock()


def set_ta_weights(new_weights: dict[str, float]) -> None:
    """Update the in-process TA weights immediately after calibration writes them.

    T232-SIG6: calibrate_ta_weights() used to write to ta_weights.json + Redis and report
    success, but _ta_weights/_ta_weights_calibrated (the module globals _ta_score() actually
    reads on every call) were only ever set once at import time — a running process could be
    operating on weeks-stale weights while an admin believed the latest calibration was live.
    Mirrors set_ml_weight_global_cap()'s existing reassign-under-lock pattern.

    AUD232-004/045: applies the same key-rename migration _load_ta_weights() already applies
    on process start — calibrate_ta_weights() (routes.py) still fits/persists the legacy
    "volume_surge" key name, which _flag_map never reads (it only looks up "volume_z").
    Without this, a raw reassign here left a dangling "volume_surge" key that silently
    contributed zero to every calibrated_ta_score until the next restart re-migrated it.
    """
    global _ta_weights, _ta_weights_calibrated
    with _ta_weights_lock:
        _ta_weights = _apply_ta_weights_migration(dict(new_weights))
        _ta_weights_calibrated = True


def load_conviction_weights() -> dict[str, float]:
    """Load calibrated conviction layer weights from Redis (primary), file (fallback) (AL-3).

    Returns a dict of {reason_flag: accuracy_vs_baseline} where values > 0 mean the
    flag is more common in winning trades. Returns empty dict if not yet calibrated.
    T228: Redis-primary so weights survive Docker rebuilds.
    """
    try:
        from common.redis_client import get_redis as _get_pool_redis
        _rc = _get_pool_redis()
        _raw = _rc.get("stockai:conviction_weights")
        if _raw:
            return json.loads(_raw).get("edge_pct", {})
    except Exception:
        pass
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


def _fetch_ml_data(symbol: str, style_key: str = "SWING") -> tuple[float | None, float, dict]:
    """Return (bullish_probability, test_auc, ml_meta) for the given style.

    SA-8: tries the 3-model ensemble (XGBoost+LightGBM+RF) first, then 2-model,
    then XGBoost-only. ml_meta carries per-model probabilities and agreement status
    for storage in Signal.reasons.

    test_auc drives the dynamic ML/TA fusion weight — a high-quality model (AUC 0.70)
    earns up to 75% weight; a near-random model (AUC < 0.52) gets 0% weight.

    style_key routes to a horizon-specific artifact (e.g. {symbol}_short.joblib).
    Falls back gracefully to the SWING artifact if a style-specific model is absent.
    """
    payload = {"symbol": symbol, "style": style_key}
    endpoints = [
        ("/ml/predict_ensemble_three", payload),
        ("/ml/predict_ensemble",       payload),
        ("/ml/predict",                {**payload, "model": "xgboost"}),
    ]
    for endpoint, body in endpoints:
        try:
            with httpx.Client(timeout=10) as c:
                r = c.post(f"{_settings.ml_prediction_url}{endpoint}", json=body,
                           headers={"Authorization": f"Bearer {_ml_service_token()}"})
                if r.status_code == 200:
                    data = r.json()
                    prob = float(data.get("bullish_probability", 0.5))
                    m = data.get("metrics") or {}
                    test_auc = float(m.get("mean_model_test_auc") or m.get("auc") or m.get("cv_auc_mean") or 0.55)
                    ml_meta = {
                        "ml_model": data.get("model", "xgboost"),
                        "ml_agreement": data.get("ensemble_agreement"),
                        "ml_model_probs": data.get("model_probabilities"),
                        "ml_oos_suppressed": bool(data.get("oos_suppressed", False)),
                    }
                    return prob, test_auc, ml_meta
                # 404 = no model for this endpoint — try next in cascade (expected, not an error)
                if r.status_code != 404:
                    log.warning("ml.fetch_unexpected_status", symbol=symbol, endpoint=endpoint, status=r.status_code)
        except Exception as exc:
            log.warning("ml.fetch_failed", symbol=symbol, endpoint=endpoint, error=str(exc))
    log.debug("ml.no_model", symbol=symbol, note="all endpoints returned 404 — TA-only signal")
    return None, 0.0, {}


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


def _fetch_hsi_regime() -> str:
    """Returns 'bull', 'bear', or 'unknown' based on HSI vs its 20-day SMA.

    Called only for HK stocks. Returns 'unknown' on any failure (fail-open).
    The US SPY/VIX regime does not reflect HK market conditions — during June 2026,
    all HK signals showed market_regime='bull' while HSI was in a sustained downtrend.
    """
    try:
        import yfinance as yf
        hist = yf.Ticker("^HSI").history(period="35d")
        closes = hist["Close"].dropna().tolist()
        if len(closes) >= 20:
            sma20 = sum(closes[-20:]) / 20
            return "bull" if float(closes[-1]) > sma20 else "bear"
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

    Delegates to market-data's /stocks/{symbol}/relative-strength endpoint which owns the
    yfinance ETF fetches and caches results in Redis (1h per symbol, 4h per ETF ticker).
    This makes market-data the single source of truth for RS data — no direct yfinance
    calls from signal-engine.
    """
    try:
        with httpx.Client(timeout=8) as c:
            r = c.get(f"{_settings.market_data_url}/stocks/{symbol}/relative-strength")
        if r.status_code != 200:
            return None, None, None, None
        d = r.json()
        rs_score  = d.get("rs_score")
        rs_rank   = d.get("rs_rank")
        etf_above = d.get("sector_etf_above_sma50")
        ret_pct   = d.get("stock_20d_return_pct")
        stock_ret = ret_pct / 100.0 if ret_pct is not None else None
        return (
            float(rs_score)  if rs_score  is not None else None,
            float(rs_rank)   if rs_rank   is not None else None,
            bool(etf_above)  if etf_above is not None else None,
            float(stock_ret) if stock_ret is not None else None,
        )
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


def _supertrend(df: pd.DataFrame, period: int = 10, multiplier: float = 3.0) -> tuple[int, bool, bool]:
    """Return (trend, cross_up, cross_down) from the last bar.

    trend:      +1 if price above supertrend line (bullish), -1 if below
    cross_up:   True if trend just flipped from -1 → +1 this bar
    cross_down: True if trend just flipped from +1 → -1 this bar
    """
    high  = df["high"].astype(float)
    low   = df["low"].astype(float)
    close = _adj_close(df)
    n = len(close)
    if n < period + 2:
        return 1, False, False

    # AUD232-073 / AUD-DUPLOGIC: was its own inline .ewm(...).mean() with no min_periods —
    # computed a real-looking ATR from bar 0, before `period` true-range bars have accumulated
    # (same bug class as T237-TA-ATR-MINPERIODS, already fixed in the canonical technical-
    # analysis core.py). Now delegates to shared/common/indicators.py's canonical atr()
    # (min_periods=period included there) instead of a second inline copy. The loop below
    # (line ~584) already has an explicit `if np.isnan(basic_upper[i])` guard that correctly
    # holds the prior trend during warmup — it just never had real NaNs to catch before this
    # fix, since the under-warmed ATR silently produced a real (if unreliable) number.
    atr_s = _canon_atr(high, low, close, period=period)

    hl2 = (high + low) / 2
    basic_upper = (hl2 + multiplier * atr_s).values
    basic_lower = (hl2 - multiplier * atr_s).values
    close_v = close.values

    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    trend = np.ones(n)

    for i in range(1, n):
        if np.isnan(basic_upper[i]) or np.isnan(basic_lower[i]):
            trend[i] = trend[i - 1]
            continue
        final_upper[i] = basic_upper[i] if (basic_upper[i] < final_upper[i - 1] or close_v[i - 1] > final_upper[i - 1]) else final_upper[i - 1]
        final_lower[i] = basic_lower[i] if (basic_lower[i] > final_lower[i - 1] or close_v[i - 1] < final_lower[i - 1]) else final_lower[i - 1]
        trend[i] = 1.0 if (trend[i - 1] == -1 and close_v[i] > final_upper[i]) else (
            -1.0 if (trend[i - 1] == 1 and close_v[i] < final_lower[i]) else trend[i - 1]
        )

    return (
        int(trend[-1]),
        bool(trend[-1] == 1 and trend[-2] == -1),
        bool(trend[-1] == -1 and trend[-2] == 1),
    )


def _adx(df: pd.DataFrame, period: int = 14) -> tuple[float, float, float]:
    """Return (ADX, +DI, -DI). ADX > 25 = trending, > 40 = strong trend."""
    high = df["high"].astype(float)
    low  = df["low"].astype(float)
    close = _adj_close(df)

    up_move   = high.diff()
    down_move = (-low.diff())
    dm_plus  = up_move.where((up_move > down_move) & (up_move > 0), 0.0)
    dm_minus = down_move.where((down_move > up_move) & (down_move > 0), 0.0)

    # AUD232-073 / AUD-DUPLOGIC: this used to be its own inline TR/ATR calc — identical math to
    # (and now delegates to) shared/common/indicators.py's canonical atr(), the same one
    # ranking-engine's kscore.py already uses. min_periods=period (AUD232-073's own fix — an
    # under-warmed ATR still produced a real-looking, unreliable number for short-history
    # stocks) lives in the canonical function now, so this can't silently regress in this file
    # without also affecting every other caller of the shared version.
    atr      = _canon_atr(high, low, close, period=period)
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
    close = _adj_close(df)

    # T233-ARCH-INDICATOR-DEDUP: delegates to shared/common/indicators.py's canonical
    # Wilder's RSI/MACD. Pure refactor here — this function's own guard (len(df) < 15) already
    # ensures RSI's min_periods=14 always converges by the time this runs, and MACD's
    # weekly_confidence scaling (see comment above) already correctly discounts the 15-25 bar
    # window where the 26-bar slow EMA hasn't fully converged, so no behavior changes.
    rsi = _canon_rsi(close, window=14)
    rsi_val = float(rsi.iloc[-1]) if not pd.isna(rsi.iloc[-1]) else None

    sma10 = close.rolling(10).mean()
    weekly_trend = "neutral"
    if not pd.isna(sma10.iloc[-1]):
        pct = (close.iloc[-1] - float(sma10.iloc[-1])) / float(sma10.iloc[-1])
        if pct > 0.01:
            weekly_trend = "up"
        elif pct < -0.01:
            weekly_trend = "down"

    _weekly_macd_df = _canon_macd(close, fast=12, slow=26, signal=9)
    macd_line = _weekly_macd_df["macd"]
    hist = _weekly_macd_df["hist"]
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

    # Count consecutive weeks RSI ≤ 38 (used by graduated bearish gate)
    rsi_consec_low = 0
    for v in reversed((rsi <= 38).values):
        if v:
            rsi_consec_low += 1
        else:
            break

    return {
        "weekly_rsi": round(rsi_val, 1) if rsi_val is not None else None,
        "weekly_trend": weekly_trend,
        "weekly_macd_bull": weekly_macd_bull,
        "weekly_score": float(np.clip(score, 0, 1)),
        "weekly_confidence": weekly_confidence,
        "weekly_rsi_consec_low": rsi_consec_low,
    }


def _fetch_sr_context_from_ta(symbol: str) -> dict | None:
    """Fetch the canonical sr_context classification from technical-analysis's GET
    /ta/{symbol}/levels — the same endpoint _fetch_patterns_from_ta() already calls, matching
    this file's existing HTTP-to-TA integration pattern. Returns None on any failure so the
    caller can fall back to the local computation (technical-analysis being unreachable must
    never block signal generation).
    """
    try:
        url = f"{_settings.technical_analysis_url}/ta/{symbol}/levels"
        with httpx.Client(timeout=8) as c:
            r = c.get(url)
            if r.status_code == 200:
                data = r.json().get("sr_context")
                if isinstance(data, dict) and "sr_context" in data:
                    return data
    except Exception:
        pass
    return None


def _sr_context(df: pd.DataFrame, symbol: str | None = None) -> dict:
    """Detect price position relative to key support/resistance levels.

    AUD-DUPLOGIC: when `symbol` is provided, fetches the canonical classification from
    technical-analysis's GET /ta/{symbol}/levels (services/technical-analysis/src/indicators/
    trendlines.py::detect_sr_context() — the same 3-tier pivot-detection strategy the chart's
    own official S/R levels use, already fixed once for a close-vs-high/low pivot mismatch this
    file's own independent pivot detection never received) instead of reimplementing pivot
    detection with a different, simpler window here. Falls back to the local computation below
    (this file's own original 60-bar/±3-window pivot scan) if technical-analysis is unreachable
    or `symbol` is omitted — signal generation must never hard-fail on a TA-service outage.

    Returns sr_context: 'breakout' | 'at_resistance' | 'at_support' | 'neutral'.
    """
    if symbol:
        remote = _fetch_sr_context_from_ta(symbol)
        if remote is not None:
            return remote

    close = _adj_close(df)
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
    # T237-SIG1: `nearest_res` is filtered to strictly > current by construction, so the
    # "already cleared resistance" branch below could never be true — a stock that decisively
    # breaks to a new all-time high (clearing every pivot/52w-high in one move) got NO breakout
    # boost at all, since no resistance level qualifies as "nearest" once price has passed it.
    # Track the highest resistance level still <= current separately so a genuine cleared-level
    # breakout is recognized.
    cleared_res = max((r for r in resistances if r <= current), default=None)

    thr = 0.015  # 1.5% proximity threshold
    sr_context = "neutral"

    if cleared_res is not None and prev < cleared_res:
        # Price closed at/above a former resistance level that the prior bar was still below —
        # a genuine, freshly-confirmed breakout, not just historically having traded above it.
        sr_context = "breakout"
    elif nearest_res is not None:
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
    close  = _adj_close(df)
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
    vol_avg = volume.iloc[:-1].rolling(20).mean().iloc[-1]
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


def _ta_score(df: pd.DataFrame, ta_weights: dict[str, float] | None = None) -> tuple[float, dict]:
    # T233-SIG-RSI1: raised from 14 to 15 — close.diff() below drops the first row, so a
    # 14-bar df only ever produces 13 real diffs, one short of what canonical rsi()'s
    # min_periods=14 needs for a real (non-NaN) value. The old unguarded .ewm() (no
    # min_periods) silently produced a fabricated RSI at exactly 14 bars instead of correctly
    # returning None — this guard now matches what the RSI calculation actually needs.
    if df.empty or len(df) < 15:
        return 0.5, {"insufficient_data": True, "bar_count": len(df)}
    close  = _adj_close(df)
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

    # GC spread velocity: is the 50/200 spread still widening (bullish) or narrowing (exhaustion)?
    # Narrowing spread in golden territory is an early warning even before a death cross forms.
    gc_spread_pct = None
    gc_spread_expanding = False
    if not pd.isna(sma50) and not pd.isna(sma200) and sma200 > 0:
        gc_spread_pct = round(float((sma50 - sma200) / sma200), 4)
        if len(sma50_s.dropna()) >= 6 and len(sma200_s.dropna()) >= 6:
            spread_now = float(sma50_s.iloc[-1] - sma200_s.iloc[-1])
            spread_5d  = float(sma50_s.iloc[-6] - sma200_s.iloc[-6])
            gc_spread_expanding = bool(spread_now > spread_5d)

    reasons["trend_above_sma50"]    = above_sma50
    reasons["sma50_above_sma200"]   = sma50_above_sma200
    reasons["golden_cross_event"]   = golden_cross_event
    reasons["death_cross_event"]    = death_cross_event
    reasons["gc_spread_pct"]        = gc_spread_pct
    reasons["gc_spread_expanding"]  = gc_spread_expanding

    # ── RSI (full series — needed for StochRSI and divergence) ────────────
    # T233-ARCH-INDICATOR-DEDUP / T233-SIG-RSI1: delegates to shared/common/indicators.py's
    # canonical Wilder's RSI (with min_periods=14) instead of a standalone reimplementation
    # that had no min_periods and could produce a fabricated value at exactly 14 bars.
    rsi = _canon_rsi(close, window=14)
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
    # ── MACD histogram + zero-line crossover ──────────────────────────────
    # T233-ARCH-INDICATOR-DEDUP / T233-SIG-RSI1: delegates to shared/common/indicators.py's
    # canonical MACD (min_periods=12/26/9) instead of a standalone reimplementation with no
    # min_periods. A thin-history stock (15-25 bars, below the 26-bar slow-EMA window) now
    # correctly gets NaN instead of a fabricated MACD line — every downstream consumer here
    # (macd_hist > 0, macd_rising, macd_zero_cross_up) already treats NaN comparisons as
    # False via Python's built-in semantics, degrading safely to "not bullish" rather than
    # crashing or silently corrupting the score.
    _macd_df = _canon_macd(close, fast=12, slow=26, signal=9)
    macd_line = _macd_df["macd"]
    hist = _macd_df["hist"]
    macd_hist  = float(hist.iloc[-1])
    macd_rising = bool(hist.iloc[-1] > hist.iloc[-2]) if len(hist) >= 2 else False
    # 3-bar histogram slope: smoother than single-bar, catches momentum exhaustion earlier.
    # A falling slope on a positive histogram (macd_momentum_fading) is the key warning:
    # it precedes price drops by 2-3 bars and was the root cause of false BUYs like 6613.HK.
    macd_hist_slope = float(hist.iloc[-1] - hist.iloc[-3]) if len(hist.dropna()) >= 4 else 0.0
    macd_hist_expanding  = macd_hist_slope > 0
    # Only flag fading when slope is strictly negative — flat (slope==0) scores 0.7 in macd_score,
    # not 0.5, so that branch is reachable.
    macd_momentum_fading = (macd_hist > 0) and (macd_hist_slope < 0)
    macd_zero_cross_up = False
    if len(macd_line.dropna()) >= 2:
        macd_zero_cross_up = bool(macd_line.iloc[-1] > 0 and macd_line.iloc[-2] <= 0)
    reasons["macd_hist"]             = macd_hist
    reasons["macd_rising"]           = macd_rising
    reasons["macd_hist_slope"]       = round(macd_hist_slope, 5)
    reasons["macd_hist_expanding"]   = macd_hist_expanding
    reasons["macd_momentum_fading"]  = macd_momentum_fading
    reasons["macd_zero_cross_up"]    = macd_zero_cross_up

    # ── Bollinger Bands %B ────────────────────────────────────────────────
    sma20 = close.rolling(20).mean()
    std20 = close.rolling(20).std()
    bb_upper = sma20 + 2 * std20
    bb_lower = sma20 - 2 * std20
    band_width = bb_upper.iloc[-1] - bb_lower.iloc[-1]
    bb_pct_b = float((close.iloc[-1] - bb_lower.iloc[-1]) / band_width) if band_width > 0 else 0.5
    reasons["bb_pct_b"] = round(bb_pct_b, 3)

    # ── Rolling 20-day VWMA (Volume-Weighted Moving Average, not session-reset VWAP) ──
    # Use iloc[:-1] to exclude today's bar from the rolling window (avoid self-contamination)
    typical_price = (df["high"].astype(float) + df["low"].astype(float) + close) / 3
    vwma_20 = (typical_price.iloc[:-1] * volume.iloc[:-1]).rolling(20).sum() / volume.iloc[:-1].rolling(20).sum()
    vwma_val = vwma_20.iloc[-1]
    price_above_vwap: bool | None = None
    if not pd.isna(vwma_val) and not np.isinf(vwma_val) and vwma_val > 0:
        price_above_vwap = bool(close.iloc[-1] > vwma_val)
    reasons["price_above_vwap"] = price_above_vwap
    reasons["vwma_20"] = float(vwma_val) if not pd.isna(vwma_val) else None

    # ── Supertrend ────────────────────────────────────────────────────────
    st_trend, st_cross_up, st_cross_down = _supertrend(df)
    reasons["supertrend_bullish"] = bool(st_trend == 1)
    reasons["supertrend_cross_up"] = st_cross_up
    reasons["supertrend_cross_down"] = st_cross_down

    # ── ROC (Rate of Change) — 10-day and 20-day ──────────────────────────
    roc_10 = float((close.iloc[-1] / close.iloc[-11] - 1) * 100) if len(close) >= 11 else None
    roc_20 = float((close.iloc[-1] / close.iloc[-21] - 1) * 100) if len(close) >= 21 else None
    reasons["roc_10"] = round(roc_10, 2) if roc_10 is not None else None
    reasons["roc_20"] = round(roc_20, 2) if roc_20 is not None else None

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
    obv_trend_bullish = bool(obv.rolling(10).mean().iloc[-1] > obv.rolling(30).mean().iloc[-1])
    reasons["obv_trend_bullish"] = obv_trend_bullish

    # ── Volume expansion ──────────────────────────────────────────────────
    vol_std = volume.iloc[:-1].rolling(20).std().iloc[-1]
    vol_z = (volume.iloc[-1] - volume.iloc[:-1].rolling(20).mean().iloc[-1]) / vol_std if vol_std and vol_std > 0 else 0.0
    reasons["volume_z"] = float(vol_z) if not pd.isna(vol_z) else None

    # ── SA-19: Pillar-based TA score — 4 independent dimensions ─────────────
    # Each pillar scores 0–1 using the BEST signal within that dimension (max,
    # not sum). Correlated indicators (above_sma50 + sma50_above_sma200 + adx)
    # all reflect the same underlying trend, so stacking them overstates edge.
    # TA score = mean of the 4 pillar scores; independent_pillars_active counts
    # how many pillars reach ≥ 0.5 (used by _apply_style_signal for gate/boost).

    _vz = float(reasons.get("volume_z") or 0.0)

    # TREND pillar — structural price direction
    # Supertrend included (10%): complements SMA/ADX trend confirmation.
    # Cross-up gets extra weight (1.0) vs sustained bullish (0.7).
    # GC spread velocity: golden territory with narrowing spread scores 0.4 (not 1.0) —
    # 50 SMA curling back toward 200 is early-warning reversal even before a death cross.
    if death_cross_event or st_cross_down:
        p_trend = 0.0  # confirmed downtrend; hard override
    else:
        _gc_score = (
            1.0 if (golden_cross_event and _vz > 0.5 and gc_spread_expanding) else
            0.8 if (golden_cross_event and gc_spread_expanding) else
            0.5 if golden_cross_event else  # fresh cross but spread already narrowing
            0.0
        )
        _sma_golden_score = (0.8 if gc_spread_expanding else 0.4) if sma50_above_sma200 else 0.0
        _st_score = 1.0 if st_cross_up else (0.7 if st_trend == 1 else 0.0)
        p_trend = (
            (1.0 if above_sma50 else 0.0) * 0.30 +
            _sma_golden_score              * 0.25 +
            (1.0 if bullish_trend else 0.0) * 0.20 +
            _gc_score                       * 0.15 +
            _st_score                       * 0.10
        )

    # MOMENTUM pillar — oscillator-based rate of change
    # Weighted average (not max) so overbought RSI/Stoch meaningfully reduce the pillar
    # even when MACD is strong. Overbought penalties applied before averaging.
    # T232-SIG-ENTRYTIMING (option 1): 28-35 was previously a flat 0.0 — the model actively
    # zeroed out the exact "early recovery off a real dip" zone where genuine bottoms form,
    # while rewarding 45-65 (mid-rally) with full credit. This structurally biased BUY signals
    # toward firing after a move had already run rather than at a healthier, earlier entry.
    # Mirrors the BEARISH pillar's own treatment of this same range (28 <= rsi_val <= 35 scores
    # 0.5 there too — "oversold but not extreme") rather than inventing a new asymmetric rule.
    # Below 28 stays 0.0 — genuinely extreme oversold has no confirmation at all yet, matching
    # the bearish pillar's own boundary.
    rsi_score = (
        1.0 if (rsi_val is not None and 45 < rsi_val < 65) else
        0.8 if (rsi_val is not None and 35 < rsi_val <= 45) else
        0.5 if (rsi_val is not None and 65 <= rsi_val < 72) else
        0.5 if (rsi_val is not None and 28 <= rsi_val < 35) else
        0.0
    )
    # Use 3-bar histogram slope (macd_hist_expanding) instead of single-bar macd_rising.
    # macd_momentum_fading: histogram positive but slope negative — momentum exhaustion,
    # scores 0.5 instead of 0.7 to avoid rewarding a decaying BUY edge.
    macd_score = (
        1.0 if (macd_hist > 0 and macd_hist_expanding) else
        0.9 if macd_zero_cross_up else
        0.5 if macd_momentum_fading else
        0.7 if macd_hist > 0 else
        0.0
    )
    stoch_score = 0.8 if stoch_oversold else (0.7 if stoch_cross_up else 0.0)
    # Apply overbought penalties to individual components before averaging
    if stoch_overbought:
        stoch_score *= 0.0   # overbought stoch is bearish — zero it
        macd_score  *= 0.7   # reduce MACD conviction when stoch overbought
    if rsi_val is not None and rsi_val >= 72:
        rsi_score   *= 0.0   # extreme overbought RSI is a warning, not a bullish signal
    p_momentum = rsi_score * 0.35 + macd_score * 0.40 + stoch_score * 0.25

    # VOLUME pillar — demand confirmation (SA-32: weighted AND logic)
    # Both OBV trend and volume-z positive → full conviction (1.0).
    # Only one positive → partial conviction (0.6); neither → 0.0.
    # Previous OR logic (max) allowed a strong OBV trend with flat recent volume
    # (or vice versa) to score 1.0, overstating demand confirmation.
    obv_signal = obv_trend_bullish
    vol_z_signal = _vz > 0.5
    if obv_signal and vol_z_signal:
        p_volume = 1.0
    elif obv_signal or vol_z_signal:
        p_volume = 0.6
    else:
        p_volume = 0.0

    # STRUCTURE pillar — price position (VWAP + Bollinger Band)
    bb_score = 0.8 if (0.2 < bb_pct_b < 0.8) else 0.0
    vwap_score = (
        1.0 if price_above_vwap is True else
        0.0 if price_above_vwap is False else
        0.4  # unknown — treat as mildly neutral
    )
    p_structure = max(vwap_score, bb_score)
    if price_above_vwap is False:
        p_structure = max(0.0, p_structure - 0.15)  # below VWAP pulls structure down

    pillar_scores = [p_trend, p_momentum, p_volume, p_structure]
    independent_pillars_active = sum(1 for ps in pillar_scores if ps >= 0.5)
    reasons["independent_pillars_active"] = independent_pillars_active
    reasons["pillar_trend"]     = round(p_trend, 2)
    reasons["pillar_momentum"]  = round(p_momentum, 2)
    reasons["pillar_volume"]    = round(p_volume, 2)
    reasons["pillar_structure"] = round(p_structure, 2)

    # T232-SIG10: bearish mirror of the 4 pillars above — observability-only, not wired into
    # any live gate/compression yet. Deliberately NOT `1 - bullish_score`: that would just be a
    # restatement of the bullish pillar, not independent bearish evidence, and would score a
    # merely-neutral stock (bullish pillar ~0.5) as equally bearish, which is wrong. Each
    # bearish pillar is scored from its own bearish-specific conditions (death cross, RSI/MACD
    # breaking down, OBV distribution, price below VWAP/BB), mirroring the bullish pillar's
    # exact structure inverted. This exists so bear/high_vol/choppy/risk_off SELL outcome data
    # starts accumulating a `bearish_pillars_active` value from today — per this tracker item's
    # own prior finding (2026-07-04, re-confirmed 2026-07-20: 2474 bull-regime SELL outcomes vs.
    # 33 unknown vs. ZERO bear/high_vol samples), there is not yet enough non-bull SELL outcome
    # data to fit a real min_pillars_for_sell gate or regime-tiered sell_threshold against —
    # inventing one now would repeat the exact "overfit argmax on thin data" mistake already
    # documented at T232-OC3. This block collects the feature so a future calibration pass has
    # something real to validate against, without gating/compressing any live signal yet.
    bearish_trend = trending and di_minus > di_plus
    macd_zero_cross_down = False
    if len(macd_line.dropna()) >= 2:
        macd_zero_cross_down = bool(macd_line.iloc[-1] < 0 and macd_line.iloc[-2] >= 0)
    stoch_rsi_cross_down = False
    if len(k_smooth.dropna()) >= 2:
        stoch_rsi_cross_down = bool(k_smooth.iloc[-1] < 0.80 and k_smooth.iloc[-2] >= 0.80)
    reasons["bearish_trend"]          = bearish_trend
    reasons["macd_zero_cross_down"]   = macd_zero_cross_down
    reasons["stoch_rsi_cross_down"]   = stoch_rsi_cross_down

    # TREND (bearish) — mirrors p_trend: death cross / supertrend cross-down as a hard
    # override (confirmed downtrend), else weighted from below-SMA50, a death-cross already
    # in place with the spread still widening (analogous to gc_spread_expanding above),
    # bearish ADX trend, and a fresh supertrend cross-down.
    if golden_cross_event or st_cross_up:
        pb_trend = 0.0  # confirmed uptrend; hard override, mirrors death_cross_event above
    else:
        _dc_score = (
            1.0 if (death_cross_event and _vz > 0.5 and not gc_spread_expanding) else
            0.8 if (death_cross_event and not gc_spread_expanding) else
            0.5 if death_cross_event else
            0.0
        )
        _sma_death_score = (0.8 if not gc_spread_expanding else 0.4) if not sma50_above_sma200 else 0.0
        _st_bear_score = 1.0 if st_cross_down else (0.7 if st_trend == -1 else 0.0)
        pb_trend = (
            (1.0 if above_sma50 is False else 0.0) * 0.30 +
            _sma_death_score                         * 0.25 +
            (1.0 if bearish_trend else 0.0)          * 0.20 +
            _dc_score                                * 0.15 +
            _st_bear_score                           * 0.10
        )

    # MOMENTUM (bearish) — mirrors p_momentum: RSI breaking down (not just "not bullish"),
    # MACD histogram negative and expanding downward, stochastic RSI overbought-reversing.
    rsi_bear_score = (
        1.0 if (rsi_val is not None and 35 < rsi_val < 55) else  # bearish sweet spot
        0.8 if (rsi_val is not None and 55 <= rsi_val < 65) else  # mild overbought, room to fall
        0.5 if (rsi_val is not None and 28 <= rsi_val <= 35) else  # oversold but not extreme
        0.0
    )
    macd_bear_score = (
        1.0 if (macd_hist < 0 and not macd_hist_expanding) else
        0.9 if macd_zero_cross_down else
        0.7 if macd_hist < 0 else
        0.0
    )
    stoch_bear_score = 0.8 if stoch_overbought else (0.7 if stoch_rsi_cross_down else 0.0)
    if stoch_oversold:
        stoch_bear_score *= 0.0  # oversold is bullish reversal territory — zero the bear score
        macd_bear_score  *= 0.7
    if rsi_val is not None and rsi_val <= 28:
        rsi_bear_score   *= 0.0  # extreme oversold is a bounce warning, not confirmation to sell
    pb_momentum = rsi_bear_score * 0.35 + macd_bear_score * 0.40 + stoch_bear_score * 0.25

    # VOLUME (bearish) — mirrors p_volume: OBV trend bearish + volume expansion together
    # is full conviction (distribution, not accumulation); either alone is partial.
    obv_bear_signal = not obv_trend_bullish
    if obv_bear_signal and vol_z_signal:
        pb_volume = 1.0
    elif obv_bear_signal or vol_z_signal:
        pb_volume = 0.6
    else:
        pb_volume = 0.0

    # STRUCTURE (bearish) — mirrors p_structure: below VWAP + BB%B pinned near the LOWER band
    # specifically (bb_pct_b <= 0.2), not "outside the neutral band" generally — a %B near 1.0
    # (upper-band extreme, e.g. a strong steady uptrend) is a bullish extreme, not bearish
    # evidence, and treating both tails as bearish would score a strongly uptrending stock as
    # partially bearish on structure alone, which real data confirmed was wrong before this fix.
    bb_bear_score = 0.8 if bb_pct_b <= 0.2 else 0.0
    vwap_bear_score = (
        1.0 if price_above_vwap is False else
        0.0 if price_above_vwap is True else
        0.4
    )
    pb_structure = max(vwap_bear_score, bb_bear_score)
    if price_above_vwap is True:
        pb_structure = max(0.0, pb_structure - 0.15)  # above VWAP pulls bearish structure down

    bearish_pillar_scores = [pb_trend, pb_momentum, pb_volume, pb_structure]
    bearish_pillars_active = sum(1 for ps in bearish_pillar_scores if ps >= 0.5)
    reasons["bearish_pillars_active"] = bearish_pillars_active
    reasons["bearish_pillar_trend"]     = round(pb_trend, 2)
    reasons["bearish_pillar_momentum"]  = round(pb_momentum, 2)
    reasons["bearish_pillar_volume"]    = round(pb_volume, 2)
    reasons["bearish_pillar_structure"] = round(pb_structure, 2)

    base = float(np.mean(pillar_scores))

    # STY-001: If calibrated TA weights were loaded from ta_weights.json, blend a
    # weighted flag score (15%) with the pillar mean (85%).  Only active when the
    # calibration file exists so production behaviour is unchanged until an admin
    # explicitly runs POST /signals/calibrate_ta_weights.
    _weights = ta_weights if ta_weights is not None else _ta_weights
    if _ta_weights_calibrated or ta_weights is not None:
        _flag_map = {
            "above_sma50":              +1 if above_sma50 else 0,
            "sma50_above_sma200":       +1 if sma50_above_sma200 else 0,
            "golden_cross_event":       +1 if golden_cross_event else 0,
            "death_cross_penalty":      -1 if death_cross_event else 0,
            "gc_spread_expanding":      +1 if gc_spread_expanding else 0,
            "gc_spread_narrowing":      -1 if (sma50_above_sma200 and not gc_spread_expanding) else 0,
            "rsi_sweet_spot":           +1 if (rsi_val is not None and 45 < rsi_val < 65) else 0,
            "rsi_mild_oversold":        +1 if (rsi_val is not None and 35 < rsi_val <= 45) else 0,
            "rsi_mild_overbought":      -1 if (rsi_val is not None and 65 <= rsi_val < 72) else 0,
            "stoch_oversold":           +1 if stoch_oversold else 0,
            "stoch_overbought_penalty": -1 if stoch_overbought else 0,
            "stoch_cross_up":           +1 if stoch_cross_up else 0,
            "macd_strong":              +1 if (macd_hist > 0 and macd_hist_expanding) else 0,
            "macd_positive":            +1 if macd_hist > 0 else 0,
            "macd_zero_cross_up":       +1 if macd_zero_cross_up else 0,
            "macd_momentum_fading":     -1 if macd_momentum_fading else 0,
            "bb_mid_zone":              +1 if (0.2 < bb_pct_b < 0.8) else 0,
            "price_above_vwap":         +1 if price_above_vwap is True else 0,
            "price_below_vwap_penalty": -1 if price_above_vwap is False else 0,
            "bullish_trend":            +1 if bullish_trend else 0,
            "obv_trend_bullish":        +1 if obv_trend_bullish else 0,
            "volume_z":                 +1 if _vz > 0.5 else 0,
        }
        _max_w = sum(abs(v) for v in _weights.values())
        if _max_w > 0:
            _weighted = sum(_flag_map.get(k, 0) * v for k, v in _weights.items())
            _calibrated = float(np.clip(0.5 + _weighted / (2 * _max_w), 0.0, 1.0))
            base = base * 0.85 + _calibrated * 0.15
            reasons["calibrated_ta_score"] = round(_calibrated, 3)

    # SA-14 / SA-32: pullback + recovery delta stored in reasons for deferred application.
    # The boost is applied AFTER the pillar gate check in _apply_style_signal so it only
    # rewards high-conviction setups that already pass the independent-pillars requirement.
    # Applying it here (before fusion) was incorrect: a 2-pillar borderline setup could
    # clear the pillar gate threshold only because the pullback boost inflated ta_prob.
    pr_delta, pr_reasons = _pullback_recovery(df)
    reasons.update(pr_reasons)
    reasons["pullback_recovery_delta"] = pr_delta  # deferred; applied post-pillar-gate

    return float(np.clip(base, 0.0, 1.0)), reasons


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
        "ml_weight_floor": 0.10,  # global cap cannot push ML weight below this
        # SA-31: bull raised 0.60→0.63; 16.2% BUY win rate (n=37) — tighter TA alignment needed.
        "buy_threshold":  {"bull": 0.63, "high_vol": 0.65, "bear": 0.68, "unknown": 0.62},
        "hold_threshold": {"bull": 0.46, "high_vol": 0.50, "bear": 0.52, "unknown": 0.47},
        # SA-31: raised 25→27; SHORT requires a cleaner directional trend to BUY.
        "adx_min": 27, "adx_compression": 0.85,
        "high_vol_compression": 0.92,
        "breadth_compression": 0.90,  # REG-002: SHORT style now penalised when market breadth < 40%
        "weekly_boost": 1.08, "weekly_compress": 0.93,
        "earnings_compression": None,
        "news_compression": None,
        "rs_compression": 0.90,
        "kscore_boost": False,
        "max_compress_ratio": 0.70,
    },
    "SWING": {
        # SA-31: 0.75→0.65; outcome analysis showed high-ML-confidence SWING BUY signals
        # (conf=65-79) had the WORST win rate (13.3%), while moderate-conf (30-49) was best
        # (30.8%). Reducing cap shifts overconfident ML-pushed signals back toward TA balance.
        "ml_weight_cap": 0.65,
        "ml_weight_floor": 0.15,
        # SA-28: SWING bull threshold raised 0.62→0.65 (reverts SA-8 over-relaxation).
        # SA-12: only tighten bear/high_vol — keep those unchanged.
        # SA-31: bull+unknown raised 0.65→0.67; after cap reduction, borderline ML-pushed
        # signals that cleared 0.65 only due to high ML weight are now filtered out.
        # SA-32: bull raised 0.67→0.72; bear raised 0.72→0.76; high_vol raised 0.72→0.74.
        # Outcomes audit: SWING BUY at fused 0.67-0.72 had lowest win rate cohort; tighter
        # thresholds eliminate marginal entries in all regime states.
        # T232-DL6: no separate HK SWING threshold exists — this single buy_threshold dict
        # applies identically to US and HK SWING signals. HK-specific adjustment happens only
        # via the HSI-regime compression gates (hsi_bear_gate etc.), not a per-market threshold.
        "buy_threshold":  {"bull": 0.72, "high_vol": 0.74, "bear": 0.76, "unknown": 0.72},
        "hold_threshold": {"bull": 0.50, "high_vol": 0.54, "bear": 0.56, "unknown": 0.50},
        "adx_min": 15, "adx_compression": 0.90,
        "high_vol_compression": 0.85,
        "breadth_compression": 0.90,
        "weekly_boost": 1.12, "weekly_compress": 0.85,
        # Fixed: was {2: 0.25, 5: 0.55, 10: 0.80}. The 0.25× meant a stock needed
        # fused_prob ≈ 1.10 to fire a BUY with earnings in ≤2 days — impossible.
        "earnings_compression": {2: 0.65, 5: 0.85, 10: 0.95},
        "news_compression": {25: 0.75, 35: 0.85},
        "rs_compression": 0.85,
        "kscore_boost": False,
        "max_compress_ratio": 0.55,
        "min_pillars_for_buy": 3,  # SA-30: require 3+ active pillars; 2-pillar BUYs get ×0.70 compress
    },
    "LONG": {
        "ml_weight_cap": 0.45,
        "ml_weight_floor": 0.12,
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
        "min_pillars_for_buy": 3,  # SA-30: LONG requires broad agreement before committing to 20d hold
    },
    # SA-13: Growth/Momentum style for high-volatility, high-return stocks (AI, tech hypergrowth).
    # Key differences from SWING:
    #   - No SMA50>SMA200 requirement: growth stocks consolidate below 200MA for months
    #   - Wider RSI window (38-80): momentum names run "overbought" by traditional standards
    #   - Lower ML bar (0.60 bull): higher-variance names need ML confidence, not structural perfection
    #   - No RS compression: growth stocks often lag their sector before explosive moves
    #   - No weekly BUY gate: weekly RSI can be high without being a sell signal for growth names
    "GROWTH": {
        # T225-C: Reduced 0.70→0.60 — Jun 2026 signal_outcomes audit: ml_prob>0.85 GROWTH BUY
        # had only 33% win rate (9 samples) vs 100% for ml_prob 0.75-0.85 (4 samples).
        # Same ML overconfidence pattern found in SWING (fixed SA-31) and HK (fixed T224).
        "ml_weight_cap": 0.60,
        "ml_weight_floor": 0.20,
        # SA-28: GROWTH bull threshold raised 0.57→0.60 — aligns with SHORT/LONG in bull markets.
        "buy_threshold":  {"bull": 0.60, "high_vol": 0.65, "bear": 0.68, "unknown": 0.60},
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
    close = _adj_close(df)
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


# T247-SIGNALENGINE-INIT-GRADE: yfinance's own grade vocabulary is free-text per analyst firm
# (no fixed enum) — confirmed live against real production data (AAPL): Buy, Strong Buy,
# Outperform, Overweight are bullish; Neutral/Hold are neutral; Sell/Underperform/Underweight
# (not observed in this sample but yfinance's documented vocabulary) are bearish. Only the
# unambiguous bullish set is used to classify an "init" (initiated coverage) as an upgrade —
# anything else (neutral, bearish, or an unrecognized grade) does NOT count as an upgrade,
# matching this function's own docstring ("init counts as an upgrade if to_grade is positive")
# which the original code never actually implemented.
_POSITIVE_GRADES = {
    "buy", "strong buy", "outperform", "overweight", "positive", "add",
}


def _fetch_analyst_momentum(symbol: str) -> tuple[int, int]:
    """Return (upgrades_7d, downgrades_7d) from market-data analyst_actions (last 7 days).

    Uses the already-cached fundamentals endpoint so no extra yfinance call is made.
    action values from yfinance: "up" / "down" / "main" / "init" / "reit".
    "init" (initiated coverage) counts as an upgrade if to_grade is positive.
    Returns (0, 0) on any failure.
    """
    _UP_ACTIONS = {"up", "upgrade"}
    _DOWN_ACTIONS = {"down", "downgrade"}
    _INIT_ACTIONS = {"init", "initiated"}
    try:
        from datetime import date as _adate, timedelta as _td
        cutoff = (_adate.today() - _td(days=7)).isoformat()
        url = f"{_settings.market_data_url}/stocks/{symbol}/fundamentals"
        with httpx.Client(timeout=6) as c:
            r = c.get(url)
            if r.status_code == 200:
                actions = r.json().get("analyst_actions", [])
                recent = [a for a in actions if a.get("date", "") >= cutoff]
                ups = sum(
                    1 for a in recent
                    if a.get("action", "").lower() in _UP_ACTIONS
                    or (
                        a.get("action", "").lower() in _INIT_ACTIONS
                        and a.get("to_grade", "").strip().lower() in _POSITIVE_GRADES
                    )
                )
                downs = sum(
                    1 for a in recent
                    if a.get("action", "").lower() in _DOWN_ACTIONS
                )
                return ups, downs
    except Exception:
        pass
    return 0, 0


def _redis_get_float(key: str) -> float | None:
    """Read a float value from Redis; return None on miss or error."""
    try:
        from common.redis_client import get_redis as _get_pool_redis
        r = _get_pool_redis()
        val = r.get(key)
        return float(val) if val else None
    except Exception:
        return None


_DYNAMIC_BUY_THRESHOLD_BOUNDS = (0.55, 0.85)
_DYNAMIC_SELL_THRESHOLD_BOUNDS = (0.15, 0.45)


def _get_dynamic_buy_threshold(style_key: str, reg: str) -> float | None:
    """Read empirically-calibrated buy threshold from Redis if available.

    Written by POST /outcomes/calibrate/apply (Tier 79) on the fused-probability scale
    (T232-CAL1 fix — previously written on the 0-100 confidence scale and misapplied here).

    T232-CAL2: the calibrated/watchdog value is stored as a single (regime-agnostic)
    number fit mostly on bull-market samples. Rather than overriding all four regime
    tiers with one flat value, we apply it as a delta from the hardcoded bull baseline
    so bear/high_vol stay tighter than bull, preserving SA-32's regime-tiered protection.
    A sanity clamp rejects corrupted/out-of-range values (defense in depth).
    """
    p = _STYLE_PROFILES[style_key]
    bull_base = p["buy_threshold"]["bull"]
    regime_base = p["buy_threshold"].get(reg, bull_base)

    # Check watchdog emergency adjustment first (most recent, tightest)
    dynamic = _redis_get_float(f"stockai:watchdog:{style_key.upper()}:threshold")
    if dynamic is None:
        # Calibrated threshold from weekly outcomes sweep
        dynamic = _redis_get_float(f"stockai:signal_thresholds:{style_key.upper()}")
    if dynamic is None:
        return None

    lo, hi = _DYNAMIC_BUY_THRESHOLD_BOUNDS
    if not (lo <= dynamic <= hi):
        return None  # corrupted/stale-scale value — ignore, fall back to hardcoded profile

    delta = dynamic - bull_base
    return float(np.clip(regime_base + delta, lo, hi))


# AUD232-051: unlike buy_threshold, _STYLE_PROFILES has no per-style/per-regime sell_threshold
# key — the SELL fallback is genuinely a single flat value regardless of style/regime, so a
# module-level constant (not a fake per-style dict) is the correct single source of truth here.
# routes.py's outcomes_calibrate_apply SELL sweep imports this instead of keeping its own
# independently-hardcoded copy that had to be updated by hand in sync (see its old comment,
# which literally said so, at the exact drift risk this fixes).
_SELL_THRESHOLD_FALLBACK = 0.35


def _get_dynamic_sell_threshold(style_key: str) -> float | None:
    """Read empirically-calibrated SELL threshold from Redis if available.

    T228: written by POST /outcomes/calibrate/apply SELL sweep, fused-probability scale
    (T232-CAL3 fix). Returns None → falls back to _SELL_THRESHOLD_FALLBACK in _decide_style.
    """
    dynamic = _redis_get_float(f"stockai:signal_thresholds:SELL:{style_key.upper()}")
    if dynamic is None:
        return None
    lo, hi = _DYNAMIC_SELL_THRESHOLD_BOUNDS
    if not (lo <= dynamic <= hi):
        return None
    return dynamic


def _get_style_tuned_param(style_key: str, param: str, default):
    """Read a tuned style parameter from Redis if available (written by tune_style_profiles).

    Falls back to `default` (the value from _STYLE_PROFILES) when absent.
    Keys: stockai:style_tune:{STYLE}:{param}
    """
    val = _redis_get_float(f"stockai:style_tune:{style_key.upper()}:{param}")
    return val if val is not None else default


def _decide_style(fused_prob: float, style_key: str, market_regime: str) -> tuple[str, str, str]:
    """Map fused probability to a BUY/HOLD/WAIT/SELL label using style thresholds.

    Reads dynamically-calibrated buy and sell thresholds from Redis if available
    (written by POST /outcomes/calibrate/apply).  Falls back to hardcoded values.

    Returns (signal, style_key, threshold_tier).
    """
    p = _STYLE_PROFILES[style_key]
    reg = market_regime if market_regime in ("bull", "high_vol", "bear", "unknown") else "unknown"
    # Dynamic buy override from outcomes-based calibration
    dynamic_buy = _get_dynamic_buy_threshold(style_key, reg)
    buy_t  = dynamic_buy if dynamic_buy is not None else p["buy_threshold"][reg]
    hold_t = p["hold_threshold"][reg]
    # T228: dynamic SELL threshold from SELL-outcomes calibration; fallback to _SELL_THRESHOLD_FALLBACK
    dynamic_sell = _get_dynamic_sell_threshold(style_key)
    sell_t = dynamic_sell if dynamic_sell is not None else _SELL_THRESHOLD_FALLBACK
    tier = "bull" if reg == "bull" else ("bear" if reg in ("bear", "high_vol") else "neutral")
    if fused_prob > buy_t:   return "BUY",  style_key, tier
    if fused_prob > hold_t:  return "HOLD", style_key, tier
    if fused_prob >= sell_t: return "WAIT", style_key, tier
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
        # Per-style ML weight cap: Redis override (tune_style_profiles) > global file override > profile default
        _per_style_cap = _get_style_tuned_param(style_key, "ml_weight_cap", None)
        eff_cap = _per_style_cap if _per_style_cap is not None else (_ml_weight_global_cap if _ml_weight_global_cap is not None else p["ml_weight_cap"])
        ml_w = min(raw_w, eff_cap)
        if raw_w > 0:  # floor only applies to non-zero weights — don't resurrect a zero-weighted inverse model
            # T228: AUC-scaled floor — near-random (AUC≈0.50) gets floor≈0; AUC≥0.60 gets full floor
            auc_floor = max(0.0, (ml_test_auc - 0.50) / 0.10) * p.get("ml_weight_floor", 0.0)
            ml_w = max(ml_w, auc_floor)
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
        reasons["ml_probability"] = round(float(ml_prob_c), 4)  # H-3: per-style, not shared SWING value
    else:
        fused = ta_prob
        reasons["ml_ta_conflict"] = False
        reasons["ml_weight"] = 0.0
        reasons["ml_probability"] = None

    fused = float(np.clip(fused, 0.0, 1.0))

    # T225-B: SWING ML over-confidence gate — when ML is very confident but TA is only moderate,
    # the signal is ML-dominant and has historically underperformed. Jun 2026 data:
    # SWING BUY conf 60-75 bucket (avg_ml=0.951, avg_ta=0.759): only 26.3% win rate.
    # SWING BUY conf 75+ bucket (avg_ml=0.669, avg_ta=0.982): 55.6% — both agree.
    # 15% compression pushes ML-dominant SWING signals below the buy threshold.
    reasons["ml_overconfidence_gate"] = False
    if (style_key == "SWING"
            and ml_prob is not None
            and float(ml_prob) > 0.90
            and ta_prob < 0.75):
        fused = 0.5 + (fused - 0.5) * 0.85
        fused = float(np.clip(fused, 0.0, 1.0))
        reasons["ml_overconfidence_gate"] = True

    fused_before_filters = fused  # snapshot before weekly blend + compression — used for cap enforcement

    # SA-18 (additive 15% weekly blend) was removed: the weekly alignment filter
    # below (lines ~1205-1227) already integrates the weekly picture via boost/compress.
    # Double-applying the same weekly_score data (once as absolute blend, once as
    # directional amplifier) produced compounding effects larger than documented.
    # The alignment filter is the sole weekly integration mechanism.
    # ── SA-19 / SA-30: Independent pillar gate ───────────────────────────────
    # Compress signals where only 1 dimension agrees (likely market-beta noise);
    # boost where all 4 pillars converge (rare, high-confidence setup).
    # SA-30: styles with min_pillars_for_buy=3 (SWING, LONG) apply a stronger
    # ×0.70 compress when exactly 2 pillars are active, blocking borderline BUYs
    # that lack broad TA confirmation (breakeven fused ≈ 0.714 at 0.65 threshold).
    # CVG-002: use None sentinel so missing key is detected and logged instead of
    # silently defaulting to 2 (which masks data-quality issues).
    _pillars_raw = base_reasons.get("independent_pillars_active")
    if _pillars_raw is None:
        import structlog as _sl
        _sl.get_logger().warning("pillar_gate.missing_key", style=style_key)
        _pillars = 2  # neutral fallback: no gate, no boost
    else:
        _pillars = int(_pillars_raw)
    _min_pillars = int(p.get("min_pillars_for_buy", 2))
    # T232-SIG3: independent_pillars_active counts BULLISH evidence (trend/momentum/volume/
    # structure >= 0.5). A deeply bearish stock has 0-1 bullish pillars *by definition*, so
    # applying this compression to SELL candidates (fused < 0.5) pulls the clearest SELLs back
    # toward neutral — the gate was erasing exactly the signals it should confirm. Restrict to
    # the bullish side (fused > 0.5); leave SELL candidates unaffected by this gate.
    if fused > 0.5 and _pillars < 2:
        fused = 0.5 + (fused - 0.5) * 0.85
        reasons["pillar_gate"] = f"compressed_{_pillars}_pillar"
    elif fused > 0.5 and _pillars < _min_pillars:
        # SA-30: active pillars below style requirement — strong compress
        fused = 0.5 + (fused - 0.5) * 0.70
        reasons["pillar_gate"] = f"compressed_{_pillars}_pillar_below_min{_min_pillars}"
    elif _pillars >= 4:
        fused = float(np.clip(fused + 0.03, 0.0, 1.0))
        reasons["pillar_gate"] = "boosted_4_pillar_confluence"
    else:
        reasons["pillar_gate"] = f"{_pillars}_pillars"
    fused = float(np.clip(fused, 0.0, 1.0))

    # ── SA-14 / SA-32: Pullback-recovery boost (deferred from _ta_score) ─────
    # Applied AFTER the pillar gate so the boost only rewards setups that already
    # have sufficient independent TA confirmation (>= min_pillars). A pullback
    # recovery on a 2-pillar setup (compressed above) should not bypass that gate.
    # Only apply when pillars met the style minimum (no compress was applied).
    #
    # T232-SIG-ENTRYTIMING (option 2): the ORIGINAL gate above meant this bonus — the one
    # mechanism specifically built to reward a healthy early dip+recovery — could only ever
    # pad an ALREADY-confirmed setup, never help the early entry it exists to reward. That's
    # because trend/momentum pillars are structurally weak right after a pullback (see the
    # option 1 fix above), so a genuine recovery rarely clears min_pillars on its own merit
    # in time to benefit. Narrow, targeted exception: when RSI sits in the 30-45 recovery band
    # (a real, not-yet-fully-confirmed bounce off a dip — distinct from a stock with no real
    # oversold/recovery evidence at all) AND the pullback-recovery pattern is genuinely
    # volume-confirmed (the strongest of _pullback_recovery()'s two tiers, delta=0.07), allow
    # the bonus to apply even below the style's own min_pillars, but NEVER below the universal
    # 2-pillar floor (_pillars >= 2) — a setup with fewer than 2 real pillars active still has
    # no independent TA support at all, and this exception must not become a full bypass of
    # SA-19's own baseline gate the way the original SA-14/SA-32 comment correctly warned against.
    _pr_delta = base_reasons.get("pullback_recovery_delta", 0.0) or 0.0
    _rsi_val = base_reasons.get("rsi")
    _early_recovery_exception = (
        _pr_delta >= 0.07
        and _rsi_val is not None and 30 <= _rsi_val <= 45
        and _pillars >= 2
    )
    if _pr_delta > 0 and (_pillars >= _min_pillars or _early_recovery_exception):
        fused = float(np.clip(fused + _pr_delta, 0.0, 1.0))
        reasons["pullback_recovery_applied"] = True
        if _pillars < _min_pillars:
            reasons["pullback_recovery_early_exception"] = True
    else:
        reasons["pullback_recovery_applied"] = False

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
    adx_min  = _get_style_tuned_param(style_key, "adx_min", p.get("adx_min"))
    adx_comp = p.get("adx_compression")
    # C3 FIX: skip compression if adx_val is None (insufficient history) — don't penalise
    if adx_min is not None and adx_comp is not None and adx_val is not None and adx_val < adx_min:
        fused = 0.5 + (fused - 0.5) * adx_comp
    reasons["adx_compression"] = (adx_min is not None and adx_val is not None and adx_val < adx_min)

    # ── High-volatility regime compression ───────────────────────────────────
    # T232-SIG5: only compress the bullish side. High-vol regimes are exactly the conditions
    # that CONFIRM a SELL — compressing SELL candidates toward neutral here suppressed the
    # signal in the regime that validates it.
    hv_comp = _get_style_tuned_param(style_key, "high_vol_compression", p.get("high_vol_compression"))
    hv_fired = hv_comp is not None and market_regime == "high_vol" and fused > 0.5
    if hv_fired:
        fused = 0.5 + (fused - 0.5) * hv_comp
    reasons["high_vol_compression"] = hv_fired

    # ── Market breadth compression ────────────────────────────────────────────
    # T232-SIG5: same direction-blind bug — thin breadth (<40% of stocks above their MA) is
    # itself bearish confirmation and should not mute a SELL. Bullish-only.
    breadth_pct = base_reasons.get("breadth_pct")
    bc = _get_style_tuned_param(style_key, "breadth_compression", p.get("breadth_compression"))
    breadth_fired = False
    if bc is not None and breadth_pct is not None and breadth_pct < 40 and fused > 0.5:
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

    # SA-25: SHORT style binary-event guard — DTE≤2 is a coin-flip event that can wipe a 5-day trade.
    # Uses beat_rate scaling (same logic as other styles) so reliable earnings beaters get less compression.
    if style_key == "SHORT" and days_to_earnings is not None and 0 <= days_to_earnings <= 2:
        ebr_short = earnings_beat_rate
        if ebr_short is not None and ebr_short >= 0.70 and market_regime == "bull":
            beat_scale_short = 2.0  # reliable beater — halve compression
        elif market_regime in ("bear", "high_vol"):
            beat_scale_short = 0.85 if ebr_short is None else float(np.clip(0.75 + 0.25 * ebr_short, 0.75, 1.0))
        else:
            beat_scale_short = 1.0 if ebr_short is None else float(np.clip(1.0 + 0.20 * (ebr_short - 0.50) / 0.50, 0.80, 1.20))
        adj_short = float(np.clip(0.40 * beat_scale_short, 0.0, 1.0))
        fused = 0.5 + (fused - 0.5) * adj_short
        reasons["earnings_warning"] = "short_imminent_event"
        reasons["earnings_beat_rate"] = round(ebr_short, 2) if ebr_short is not None else None

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
    # mean-reversion risk, so we compress the BUY signal 15% toward neutral.
    # Direction-aware (SA-32): only compress when fused > 0.5 (BUY direction).
    # When fused < 0.5 (SELL direction), sector weakness CONFIRMS the signal —
    # do not compress. Compressing a SELL in a sector downtrend was incorrectly
    # pushing weak signals back toward neutral/HOLD, reducing SELL accuracy.
    # GROWTH and SHORT are exempt: growth names lead their sector, and SHORT is
    # purely momentum-driven and unaffected by sector-level trend.
    if style_key in ("SWING", "LONG") and sector_etf_above_sma50 is False and fused > 0.5:
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
    reasons["ml_oos_suppressed"] = ml_oos_suppressed  # per-style; each horizon has its own model
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
    # None weekly_trend (missing history) safely skips the gate — no data = no penalty.
    # Threshold ≤ 38 (not < 40) reduces boundary sensitivity; RSI 38-40 is neutral, not bearish.
    if (style_key in ("SWING", "LONG")
            and not p.get("skip_weekly_gate")
            and weekly_rsi is not None
            and weekly_trend is not None
            and weekly_rsi <= 38
            and weekly_trend == "down"):
        # Graduated compression: brief dips (< 5 bars) get 0.65× — could recover quickly.
        # Confirmed downtrends (≥ 20 bars) get 0.40× — structurally broken weekly chart.
        # Linear interpolation between 5 and 20 bars.
        _consec = weekly_tech.get("weekly_rsi_consec_low", -1)   # -1 = sentinel for missing key (99 is a valid real count)
        if _consec == -1:
            log.warning("weekly_gate.consec_key_missing", symbol="unknown", note="defaulting to max compression")
            _consec = 20  # treat missing as confirmed downtrend → max compression
        if _consec < 5:
            _mult = 0.65
        elif _consec >= 20:
            _mult = 0.40
        else:
            _mult = 0.65 - 0.25 * (_consec - 5) / 15.0
        fused = 0.5 + (fused - 0.5) * _mult
        fused = float(np.clip(fused, 0.0, 1.0))
        reasons["weekly_gate_fired"] = True
        reasons["weekly_gate_bars"] = _consec
        reasons["weekly_gate_mult"] = round(_mult, 3)
    else:
        reasons["weekly_gate_fired"] = False
        reasons["weekly_gate_bars"] = 0

    # SA-28: Weekly overbought extension gate (SWING/LONG, mirrors the oversold gate above).
    # When weekly RSI > 75 and the weekly trend is up, the stock is in an extended rally —
    # buying here has historically lower forward returns. Compress 15% toward neutral so
    # the bar for a BUY is higher. GROWTH skips this via skip_weekly_gate (momentum names
    # run "overbought" by traditional standards for months). Applied post-cap same as the
    # oversold gate so it cannot be neutralised by accumulated filter boosts.
    if (style_key in ("SWING", "LONG")
            and not p.get("skip_weekly_gate")
            and weekly_rsi is not None
            and weekly_rsi > 75
            and weekly_trend == "up"):
        fused = 0.5 + (fused - 0.5) * 0.85
        fused = float(np.clip(fused, 0.0, 1.0))
        reasons["weekly_overbought_gate"] = True
    else:
        reasons["weekly_overbought_gate"] = False

    # T224-B: HSI downtrend compression for HK stocks. Applied post-cap so it cannot be
    # offset by prior boosts. 20% compression toward neutral when HSI < 20-day SMA.
    # T232-SIG5: bullish-only — an HSI downtrend is bearish confirmation and should not mute
    # a SELL (the market condition that validates the SELL was suppressing it). Mirrors the
    # southbound-flow gate below, which already guards with fused > 0.5.
    if reasons.get("hsi_regime") == "bear" and fused > 0.5:
        fused = 0.5 + (fused - 0.5) * 0.80
        fused = float(np.clip(fused, 0.0, 1.0))
        reasons["hsi_bear_gate"] = True
    else:
        reasons["hsi_bear_gate"] = False

    # T228: HK Connect southbound flow — negative 5d net = mainland selling pressure; compress BUY
    _is_hk_stock = base_reasons.get("hsi_regime") is not None
    if _is_hk_stock and fused > 0.5:
        _flow_net = base_reasons.get("flow_5d_net_hkd")
        if _flow_net is not None and float(_flow_net) < 0:
            fused = 0.5 + (fused - 0.5) * 0.85
            fused = float(np.clip(fused, 0.0, 1.0))
            reasons["hk_southbound_compression"] = True
        else:
            reasons["hk_southbound_compression"] = False
    else:
        reasons["hk_southbound_compression"] = False

    # T228: HK liquidity gate — suppress SWING/GROWTH BUY for thin markets (< HKD 50M/day turnover)
    if base_reasons.get("hk_low_liquidity") and style_key in ("SWING", "GROWTH") and fused > 0.5:
        fused = 0.5 + (fused - 0.5) * 0.30
        fused = float(np.clip(fused, 0.0, 1.0))
        reasons["hk_liquidity_gate"] = True
    else:
        reasons["hk_liquidity_gate"] = False

    signal, horizon, threshold_tier = _decide_style(fused, style_key, market_regime)
    reasons["threshold_tier"] = threshold_tier  # SA-12: log which regime threshold was applied

    # T228: HK SHORT SELL has 29.2% win rate — no edge; emit HOLD instead
    if style_key == "SHORT" and _is_hk_stock and signal == "SELL":
        signal = "HOLD"
        reasons["hk_short_sell_disabled"] = True
    else:
        reasons["hk_short_sell_disabled"] = False
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
    ta_prob, reasons = _ta_score(df, ta_weights=_ta_weights)
    sr_data = _sr_context(df, symbol=symbol)

    # T228: HK liquidity filter — flag stocks with avg 20d daily turnover < HKD 50M
    _hk_low_liquidity = False
    if symbol.upper().endswith(".HK") and len(df) >= 20:
        _close_vals = _adj_close(df)
        _vol_vals = df["volume"].astype(float)
        _daily_turnover_20d = float((_close_vals.iloc[-20:] * _vol_vals.iloc[-20:]).mean())
        _hk_low_liquidity = _daily_turnover_20d < 50_000_000
    reasons["hk_low_liquidity"] = _hk_low_liquidity
    # Per-style ML fetched in parallel — 4 sequential calls with 10s timeout each would add
    # up to 120s worst-case when ML is slow; parallel fetch caps worst-case at 30s.
    _ml_styles = ("SHORT", "SWING", "LONG", "GROWTH")
    _ml_futures = {sk: _ML_EXECUTOR.submit(_fetch_ml_data, symbol, sk) for sk in _ml_styles}
    ml_by_style: dict[str, tuple[float | None, float, dict]] = {
        sk: f.result() for sk, f in _ml_futures.items()
    }
    ml_prob, ml_test_auc, ml_meta = ml_by_style["SWING"]  # canonical for shared reasons
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
    # ml_probability is per-style — written inside _apply_style_signal() for each horizon
    reasons["ml_test_auc"]        = ml_test_auc
    reasons["ml_model"]           = ml_meta.get("ml_model")
    reasons["ml_agreement"]       = ml_meta.get("ml_agreement")
    reasons["ml_model_probs"]     = ml_meta.get("ml_model_probs")
    # ml_oos_suppressed is per-style — written inside _apply_style_signal from the style-specific
    # ml_meta so each horizon's stored signal reflects its own model's OOS status, not SWING's.
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
    # ATR-14 and last price — used by decision-engine for ATR-based game plan stops
    # AUD-DUPLOGIC: this was a THIRD independent inline TR/ATR copy in this same file, and the
    # one that had NOT received the AUD232-073 min_periods=14 fix already applied to _adx() and
    # _supertrend() above — a real, silently-recurring instance of the same bug class within one
    # file, caught only by this consolidation pass. Now delegates to the same canonical atr()
    # every other call site in this file uses.
    _close = _adj_close(df)
    _high = df["high"].astype(float)
    _low = df["low"].astype(float)
    _atr_series = _canon_atr(_high, _low, _close, period=14)
    _last_price = float(_close.iloc[-1])
    _atr_14 = float(_atr_series.iloc[-1]) if not pd.isna(_atr_series.iloc[-1]) else None
    reasons["last_price"] = round(_last_price, 4)
    reasons["atr_14"] = round(_atr_14, 4) if _atr_14 is not None else None
    # Use `is not None` not truthiness — _atr_14 == 0.0 is falsy but is a valid zero-ATR measurement.
    reasons["atr_14_pct"] = round(_atr_14 / _last_price, 4) if (_atr_14 is not None and _last_price > 0) else None

    # T208: 8-K filing flag — check for recent material SEC filings via direct DB query.
    # Querying the shared PostgreSQL sec_filings table directly avoids an HTTP hop to
    # event-intelligence and adds zero latency to signal generation.
    # Fail-open: any exception leaves eight_k_flag=None so signal generation is never blocked.
    try:
        from db import SessionLocal
        from sqlalchemy import text as _text_8k
        with SessionLocal() as _db_8k:
            # Fetch the most recent material filing within the last 7 days
            _row_material = _db_8k.execute(
                _text_8k("""
                    SELECT filed_date, form FROM sec_filings
                    WHERE symbol = :sym
                      AND filed_date >= now() - interval '7 days'
                      AND is_material = true
                    ORDER BY filed_date DESC
                    LIMIT 1
                """),
                {"sym": symbol},
            ).fetchone()
            if _row_material:
                reasons["eight_k_flag"] = True
                reasons["eight_k_date"] = str(_row_material[0])
                reasons["eight_k_form"] = _row_material[1] or "8-K"
            else:
                # Check for any 8-K (even non-material) in the past 7 days
                _row_any = _db_8k.execute(
                    _text_8k("""
                        SELECT filed_date, form FROM sec_filings
                        WHERE symbol = :sym
                          AND filed_date >= now() - interval '7 days'
                        ORDER BY filed_date DESC
                        LIMIT 1
                    """),
                    {"sym": symbol},
                ).fetchone()
                if _row_any:
                    reasons["eight_k_flag"] = True
                    reasons["eight_k_date"] = str(_row_any[0])
                    reasons["eight_k_form"] = _row_any[1] or "8-K"
                else:
                    reasons["eight_k_flag"] = False
                    reasons["eight_k_date"] = None
                    reasons["eight_k_form"] = None
    except Exception:
        reasons["eight_k_flag"] = None
        reasons["eight_k_date"] = None
        reasons["eight_k_form"] = None

    # T220-E: 13F institutional ownership QoQ change — detect smart-money accumulation.
    # Queries institutional_holdings directly (no HTTP hop). Compares the two most recent
    # quarterly period_dates; sets inst_change_pct (+%) and inst_ownership_increased=True
    # when institutions collectively increased their holdings by >5% QoQ.
    try:
        from db import SessionLocal as _SL_inst
        from sqlalchemy import text as _text_inst
        with _SL_inst() as _db_inst:
            _inst_rows = _db_inst.execute(
                _text_inst("""
                    SELECT ih.period_date, SUM(ih.shares) AS total_shares
                    FROM institutional_holdings ih
                    JOIN stocks s ON s.id = ih.stock_id
                    WHERE s.symbol = :sym
                    GROUP BY ih.period_date
                    ORDER BY ih.period_date DESC
                    LIMIT 2
                """),
                {"sym": symbol.upper()},
            ).fetchall()
        if len(_inst_rows) >= 2:
            _latest_sh, _prior_sh = float(_inst_rows[0][1] or 0), float(_inst_rows[1][1] or 0)
            if _prior_sh > 0:
                _inst_chg = (_latest_sh - _prior_sh) / _prior_sh * 100
                reasons["inst_change_pct"] = round(_inst_chg, 1)
                reasons["inst_ownership_increased"] = _inst_chg > 5.0
            else:
                reasons["inst_change_pct"] = None
                reasons["inst_ownership_increased"] = False
        else:
            reasons["inst_change_pct"] = None
            reasons["inst_ownership_increased"] = False
    except Exception:
        reasons["inst_change_pct"] = None
        reasons["inst_ownership_increased"] = False

    # T220-F: Earnings revision momentum — recommendation_mean direction from weekly snapshots.
    # Same logic as builder.py: recommendation_mean lower = more bullish (1=strong buy, 5=sell).
    # Delta >0.15 means analysts have upgraded on net → +1; <-0.15 = downgrade → -1.
    try:
        from db import SessionLocal as _SL_eps
        from sqlalchemy import text as _text_eps
        with _SL_eps() as _db_eps:
            _snaps = _db_eps.execute(
                _text_eps("""
                    SELECT recommendation_mean
                    FROM fundamentals_snapshot
                    WHERE symbol = :sym
                    ORDER BY snapshot_date DESC
                    LIMIT 8
                """),
                {"sym": symbol.upper()},
            ).fetchall()
        if len(_snaps) >= 2 and _snaps[0][0] is not None and _snaps[-1][0] is not None:
            _rec_delta = float(_snaps[-1][0]) - float(_snaps[0][0])  # old - recent
            reasons["eps_revision_direction"] = 1 if _rec_delta > 0.15 else (-1 if _rec_delta < -0.15 else 0)
        else:
            reasons["eps_revision_direction"] = None
    except Exception:
        reasons["eps_revision_direction"] = None

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

    # T209: HKEX Stock Connect southbound flow enrichment for HK stocks.
    # market-data exposes a public endpoint (/stocks/hk-connect-flow/{symbol})
    # that returns a rolling flow summary from the hk_connect_flows table.
    # Hard timeout of 2s; any failure is silently swallowed so signal generation
    # is never blocked by a missing or slow flow endpoint.
    if symbol.upper().endswith(".HK"):
        try:
            import httpx as _httpx
            _rflow = _httpx.get(
                f"{_settings.market_data_url}/stocks/hk-connect-flow/{symbol.upper()}",
                timeout=2.0,
            )
            if _rflow.status_code == 200:
                _flow = _rflow.json()
                if _flow.get("flow_strength") is not None:
                    reasons["flow_5d_net_hkd"]  = _flow.get("flow_5d_net_hkd")
                    reasons["flow_20d_net_hkd"] = _flow.get("flow_20d_net_hkd")
                    reasons["flow_strength"]     = _flow.get("flow_strength")
        except Exception:
            pass  # flow data is best-effort; never block signal generation

    # T224-B: HSI regime for HK stocks — US SPY/VIX regime is irrelevant for HK timing.
    # Fetches ^HSI and compares to its 20-day SMA. Used in _apply_style_signal to apply
    # a 20% compression toward neutral when HSI is in a downtrend.
    if symbol.upper().endswith(".HK"):
        reasons["hsi_regime"] = _fetch_hsi_regime()

    # T220-C: Simple squeeze score from existing reasons data.
    # reasons["short_pct_float"] is already in percentage form (e.g. 15.0 = 15% of float shorted).
    # reasons["short_ratio"] is days-to-cover.
    _sp_float = reasons.get("short_pct_float")
    _s_ratio = reasons.get("short_ratio")
    if _sp_float is not None:
        try:
            _sp = float(_sp_float)
            _sr = float(_s_ratio) if _s_ratio is not None else 5.0
            # Simple composite: 60% SI% + 40% days-to-cover (normalized to 15-day max)
            _squeeze = min(100, (_sp * 0.6) + (min(_sr, 15) / 15 * 100 * 0.4))
            if _squeeze >= 40:
                reasons["squeeze_score"] = round(_squeeze, 1)
        except Exception:
            pass

    # SA-13: GROWTH style uses an adjusted TA score that de-penalises momentum RSI and
    # substitutes SMA20>SMA50 for the SMA50>SMA200 structural requirement.
    ta_prob_growth = float(np.clip(ta_prob + _growth_ta_adjustment(df, reasons), 0.0, 1.0))

    def _make_signal(style_key: str) -> "AIConfidence":
        _ml_prob, _ml_auc, _ml_m = ml_by_style[style_key]
        tp = ta_prob_growth if style_key == "GROWTH" else ta_prob
        return _apply_style_signal(
            ta_prob=tp,
            ml_prob=_ml_prob,
            ml_test_auc=_ml_auc,
            style_key=style_key,
            market_regime=market_regime,
            adx_val=reasons.get("adx"),
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
            ml_oos_suppressed=_ml_m.get("ml_oos_suppressed", False),
        )

    return {k: _make_signal(k) for k in ("SHORT", "SWING", "LONG", "GROWTH")}


def generate_signal(symbol: str) -> AIConfidence:
    """Generate the SWING signal for a symbol — backwards-compatible wrapper."""
    return generate_all_signals(symbol)["SWING"]
