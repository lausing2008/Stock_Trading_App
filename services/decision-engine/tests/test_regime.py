"""Regression tests for T247-DECISIONENGINE-REGIME-BLOCKING.

get_regime() makes a blocking httpx.get() call and was being invoked directly (unawaited)
from inside async def _decide() (routes.py) — a cache miss there stalls the entire asyncio
event loop, not just the calling task, serializing every other concurrent request
(/decide/batch fans out via asyncio.gather over many symbols at once). aget_regime() is the
fix: the blocking fetch now runs in a dedicated ThreadPoolExecutor via run_in_executor(),
matching the pattern aggregator.py's own yfinance fallback already uses.

No pytest-asyncio available locally — async behavior is driven directly via asyncio.run()
inside plain sync test functions.
"""
import asyncio
import sys
import time
from unittest.mock import MagicMock

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())

import src.api.core.regime as regime  # noqa: E402


def _reset_caches():
    regime._US_CACHE = {}
    regime._US_TS = 0.0
    regime._HK_CACHE = {}
    regime._HK_TS = 0.0


def test_aget_regime_runs_the_blocking_fetch_in_a_thread_pool_not_on_the_event_loop(monkeypatch):
    """Core regression guard: a slow synchronous fetch inside aget_regime() must not block
    other coroutines scheduled on the same event loop. Simulated by having the (monkeypatched)
    blocking fetch sleep for 0.2s while a concurrent, otherwise-instant coroutine runs
    alongside it — if aget_regime() blocked the loop, the tracer coroutine would only get to
    record its timestamp AFTER the fetch finishes; with the executor fix, it records almost
    immediately."""
    _reset_caches()

    def _slow_fetch(market):
        time.sleep(0.2)
        return {"state": "neutral", "vix": 15.0}

    monkeypatch.setattr(regime, "_fetch_from_market_data", _slow_fetch)

    timeline = []

    async def _tracer():
        # Give aget_regime()'s executor call a moment to actually start before recording.
        await asyncio.sleep(0.01)
        timeline.append(("tracer_ran", time.monotonic()))

    async def _main():
        start = time.monotonic()
        gather_results = await asyncio.gather(regime.aget_regime("US"), _tracer())
        return gather_results, start

    gather_results, start = asyncio.run(_main())
    regime_result = gather_results[0]
    tracer_ts = timeline[0][1]
    # The tracer coroutine (only a 0.01s sleep) must complete well before the 0.2s fetch does —
    # proving the fetch ran off the event loop thread rather than blocking it.
    assert tracer_ts - start < 0.15, (
        f"tracer took {tracer_ts - start:.3f}s to run — the event loop was blocked by the "
        "regime fetch instead of it running in a thread pool"
    )
    assert regime_result["state"] == "neutral"


def test_aget_regime_two_concurrent_calls_do_not_serialize(monkeypatch):
    """Two concurrent aget_regime() calls (e.g. US market decisions fanning out via
    asyncio.gather in /decide/batch) hitting a cold cache must run concurrently, not
    back-to-back — total wall time should be close to ONE fetch's duration, not two."""
    _reset_caches()

    def _slow_fetch(market):
        time.sleep(0.15)
        return {"state": "neutral", "vix": 15.0}

    monkeypatch.setattr(regime, "_fetch_from_market_data", _slow_fetch)

    async def _main():
        start = time.monotonic()
        # Two distinct markets so both genuinely miss cache (US and HK caches are independent).
        await asyncio.gather(regime.aget_regime("US"), regime.aget_regime("HK"))
        return time.monotonic() - start

    elapsed = asyncio.run(_main())
    # Sequential would take ~0.30s; concurrent (2 worker threads) should take ~0.15s.
    assert elapsed < 0.25, f"two concurrent regime fetches took {elapsed:.3f}s — not running concurrently"


def test_aget_regime_returns_cached_value_without_hitting_executor_when_fresh(monkeypatch):
    """A warm cache must short-circuit entirely — no thread pool dispatch at all."""
    _reset_caches()
    regime._US_CACHE = {"state": "risk_on", "vix": 12.0}
    regime._US_TS = time.time()

    called = {"n": 0}

    def _should_not_be_called(market):
        called["n"] += 1
        return {"state": "neutral", "vix": 15.0}

    monkeypatch.setattr(regime, "_fetch_from_market_data", _should_not_be_called)

    result = asyncio.run(regime.aget_regime("US"))
    assert result["state"] == "risk_on"
    assert called["n"] == 0


def test_aget_regime_and_get_regime_share_the_same_cache():
    """The async and sync variants must read/write the SAME module-level cache dict — a
    regime fetched via one path should be visible to the other (e.g. get_regime() is still
    used by the sync /decide/regime route while _decide() uses aget_regime())."""
    _reset_caches()
    regime._US_CACHE = {"state": "choppy", "vix": 18.0}
    regime._US_TS = time.time()

    assert regime.get_regime("US")["state"] == "choppy"
    assert asyncio.run(regime.aget_regime("US"))["state"] == "choppy"
