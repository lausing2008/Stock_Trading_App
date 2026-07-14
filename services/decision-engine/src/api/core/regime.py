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

import asyncio
import time as _time
from concurrent.futures import ThreadPoolExecutor

import httpx
import structlog

from common.config import get_settings

log = structlog.get_logger()
_settings = get_settings()

# T247-DECISIONENGINE-REGIME-BLOCKING: get_regime() is called unawaited from inside
# async def _decide() (routes.py) — a blocking httpx.get() there stalls the ENTIRE event
# loop, not just the calling task. A cold/expired cache hit during a 30-symbol /decide/batch
# gather() serializes every concurrent request for up to the 10s timeout, exactly the
# pattern aggregator.py's own yfinance fallback already runs in a thread pool to avoid.
# Same fix here: run the blocking fetch in a dedicated executor.
_regime_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="regime_fetch")

_CACHE_TTL = 300  # 5 minutes — matches market-data's _refresh_5m cadence (T232-DE7). Was 900s;
# after T232-DE7 added 2-tick hysteresis to market-data's own classification, a genuine
# confirmed regime change could still take up to ~10 extra minutes to reach this cache on top
# of that. 5 minutes keeps this display in step with the underlying refresh rate without
# hammering market-data with a fetch on every Entry Gate page load.

_US_CACHE: dict = {}
_US_TS: float = 0.0
_HK_CACHE: dict = {}
_HK_TS: float = 0.0

_NEUTRAL = {"state": "neutral", "vix": None, "notes": ["regime unavailable"]}


def get_regime(market: str = "US") -> dict:
    """Return current market regime dict. Uses cache; re-fetches after TTL.

    Synchronous — safe to call from plain `def` routes (FastAPI runs those in their own
    thread pool already) but NOT from `async def` code, where a cache-miss would block the
    single event loop thread. Async callers must use `aget_regime()` instead.
    """
    if market.upper() == "HK":
        return _get_cached("HK")
    return _get_cached("US")


async def aget_regime(market: str = "US") -> dict:
    """Async counterpart of get_regime() — the blocking fetch runs in a thread pool so a
    cache miss stalls only the awaiting task, not the whole event loop. Use this from any
    `async def` caller (e.g. routes.py's _decide(), which fans out many concurrent symbols
    via asyncio.gather and would otherwise serialize on the first cold cache hit)."""
    market_key = "HK" if market.upper() == "HK" else "US"
    cache, ts = (_HK_CACHE, _HK_TS) if market_key == "HK" else (_US_CACHE, _US_TS)
    if cache and (_time.time() - ts) < _CACHE_TTL:
        return dict(cache)
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_regime_executor, _get_cached, market_key)


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
