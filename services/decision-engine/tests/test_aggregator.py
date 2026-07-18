"""Regression tests for T247-DECISIONENGINE-STYLEPARAMS-BLOCKING.

build_game_plan() is called directly (unawaited) from async def _decide() (routes.py). On the
signal-reasons-missing path it falls through to _default_game_plan() -> _get_style_params(),
which does a blocking httpx.get() to market-data on a cache miss — same event-loop-stall class
as regime.py's get_regime() bug. abuild_game_plan() runs the whole (unmodified) sync
build_game_plan() via run_in_executor() so a cache miss only stalls the awaiting request.

No pytest-asyncio available locally — async behavior is driven directly via asyncio.run()
inside plain sync test functions, matching test_regime.py's pattern.
"""
import asyncio
import sys
import time
from unittest.mock import MagicMock

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())

import src.api.core.aggregator as aggregator  # noqa: E402


def _reset_style_params_cache():
    aggregator._STYLE_PARAMS_CACHE = None
    aggregator._STYLE_PARAMS_TS = 0.0


def test_abuild_game_plan_runs_the_blocking_fetch_off_the_event_loop(monkeypatch):
    """Core regression guard: a slow synchronous style-params fetch inside abuild_game_plan()
    must not block other coroutines scheduled on the same event loop."""
    _reset_style_params_cache()

    def _slow_get_style_params():
        time.sleep(0.2)
        return aggregator._STYLE_PARAMS_FALLBACK

    monkeypatch.setattr(aggregator, "_get_style_params", _slow_get_style_params)

    timeline = []

    async def _tracer():
        await asyncio.sleep(0.01)
        timeline.append(time.monotonic())

    async def _main():
        start = time.monotonic()
        # signal_data=None forces the _default_game_plan() -> _get_style_params() path.
        await asyncio.gather(
            aggregator.abuild_game_plan(100.0, "SWING", None),
            _tracer(),
        )
        return start

    start = asyncio.run(_main())
    tracer_ts = timeline[0]
    assert tracer_ts - start < 0.15, (
        f"tracer took {tracer_ts - start:.3f}s — the event loop was blocked by the style-params "
        "fetch instead of it running in a thread pool"
    )


def test_abuild_game_plan_returns_the_same_result_as_the_sync_version():
    """abuild_game_plan() must be a faithful async wrapper — not a reimplementation that could
    silently drift from build_game_plan()'s real logic."""
    _reset_style_params_cache()
    aggregator._STYLE_PARAMS_CACHE = aggregator._STYLE_PARAMS_FALLBACK
    aggregator._STYLE_PARAMS_TS = time.time()

    signal_data = {"reasons": {}}  # incomplete reasons -> falls through to _default_game_plan
    sync_result = aggregator.build_game_plan(100.0, "SWING", signal_data)
    async_result = asyncio.run(aggregator.abuild_game_plan(100.0, "SWING", signal_data))
    assert async_result == sync_result


def test_abuild_game_plan_uses_signal_reasons_when_complete_no_network_call(monkeypatch):
    """The common case — full game-plan reasons already present — must never touch
    _get_style_params()/network at all, sync or async."""
    called = {"n": 0}

    def _should_not_be_called():
        called["n"] += 1
        return aggregator._STYLE_PARAMS_FALLBACK

    monkeypatch.setattr(aggregator, "_get_style_params", _should_not_be_called)

    signal_data = {"reasons": {
        "entry2": 101.0, "breakout": 103.5, "stop": 88.0, "take_profit": 135.0, "target_1": 118.0,
    }}
    result = asyncio.run(aggregator.abuild_game_plan(100.0, "SWING", signal_data))
    assert called["n"] == 0
    assert result["stop"] == 88.0


