"""AUD-HKWEEKEND: HK scheduling gates ran a full cycle every weekend.

`_is_hk_holiday()` checks the HKEX holiday list ONLY — it has no weekend check, unlike its US
counterpart `_is_us_trading_day()` (which tests `weekday() >= 5` first). Three scheduling gates
asked "is the HK market closed?" using `_is_hk_holiday()` alone:

    _refresh_market()        — full ingest + signal refresh + alert checks + paper trading
    _refresh_5m()            — 5-minute intraday price/stop/exit sweep
    _ingest_hk_connect_flows() — HKEX Stock Connect flow ingest

All three therefore ran every Saturday and Sunday against a closed market. The US side was
correctly gated the entire time; only HK was exposed. Confirmed live on Saturday 2026-09-05:
`_is_us_trading_day()` returned False (US correctly skipped) while `_is_hk_holiday()` returned
False, so the HK refresh proceeded.

`_ingest_hk_connect_flows()`'s own docstring already documented the intended behavior — "HKEX does not
publish flow data on weekends or holidays" — while its check only covered holidays.

Fixed by adding `_is_hk_trading_day()`, an exact mirror of `_is_us_trading_day()`, and pointing
the three scheduling gates at it. `_is_hk_holiday()` is deliberately left holiday-only (its name
says so), and the two DQ-check callers that already pair it with their own `weekday() >= 5`
check are correct as-is.

scheduler.py can't be imported directly here (its import chain pulls in apscheduler, not
installed locally) — the calendar functions are pure and dependency-free, so they're extracted
via exec() against the real source, matching this repo's established technique.
"""
import pathlib
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SOURCE = _SCHEDULER_PATH.read_text()


def _load_calendar_fns():
    """Extract the real _HK_HOLIDAYS/_NYSE_HOLIDAYS sets and both trading-day predicates."""
    ns: dict = {"datetime": datetime, "timezone": timezone}

    for const in ("_HK_HOLIDAYS", "_NYSE_HOLIDAYS"):
        start = _SOURCE.index(f"{const}: frozenset")
        end = _SOURCE.index("])", start) + len("])")
        exec(_SOURCE[start:end], ns)  # noqa: S102 — isolated eval of one real constant

    for fn in ("_is_hk_holiday", "_is_hk_trading_day", "_is_us_trading_day"):
        start = _SOURCE.index(f"def {fn}(")
        end = _SOURCE.index("\n\n\n", start)
        exec(_SOURCE[start:end], ns)  # noqa: S102 — isolated eval of one pure function
    return ns


_NS = _load_calendar_fns()
_is_hk_holiday = _NS["_is_hk_holiday"]
_is_hk_trading_day = _NS["_is_hk_trading_day"]
_is_us_trading_day = _NS["_is_us_trading_day"]

_HKT = ZoneInfo("Asia/Hong_Kong")


def _hk(y, m, d, hour=11):
    """A datetime that lands on the given calendar date in Hong Kong local time."""
    return datetime(y, m, d, hour, tzinfo=_HKT).astimezone(timezone.utc)


# ── the regression: weekends ─────────────────────────────────────────────────

def test_saturday_is_not_a_trading_day():
    """2026-09-05 is the Saturday the bug was confirmed live on."""
    assert _is_hk_trading_day(_hk(2026, 9, 5)) is False


def test_sunday_is_not_a_trading_day():
    assert _is_hk_trading_day(_hk(2026, 9, 6)) is False


def test_ordinary_weekday_is_a_trading_day():
    assert _is_hk_trading_day(_hk(2026, 9, 4)) is True   # Friday
    assert _is_hk_trading_day(_hk(2026, 9, 7)) is True   # Monday


def test_holiday_only_helper_still_returns_false_on_a_weekend():
    """Pins the exact gap that caused the bug — _is_hk_holiday() says 'not a holiday' on a
    Saturday, which is true but was being read as 'market is open'."""
    assert _is_hk_holiday(_hk(2026, 9, 5)) is False
    assert _is_hk_trading_day(_hk(2026, 9, 5)) is False


# ── holidays still work ──────────────────────────────────────────────────────

def test_a_real_hk_holiday_on_a_weekday_is_not_a_trading_day():
    holidays = _NS["_HK_HOLIDAYS"]
    weekday_holiday = next(
        (y, m, d) for (y, m, d) in sorted(holidays)
        if datetime(y, m, d, tzinfo=_HKT).weekday() < 5
    )
    y, m, d = weekday_holiday
    assert _is_hk_holiday(_hk(y, m, d)) is True
    assert _is_hk_trading_day(_hk(y, m, d)) is False


def test_trading_day_requires_both_conditions():
    """Weekday AND not-a-holiday — neither alone is sufficient."""
    holidays = _NS["_HK_HOLIDAYS"]
    for (y, m, d) in sorted(holidays):
        dt = _hk(y, m, d)
        assert _is_hk_trading_day(dt) is False, f"{y}-{m}-{d} is a holiday"


# ── parity with the US implementation ────────────────────────────────────────

def test_hk_and_us_agree_that_weekends_are_closed():
    """The whole point of the fix: HK now behaves like US on the axis that was missing.

    Each predicate must be given an instant that is a weekend IN ITS OWN zone. A single
    shared instant does not work: Saturday 11:00 in Hong Kong is 03:00 UTC, which is still
    Friday afternoon in New York — a real US trading day. Comparing the two calendars at one
    instant tests the timezone offset, not the weekend logic.
    """
    _NY = ZoneInfo("America/New_York")
    for day in (5, 6):  # Sat, Sun 2026-09
        hk_instant = datetime(2026, 9, day, 11, tzinfo=_HKT).astimezone(timezone.utc)
        us_instant = datetime(2026, 9, day, 11, tzinfo=_NY).astimezone(timezone.utc)
        assert _is_hk_trading_day(hk_instant) is False
        assert _is_us_trading_day(us_instant) is False


# ── the three scheduling gates actually use the new predicate ────────────────

def test_refresh_market_hk_gate_uses_trading_day():
    idx = _SOURCE.index("def _refresh_market(")
    body = _SOURCE[idx:idx + 3000]
    assert 'if market == "HK" and not _is_hk_trading_day():' in body
    assert 'if market == "HK" and _is_hk_holiday():' not in body


def test_hk_connect_flows_gate_uses_trading_day():
    idx = _SOURCE.index("def _ingest_hk_connect_flows(")
    body = _SOURCE[idx:idx + 3000]
    assert "if not _is_hk_trading_day():" in body


def test_refresh_prices_only_gate_uses_trading_day():
    idx = _SOURCE.index("def _refresh_5m(")
    body = _SOURCE[idx:idx + 2000]
    assert 'if market == "HK" and not _is_hk_trading_day():' in body


def test_dq_check_callers_keep_their_own_explicit_weekend_check():
    """Those two sites pair _is_hk_holiday() with their own `weekday() >= 5` and are already
    correct — they must NOT be rewritten to the new helper, or the intent (and the audit trail
    of why they look different) is lost."""
    assert _SOURCE.count("weekday() >= 5 or _is_hk_holiday()") == 2
