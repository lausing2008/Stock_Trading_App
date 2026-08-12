"""Tests for AUD265-SQUEEZE-CACHE-MISS-SILENT-SKIP.

`if not cached: continue` treated a stockai:fundamentals:v2:{symbol} cache miss identically to
"this symbol just doesn't qualify" at 5 real sites across this codebase (the tracker's own
2 line-number citations had drifted and only named 2 of the 5 — found the rest via a direct
grep for the pattern before fixing, rather than trusting the stale citation). 4 sites live in
routes.py (earnings_calendar, the stock-events half of events_calendar, analyst_ratings,
short_squeeze); the 5th lives in scheduler.py's check_short_squeeze_alerts() (covered
separately in test_short_squeeze_alert.py, matching that file's own established
source-text-extraction convention for scheduler.py).

_log_fundamentals_cache_misses() is a small, pure, dependency-free helper (module-level in
routes.py, no DB/session access) — tested directly via exec()'d extraction. The 4 route
functions themselves can't be imported in this test environment (conftest.py stubs
sqlalchemy/db wholesale) — their wiring is covered by source-text regression checks instead,
matching test_options_chain.py's/test_short_squeeze_alert.py's established pattern for this
exact import-constraint class.
"""
import pathlib
from unittest.mock import MagicMock, patch

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_log_helper():
    start = _ROUTES_SOURCE.index("def _log_fundamentals_cache_misses(")
    end = _ROUTES_SOURCE.index("\n\n\n# ── Earnings Calendar", start)
    func_source = _ROUTES_SOURCE[start:end]
    fake_log = MagicMock()
    namespace = {"log": fake_log}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of one pure function's real source
    return namespace["_log_fundamentals_cache_misses"], fake_log


def _route_body(func_name: str) -> str:
    start = _ROUTES_SOURCE.index(f"def {func_name}(")
    next_def = _ROUTES_SOURCE.find("\ndef ", start + 1)
    next_router = _ROUTES_SOURCE.find("\n@router", start + 1)
    candidates = [x for x in (next_def, next_router) if x != -1]
    end = min(candidates) if candidates else len(_ROUTES_SOURCE)
    return _ROUTES_SOURCE[start:end]


# ── _log_fundamentals_cache_misses() — pure helper, tested directly ────────────────────────

def test_logs_when_there_are_misses():
    fn, fake_log = _extract_log_helper()
    fn("some_endpoint", 3, 160)
    fake_log.info.assert_called_once()
    args, kwargs = fake_log.info.call_args
    assert args[0] == "fundamentals_cache.misses"
    assert kwargs["endpoint"] == "some_endpoint"
    assert kwargs["misses"] == 3
    assert kwargs["total"] == 160


def test_does_not_log_when_there_are_zero_misses():
    """A fully-warm cache (the currently-verified production state per the tracker's own
    impact note) must not spam a log line every single request — only a real miss is
    worth a log line."""
    fn, fake_log = _extract_log_helper()
    fn("some_endpoint", 0, 160)
    fake_log.info.assert_not_called()


# ── routes.py wiring: all 4 sites actually count misses and call the helper ────────────────

def test_earnings_calendar_counts_misses_and_logs_them():
    body = _route_body("earnings_calendar")
    assert "_misses += 1" in body
    assert '_log_fundamentals_cache_misses("earnings_calendar", _misses, len(stocks))' in body


def test_events_calendar_stock_events_half_counts_misses_and_logs_them():
    """The stock-events half of events_calendar() (earnings + ex-dividends from the SAME
    fundamentals cache) is a separate loop from the macro-events half in the same function —
    confirm the miss counter is scoped to this specific loop, not accidentally shared."""
    start = _ROUTES_SOURCE.index("# ── Stock events: earnings + ex-dividends")
    end = _ROUTES_SOURCE.index("\n@router.get", start)
    body = _ROUTES_SOURCE[start:end]
    assert "_stock_events_misses += 1" in body
    assert '_log_fundamentals_cache_misses("events_calendar_stock_events", _stock_events_misses, len(stocks))' in body


def test_analyst_ratings_counts_misses_and_logs_them():
    body = _route_body("analyst_ratings")
    assert "_misses += 1" in body
    assert '_log_fundamentals_cache_misses("analyst_ratings", _misses, len(stock_map))' in body


def test_short_squeeze_counts_misses_and_logs_them():
    body = _route_body("short_squeeze")
    assert "_misses += 1" in body
    assert '_log_fundamentals_cache_misses("short_squeeze", _misses, len(stock_map))' in body


def test_miss_counter_is_incremented_before_continue_not_after():
    """A counter incremented AFTER `continue` would never execute — confirm the increment is
    the statement immediately preceding continue, not dead code after it, at each site."""
    for func_name, var in [
        ("earnings_calendar", "_misses"),
        ("analyst_ratings", "_misses"),
        ("short_squeeze", "_misses"),
    ]:
        body = _route_body(func_name)
        incr_idx = body.index(f"{var} += 1")
        continue_idx = body.index("continue", incr_idx)
        # No other statement between the increment and its own continue.
        between = body[incr_idx + len(f"{var} += 1"):continue_idx].strip()
        assert between == "", f"{func_name}: unexpected statement between increment and continue: {between!r}"