def test_abuild_game_plan_uses_a_dedicated_executor_not_yf_executor():
    """AUD250-DECISIONENGINE-GAMEPLAN-SHARED-EXECUTOR regression guard: abuild_game_plan()
    previously shared _yf_executor (the 4-worker pool built for the unrelated yfinance-
    price-fallback path) — a distinct kind of blocking work contending for the same small
    pool undercuts the parallelism a batch /decide/batch request is supposed to get. Must use
    its own dedicated pool, matching regime.py's _regime_executor fix for the identical
    cross-purpose-contention pattern.

    Asserts the ACTUAL executor abuild_game_plan() submits work to (via a spy on
    run_in_executor), not just that two distinct pool objects happen to exist — the two
    pools existing side by side proves nothing if the code still passes _yf_executor to
    run_in_executor()."""
    _reset_style_params_cache()
    aggregator._STYLE_PARAMS_CACHE = aggregator._STYLE_PARAMS_FALLBACK
    aggregator._STYLE_PARAMS_TS = time.time()

    seen_executors = []

    async def _main():
        real_loop = asyncio.get_running_loop()
        real_run_in_executor = real_loop.run_in_executor

        class _SpyLoop:
            def run_in_executor(self, executor, func, *args):
                seen_executors.append(executor)
                return real_run_in_executor(executor, func, *args)

        import unittest.mock as mock
        with mock.patch.object(aggregator.asyncio, "get_running_loop", lambda: _SpyLoop()):
            await aggregator.abuild_game_plan(100.0, "SWING", {"reasons": {}})

    asyncio.run(_main())

    assert seen_executors == [aggregator._game_plan_executor]
    assert aggregator._yf_executor not in seen_executors


def test_game_plan_executor_does_not_contend_with_concurrent_yf_price_fetches():
    """Concrete demonstration of the fix's actual benefit: with _yf_executor's own worker
    pool fully SATURATED by other yfinance work, a concurrent game-plan fetch must still
    complete promptly — proving it runs on an independent pool rather than queuing behind
    yfinance work. (A non-saturating scenario proves nothing: _yf_executor has 4 workers,
    so two or three concurrent tasks fit without ever actually contending even if they DID
    share the pool — this test deliberately submits more blocking tasks than _yf_executor
    has workers, so sharing would be forced to queue and become visible.)

    Each saturating task self-releases after a short, fixed delay (rather than waiting on a
    manually-set flag) so this test can never hang even if the assertion below would fail —
    a hung test is a much worse failure mode than a fast, clear assertion error."""
    _reset_style_params_cache()

    n_yf_workers = aggregator._yf_executor._max_workers
    saturating_delay = 0.3  # comfortably longer than the < 0.15s assertion below

    def _blocking_yf_task():
        time.sleep(saturating_delay)
        return 1.0

    def _fast_get_style_params():
        return aggregator._STYLE_PARAMS_FALLBACK

    import src.api.core.aggregator as agg_mod
    monkeypatch_targets = [(agg_mod, "_get_style_params", _fast_get_style_params)]
    originals = [getattr(mod, name) for mod, name, _ in monkeypatch_targets]
    for mod, name, val in monkeypatch_targets:
        setattr(mod, name, val)

    # Saturate every _yf_executor worker with a task that self-releases after a fixed delay.
    saturating_futures = [
        agg_mod._yf_executor.submit(_blocking_yf_task) for _ in range(n_yf_workers)
    ]
    try:
        async def _main():
            start = time.monotonic()
            await aggregator.abuild_game_plan(100.0, "SWING", None)
            return time.monotonic() - start

        elapsed = asyncio.run(_main())
        assert elapsed < 0.15, (
            f"abuild_game_plan() took {elapsed:.3f}s while _yf_executor was fully saturated "
            "— it queued behind unrelated yfinance work instead of running on an independent "
            "thread pool"
        )
    finally:
        for f in saturating_futures:
            f.result(timeout=2)
        for (mod, name, _), original in zip(monkeypatch_targets, originals):
            setattr(mod, name, original)
