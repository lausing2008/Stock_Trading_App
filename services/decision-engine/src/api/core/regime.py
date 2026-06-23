"""Market regime detection with local in-memory cache (4-hour TTL).

Mirrors the logic in paper_trading_engine._fetch_market_regime() so the Decision
Engine is self-contained and doesn't need a dedicated regime endpoint on market-data.
"""
from __future__ import annotations

import time as _time

import structlog

log = structlog.get_logger()

_CACHE_TTL = 14_400  # 4 hours

_US_CACHE: dict = {}
_US_TS: float = 0.0
_HK_CACHE: dict = {}
_HK_TS: float = 0.0

_NEUTRAL = {"state": "neutral", "vix": None, "notes": ["regime unavailable"]}


def get_regime(market: str = "US") -> dict:
    """Return current market regime dict. Uses cache; re-fetches after TTL."""
    if market.upper() == "HK":
        return _get_hk()
    return _get_us()


def _get_us() -> dict:
    global _US_CACHE, _US_TS
    if _US_CACHE and (_time.time() - _US_TS) < _CACHE_TTL:
        return dict(_US_CACHE)
    try:
        result = _compute_us()
        _US_CACHE = dict(result)
        _US_TS = _time.time()
        log.info("decision.regime_refreshed", market="US", state=result.get("state"))
        return result
    except Exception as exc:
        log.warning("decision.regime_fetch_failed", market="US", error=str(exc))
        return _US_CACHE if _US_CACHE else dict(_NEUTRAL)


def _get_hk() -> dict:
    global _HK_CACHE, _HK_TS
    if _HK_CACHE and (_time.time() - _HK_TS) < _CACHE_TTL:
        return dict(_HK_CACHE)
    try:
        result = _compute_hk()
        _HK_CACHE = dict(result)
        _HK_TS = _time.time()
        log.info("decision.regime_refreshed", market="HK", state=result.get("state"))
        return result
    except Exception as exc:
        log.warning("decision.regime_fetch_failed", market="HK", error=str(exc))
        return _HK_CACHE if _HK_CACHE else dict(_NEUTRAL)


