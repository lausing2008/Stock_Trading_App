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
