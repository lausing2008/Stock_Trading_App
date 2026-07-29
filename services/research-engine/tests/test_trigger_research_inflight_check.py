"""Tests for CLAUDE-API-COST-AUDIT (2026-07-28) — trigger_research()'s new _inflight_research
read, added to close half of a real production cost leak (72 real full-Sonnet report
generations/24h found in a usage audit).

Root cause: trigger_research()'s "6h cooldown" was a plain in-memory dict age-check
(_cache.get(sym)), not an actual lock — several near-simultaneous /trigger calls for the same
symbol (from market-data's _auto_trigger_research, before ITS OWN dedup-by-symbol fix) could
all read _cache before any of their background tasks had written back to it, so all of them
passed the check and each scheduled a real, full Sonnet generation.

Fix: trigger_research() now also checks _inflight_research — the SAME dict generate_research()
already uses to dedupe concurrent generations — and skips scheduling a new background task if
the symbol is already registered there.

Deliberately READ-ONLY: the fix must NEVER write to _inflight_research itself. A first draft
of this fix synchronously pre-registered an Event in _inflight_research right before scheduling
the background task — this deadlocks, because the background task (_generate_with_service_token)
makes a REAL, separate outbound HTTP POST back to this same service's /research/{sym} endpoint,
which re-enters generate_research() fresh. That fresh call's own `if sym in _inflight_research`
check finds the pre-registered entry and takes the "someone else is already generating this —
wait for them" branch — but nothing ever calls .set() on that Event, since the real generation
logic never runs. The request hangs for the full 60s timeout before falling through anyway.

trigger_research() is decorated with @router.post(...) — router is a MagicMock in this test
environment (fastapi/pydantic stubbed in conftest.py), so the decorated name is itself a mock,
not the real function. Covered via source-text regression checks instead, matching this repo's
established pattern for decorator-wrapped functions that can't be called directly (see
market-data's test_volume_anomaly_alert.py/test_min_ta_score_config_wiring.py).
"""
import pathlib

_routes_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_routes_source = _routes_path.read_text()


def _trigger_research_body() -> str:
    start = _routes_source.index("async def trigger_research(")
    end = _routes_source.index("\nasync def ", start + 1)
    return _routes_source[start:end]


_body = _trigger_research_body()


def test_inflight_check_is_present():
    """The exact fix: a read-only check against _inflight_research, alongside the existing
    _cache age-check, so a symbol whose generation is already underway skips scheduling a
    second background task."""
    assert "if sym in _inflight_research:" in _body
    assert '"already_in_flight"' in _body


def test_inflight_check_runs_before_scheduling_the_background_task():
    """The check must happen BEFORE background_tasks.add_task — checking after would defeat
    the whole point, since a duplicate task would already be scheduled by then."""
    check_idx = _body.index("if sym in _inflight_research:")
    add_task_idx = _body.index("background_tasks.add_task(")
    assert check_idx < add_task_idx


def test_trigger_research_never_writes_to_inflight_research_itself():
    """The critical deadlock-avoidance property: this function must be strictly READ-ONLY
    against _inflight_research. It must never do `_inflight_research[sym] = ...` or call
    `.setdefault(...)` on it — generate_research() is the SOLE owner of that dict's entire
    lifecycle (creation, wait/timeout, set+pop on completion). A pre-emptive write here that
    nothing then resolves would deadlock any later waiter on this symbol for the full 60s
    timeout on every single real trigger."""
    assert "_inflight_research[sym] =" not in _body
    assert "_inflight_research.setdefault(" not in _body
    assert "_inflight_research.pop(" not in _body
    assert "_inflight_research.update(" not in _body


def test_inflight_check_comes_after_the_existing_cache_age_check():
    """The pre-existing 6h cache-freshness check (a real, still-useful guard for the common
    case — a genuinely fresh report already exists) must stay intact and run FIRST — the
    in-flight check is an ADDITIONAL guard for the concurrent-request race, not a replacement."""
    cache_check_idx = _body.index("age < 21_600")
    inflight_check_idx = _body.index("if sym in _inflight_research:")
    assert cache_check_idx < inflight_check_idx


def test_inflight_research_is_the_same_dict_generate_research_uses():
    """Must reference the actual module-level _inflight_research dict (declared once, near
    the top of the file) — not a locally-scoped or re-created dict that would never see
    entries generate_research() itself registers."""
    assert "_inflight_research: dict[str, asyncio.Event] = {}" in _routes_source
    # only one declaration should exist in the whole file — a second declaration would shadow
    # the shared dict and defeat the fix entirely
    assert _routes_source.count("_inflight_research: dict[str, asyncio.Event] = {}") == 1
