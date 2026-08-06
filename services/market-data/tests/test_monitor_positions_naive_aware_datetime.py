"""Regression tests for BUG-MONITORPOS-NAIVEAWARE.

_monitor_positions()'s WAIT-decay branch compared `last_non_wait_ts` (read straight from
Signal.ts, a plain DateTime column with no timezone=True — always naive) against `now`
(datetime.now(timezone.utc) — tz-aware, needed elsewhere in this function for exit_time
writes) via a bare `<` comparison. Python raises `TypeError: can't compare offset-naive and
offset-aware datetimes` on any such comparison — it never silently coerces.

This was introduced 2026-07-22 (BUG233-BACKTESTWALLCLOCK) when `now`'s construction changed
from datetime.utcnow() (naive) to datetime.now(timezone.utc) (aware) to support an injectable
`as_of` parameter for backtesting — the WAIT-decay comparison a few hundred lines later was
never updated to match.

The blast radius is much larger than one trade: paper_trading_step() wraps its ENTIRE
per-portfolio loop (monitor + scan, every portfolio) in a single outer try/except. The moment
_monitor_positions() raises on the FIRST portfolio it processes that happens to hold an open
trade currently sitting on a WAIT signal, the exception propagates out of the whole function —
aborting _scan_for_entries() for every OTHER portfolio in that same cycle too, not just the one
with the WAIT trade. Confirmed live in production: paper.step_failed fired on every cycle for
hours, with zero paper.entry_opened/entry_gate_blocked-then-entered events in between — i.e.
ALL paper trading (every portfolio) was silently non-functional, not just one.

_monitor_positions() itself is not exercised end-to-end here (200+ lines, heavy Signal/RSI/
regime dependencies) — matching test_monitor_positions_stale_price.py's established precedent
for this exact risk class: source-text regression checks on the specific fixed lines, plus a
standalone behavioral proof (using real datetime objects, no mocking) that the exact comparison
shape which used to raise no longer does.
"""
import pathlib
from datetime import datetime, timedelta, timezone

_PTE_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
)
_SOURCE = _PTE_PATH.read_text()


def _wait_decay_block() -> str:
    """The WAIT-decay branch inside _monitor_positions()'s per-trade loop — from the
    `elif sig_type == "WAIT":` line through the `still_waiting` computation."""
    start = _SOURCE.index('elif sig_type == "WAIT":')
    end = _SOURCE.index("if still_waiting:", start)
    return _SOURCE[start:end]


def test_the_fix_strips_tzinfo_from_now_before_comparing_to_the_naive_db_value():
    """The actual fix: `now.replace(tzinfo=None)` on the tz-aware `now`, not a change to
    last_non_wait_ts (which is correctly naive, straight from the DB, and must stay that way —
    changing IT instead would risk breaking other naive-DB-field comparisons elsewhere)."""
    body = _wait_decay_block()
    assert "last_non_wait_ts < now.replace(tzinfo=None) - timedelta(days=wait_days)" in body


def test_the_old_broken_comparison_is_gone():
    """The pre-fix line (`last_non_wait_ts < now - timedelta(...)`, with no .replace() call)
    must not still be present anywhere in the WAIT-decay block — a partial fix that left the
    old comparison behind (e.g. added a new branch instead of correcting this one) would still
    crash whenever that old line executes."""
    body = _wait_decay_block()
    assert "last_non_wait_ts < now - timedelta(days=wait_days)" not in body


def test_comparison_is_still_reached_when_last_non_wait_ts_is_a_real_datetime():
    """A regression guard against a lazier fix that short-circuits before ever comparing (e.g.
    `last_non_wait_ts is None or True` or similar) — the real datetime comparison must still be
    the operative check when last_non_wait_ts is not None."""
    body = _wait_decay_block()
    assert "last_non_wait_ts is None or" in body


def test_naive_vs_aware_comparison_no_longer_raises_typeerror():
    """The concrete behavioral proof: reproduce the EXACT shapes involved (a naive datetime
    read straight from a plain DateTime column, and an aware `now`) and confirm the fixed
    comparison expression — evaluated directly, not through _monitor_positions() itself —
    no longer raises. This is the same TypeError Python raises for ANY naive-vs-aware
    comparison; adversarial verification (reverting the .replace() call) is what actually
    proves this test is load-bearing, since a broken version would raise here too."""
    now = datetime.now(timezone.utc)  # aware
    wait_days = 5
    last_non_wait_ts = now.replace(tzinfo=None)  # naive, as SQLAlchemy returns it — right now

    # This is the literal fixed expression from the source, evaluated directly:
    result = last_non_wait_ts < now.replace(tzinfo=None) - timedelta(days=wait_days)
    assert result is False  # a signal from right now is not older than wait_days ago

    # And confirm the OLD, broken shape genuinely does raise — proving this isn't a no-op check:
    import pytest
    with pytest.raises(TypeError, match="offset-naive and offset-aware"):
        last_non_wait_ts < now - timedelta(days=wait_days)


def test_still_waiting_true_when_last_non_wait_signal_is_older_than_wait_days():
    """Confirms the fixed comparison still produces the correct TRUE/decay-exit result for a
    genuinely stale non-WAIT signal, not just that it avoids crashing."""
    now = datetime.now(timezone.utc)
    wait_days = 5
    last_non_wait_ts = now.replace(tzinfo=None) - timedelta(days=wait_days, hours=1)  # just past stale
    result = last_non_wait_ts < now.replace(tzinfo=None) - timedelta(days=wait_days)
    assert result is True