def _compute_us() -> dict:
    import yfinance as yf

    result: dict = {
        "state": "neutral", "vix": None, "vix9d": None,
        "spy_price": None, "spy_ema20": None, "spy_ema50": None, "spy_ema200": None,
        "spy_20d_ret": None, "qqq_price": None, "qqq_ema50": None,
        "vix_5d_trend": None, "vix_term_inverted": False,
        "breadth_weak": False, "breadth_size_mult": 1.0,
        "is_pre_choppy": False, "is_pre_risk_off": False,
        "notes": [],
    }

    raw = yf.download(
        ["SPY", "QQQ", "^VIX", "^VIX9D", "IWM", "MDY"],
        period="300d", auto_adjust=True, progress=False,
    )
    closes = raw["Close"] if "Close" in raw.columns else raw

    def _s(sym):
        return closes[sym].dropna() if sym in closes.columns else None

    spy_s   = _s("SPY")
    qqq_s   = _s("QQQ")
    vix_s   = _s("^VIX")
    vix9d_s = _s("^VIX9D")
    iwm_s   = _s("IWM")
    mdy_s   = _s("MDY")

    if spy_s is not None and len(spy_s) >= 20:
        result["spy_price"]  = float(spy_s.iloc[-1])
        result["spy_ema20"]  = float(spy_s.ewm(span=20, adjust=False).mean().iloc[-1])
        result["spy_ema50"]  = float(spy_s.ewm(span=50, adjust=False).mean().iloc[-1])
        if len(spy_s) >= 200:
            result["spy_ema200"] = float(spy_s.ewm(span=200, adjust=False).mean().iloc[-1])
        result["spy_20d_ret"] = round((float(spy_s.iloc[-1]) / float(spy_s.iloc[-20]) - 1) * 100, 2)

    if qqq_s is not None and len(qqq_s) >= 50:
        result["qqq_price"] = float(qqq_s.iloc[-1])
        result["qqq_ema50"] = float(qqq_s.ewm(span=50, adjust=False).mean().iloc[-1])

    if vix_s is not None and len(vix_s) >= 1:
        vix_now = float(vix_s.iloc[-1])
        result["vix"] = vix_now
        if len(vix_s) >= 6:
            vix_5d = float(vix_s.iloc[-6])
            if vix_now > vix_5d * 1.08:
                result["vix_5d_trend"] = "rising"
            elif vix_now < vix_5d * 0.92:
                result["vix_5d_trend"] = "falling"
            else:
                result["vix_5d_trend"] = "flat"

    if vix9d_s is not None and len(vix9d_s) >= 1 and result["vix"]:
        vix9d_val = float(vix9d_s.iloc[-1])
        result["vix9d"] = vix9d_val
        if result["vix"] and vix9d_val / result["vix"] > 1.10:
            result["vix_term_inverted"] = True

    # Market breadth
    iwm_below = mdy_below = False
    if iwm_s is not None and len(iwm_s) >= 200:
        iwm_ema200 = float(iwm_s.ewm(span=200, adjust=False).mean().iloc[-1])
        iwm_below  = float(iwm_s.iloc[-1]) < iwm_ema200
    if mdy_s is not None and len(mdy_s) >= 200:
        mdy_ema200 = float(mdy_s.ewm(span=200, adjust=False).mean().iloc[-1])
        mdy_below  = float(mdy_s.iloc[-1]) < mdy_ema200
    if iwm_below and mdy_below:
        result["breadth_weak"] = True
        result["breadth_size_mult"] = 0.60
    elif iwm_below or mdy_below:
        result["breadth_size_mult"] = 0.80

    # Classify state
    spy   = result["spy_price"]
    e20   = result["spy_ema20"]
    e50   = result["spy_ema50"]
    e200  = result["spy_ema200"]
    vix   = result["vix"]
    notes = result["notes"]

    VIX_HIGH = 25.0
    VIX_FEAR = 30.0

    if vix and vix >= VIX_FEAR and spy and e50 and spy < e50:
        result["state"] = "bear"
        notes.append(f"Bear: VIX={vix:.1f} ≥ {VIX_FEAR} and SPY < EMA50")
    elif vix and vix >= VIX_FEAR:
        result["state"] = "risk_off"
        notes.append(f"Risk-off: VIX={vix:.1f} ≥ {VIX_FEAR}")
    elif vix and vix >= VIX_HIGH and spy and e50 and spy < e50:
        result["state"] = "risk_off"
        notes.append(f"Risk-off: VIX={vix:.1f} ≥ {VIX_HIGH} with SPY < EMA50")
    elif spy and e200 and spy < e200:
        result["state"] = "bear"
        notes.append("Bear: SPY below 200EMA")
    elif spy and e50 and spy < e50:
        result["state"] = "choppy"
        notes.append("Choppy: SPY below 50EMA")
    elif spy and e20 and spy < e20 * 1.005:
        result["state"] = "choppy"
        notes.append("Choppy: SPY hugging EMA20")
    elif spy and e200 and e50 and spy > e200 and spy > e50:
        result["state"] = "bull"
        notes.append("Bull: SPY above 200EMA and 50EMA")
    else:
        result["state"] = "neutral"
        notes.append("Neutral: mixed signals")

    # Pre-regime early-warning flags (F11) — only relevant in bull/neutral
    if result["state"] in ("bull", "neutral"):
        if spy and e50 and 1.0 < spy / e50 <= 1.03:
            result["is_pre_choppy"] = True
            notes.append(f"Pre-choppy: SPY within 3% above EMA50 ({spy / e50 - 1:.1%})")
        if vix and 18 < vix < 25 and result.get("vix_5d_trend") == "rising":
            result["is_pre_risk_off"] = True
            notes.append(f"Pre-risk-off: VIX={vix:.1f} rising into warning zone")

    return result


def _compute_hk() -> dict:
    import yfinance as yf

    result: dict = {
        "state": "neutral", "vix": None, "notes": [],
        "hsi_price": None, "hsi_ema200": None,
    }
    try:
        raw = yf.download("^HSI", period="300d", auto_adjust=True, progress=False)
        closes = raw["Close"].dropna() if "Close" in raw.columns else raw.dropna()
        if len(closes) >= 200:
            hsi   = float(closes.iloc[-1])
            e200  = float(closes.ewm(span=200, adjust=False).mean().iloc[-1])
            result["hsi_price"]   = hsi
            result["hsi_ema200"]  = e200
            if hsi < e200 * 0.97:
                result["state"] = "bear"
                result["notes"].append(f"HK Bear: HSI={hsi:.0f} < EMA200={e200:.0f}")
            elif hsi < e200:
                result["state"] = "choppy"
                result["notes"].append(f"HK Choppy: HSI slightly below EMA200")
            else:
                result["state"] = "bull"
                result["notes"].append(f"HK Bull: HSI above EMA200")
    except Exception as exc:
        result["notes"].append(f"HK regime fetch failed: {exc}")
    return result
