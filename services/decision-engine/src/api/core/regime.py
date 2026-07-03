"""Market regime detection — calls market-data's GET /stocks/regime endpoint.

T232-DL-REGIME5X: this module previously re-implemented paper_trading_engine's regime
classifier from scratch ("mirrors the logic... so the Decision Engine is self-contained").
That mirror silently drifted from the real trading engine (different CHOPPY rule, different
failure default — neutral here vs. choppy there, no HK breadth confirmation, no HMM overlay)
— found during the 2026-07-03 deep logic review as one of five independent regime classifiers
in the codebase. Fixed 2026-07-04 by calling market-data's regime directly instead of
maintaining a second copy — market-data's paper_trading_engine.py is the actual system gating
real trades, so it is the correct source of truth for every other consumer.
"""
from __future__ import annotations

import time as _time

import httpx
import structlog

from common.config import get_settings

log = structlog.get_logger()
_settings = get_settings()

_CACHE_TTL = 900  # 15 minutes — regime can shift within a session (QW-5)

_US_CACHE: dict = {}
_US_TS: float = 0.0
_HK_CACHE: dict = {}
_HK_TS: float = 0.0

_NEUTRAL = {"state": "neutral", "vix": None, "notes": ["regime unavailable"]}


def get_regime(market: str = "US") -> dict:
    """Return current market regime dict. Uses cache; re-fetches after TTL."""
    if market.upper() == "HK":
        return _get_cached("HK")
    return _get_cached("US")


def _get_cached(market: str) -> dict:
    global _US_CACHE, _US_TS, _HK_CACHE, _HK_TS
    cache, ts = (_HK_CACHE, _HK_TS) if market == "HK" else (_US_CACHE, _US_TS)
    if cache and (_time.time() - ts) < _CACHE_TTL:
        return dict(cache)
    try:
        result = _fetch_from_market_data(market)
        if market == "HK":
            _HK_CACHE, _HK_TS = dict(result), _time.time()
        else:
            _US_CACHE, _US_TS = dict(result), _time.time()
        log.info("decision.regime_refreshed", market=market, state=result.get("state"))
        return result
    except Exception as exc:
        log.warning("decision.regime_fetch_failed", market=market, error=str(exc))
        return dict(cache) if cache else dict(_NEUTRAL)


def _fetch_from_market_data(market: str) -> dict:
    url = f"{_settings.market_data_url}/stocks/regime"
    r = httpx.get(url, params={"market": market}, timeout=10.0)
    r.raise_for_status()
    result = r.json()
    # market-data's regime dict doesn't carry a pre-computed vix_size_mult — it applies the same
    # VIX gradient formula inline at call time instead of storing it. Compute it here too so
    # decision-engine's routes.py (which reads regime["vix_size_mult"]) keeps working, using the
    # EXACT same formula as market-data (paper_trading_engine.py, T192/HIGH-4) rather than a
    # second hand-copied constant — this was previously two independently-written copies of the
    # identical formula, kept in sync only by a code comment referencing the other file.
    vix = result.get("vix")
    result["vix_size_mult"] = round(max(0.5, 1.0 - max(0.0, (float(vix) - 20.0) / 30.0)), 3) if vix is not None else 1.0
    return result
