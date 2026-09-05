"""AUD-DARKPOOL-ABSTHRESHOLD + AUD-DARKPOOL-NOPERSIST: two coupled fixes to
check_dark_pool_alerts().

**The selectivity defect.** A flat $1M premium bar is not selective. Measured live 2026-09-05,
the alert fired on 40-49 symbols per day out of a 55-symbol watched universe — ~85-89% of
everything it watches, every single day. A $1M block is routine sub-second activity in AAPL and
genuinely large in a mid-cap, so an absolute dollar bar says almost nothing. Its own resolved
outcomes agreed: 49 rows, +0.005% avg 1d, 40.8% up — noise.

Fixed with a RELATIVE bar applied ALONGSIDE the absolute floor: a print must be
_DARK_POOL_REL_MULTIPLE x the symbol's own median print premium over the trailing
_DARK_POOL_BASELINE_DAYS, AND still clear $1M in absolute terms.

**The persistence gap.** The DarkPoolPrint model and table have existed since T323-DARKPOOL but
NOTHING ever wrote them — 0 rows in production while 184 alerts had fired. That made the alert
permanently un-backtestable, and it is also precisely the history the relative bar needs. Fixed
by persisting every fetched print (not just qualifying ones — the baseline IS the ordinary
prints).

Safe-by-construction against today's empty table: with fewer than
_DARK_POOL_BASELINE_MIN_PRINTS rows the baseline returns None and only the absolute floor
applies, i.e. exactly the pre-fix behavior, until real history accumulates.

scheduler.py can't be imported here (its import chain pulls in apscheduler, not installed
locally), so the pure filtering/threshold logic is verified against the real source text plus a
behavioral model of the exact expression, matching this repo's established technique.
"""
import pathlib

_SCHEDULER_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "scheduler.py"
_SOURCE = _SCHEDULER_PATH.read_text()


def _const(name: str) -> float:
    line = next(l for l in _SOURCE.splitlines() if l.startswith(f"{name} = "))
    return float(line.split("=", 1)[1].split("#")[0].strip())


_REL_MULTIPLE = _const("_DARK_POOL_REL_MULTIPLE")
_MIN_PREMIUM = _const("_DARK_POOL_ALERT_MIN_PREMIUM")
_MIN_PRINTS = _const("_DARK_POOL_BASELINE_MIN_PRINTS")


# ── constants ────────────────────────────────────────────────────────────────

def test_relative_multiple_is_meaningfully_above_one():
    """A multiple at or near 1.0 would admit the median print itself and fix nothing."""
    assert _REL_MULTIPLE >= 3.0


def test_absolute_floor_is_retained_not_replaced():
    """The relative bar must ADD a condition, not swap one loose bar for another — a
    relatively-large print in an illiquid name still has to be a real block in dollars."""
    assert _MIN_PREMIUM == 1_000_000


def test_baseline_requires_a_real_sample():
    assert _MIN_PRINTS >= 10


# ── both bars are applied, conjunctively ─────────────────────────────────────

def _qualifying_block() -> str:
    start = _SOURCE.index("_baseline = _dark_pool_premium_baseline(session, symbol)")
    end = _SOURCE.index("if not qualifying:", start)
    return _SOURCE[start:end]


def test_absolute_and_relative_are_combined_with_and_not_or():
    """`or` would make the alert MORE permissive than before the fix — the exact opposite of
    the intent, and an easy thing to get backwards."""
    block = _qualifying_block()
    assert ">= _DARK_POOL_ALERT_MIN_PREMIUM" in block
    assert ">= _rel_floor" in block
    assert "and (r.premium or 0) >= _rel_floor" in block


def test_relative_floor_is_zero_when_no_baseline_exists():
    """Falls back to pre-fix behavior rather than blocking every alert on a cold table."""
    block = _qualifying_block()
    assert "_rel_floor = (_baseline * _DARK_POOL_REL_MULTIPLE) if _baseline else 0.0" in block


def test_prints_are_persisted_before_filtering():
    """The baseline is built from ORDINARY prints; persisting only qualifying ones would
    destroy the distribution the relative bar measures against."""
    src = _SOURCE
    persist_idx = src.index("_persist_dark_pool_prints(session, stock_id, symbol, rows)")
    filter_idx = src.index("_baseline = _dark_pool_premium_baseline(session, symbol)")
    assert persist_idx < filter_idx


def test_baseline_uses_median_not_mean():
    """Dark-pool premium is heavily right-skewed; a mean would be dragged up by one outlier
    and make the bar EASIER to clear on exactly the symbols that just saw a huge print."""
    start = _SOURCE.index("def _dark_pool_premium_baseline(")
    body = _SOURCE[start:_SOURCE.index("\n\n\ndef ", start)]
    assert "percentile_cont(0.5)" in body
    assert "AVG(premium)" not in body


def test_baseline_returns_none_below_the_minimum_sample():
    start = _SOURCE.index("def _dark_pool_premium_baseline(")
    body = _SOURCE[start:_SOURCE.index("\n\n\ndef ", start)]
    assert "row.n < _DARK_POOL_BASELINE_MIN_PRINTS" in body
    assert "return None" in body


def test_persist_is_idempotent_on_the_real_unique_constraint():
    """This job runs every 60s over a 15-min-cached UW response, so the same print is
    re-fetched many times and must not duplicate."""
    start = _SOURCE.index("def _persist_dark_pool_prints(")
    body = _SOURCE[start:_SOURCE.index("\n\n\ndef ", start)]
    assert "on_conflict_do_nothing" in body
    assert 'constraint="uq_dark_pool_print"' in body


def test_persist_fails_open_and_rolls_back():
    """Persistence must never block a real alert, and a failed INSERT must not poison the
    session for every subsequent symbol in the loop (Postgres aborts the whole transaction
    block on error — the same cascade T243-DQ4 already fixed for the DQ checks)."""
    start = _SOURCE.index("def _persist_dark_pool_prints(")
    body = _SOURCE[start:_SOURCE.index("\n\n\ndef ", start)]
    assert "except Exception" in body
    assert "session.rollback()" in body
    assert "return 0" in body


# ── behavior of the two-bar filter ───────────────────────────────────────────

def _qualifies(premium: float, baseline: float | None) -> bool:
    """The exact expression the scheduler applies."""
    rel_floor = (baseline * _REL_MULTIPLE) if baseline else 0.0
    return premium >= _MIN_PREMIUM and premium >= rel_floor


def test_routine_large_cap_print_no_longer_qualifies():
    """The core defect: a $1.2M print in a name whose median print is $800k is utterly
    ordinary, and used to alert purely for clearing $1M."""
    assert _qualifies(1_200_000, baseline=800_000) is False


def test_genuinely_unusual_print_still_qualifies():
    """20x the symbol's own median, comfortably over the floor."""
    assert _qualifies(16_000_000, baseline=800_000) is True


def test_relatively_huge_but_small_dollar_print_is_rejected():
    """50x a tiny $10k median is only $500k — not an institutional block in dollar terms, so
    the retained absolute floor correctly blocks it."""
    assert _qualifies(500_000, baseline=10_000) is False


def test_no_baseline_falls_back_to_absolute_floor_only():
    """Pre-fix behavior while history accumulates — must not silently block everything."""
    assert _qualifies(1_200_000, baseline=None) is True
    assert _qualifies(900_000, baseline=None) is False


def test_exactly_at_both_bars_qualifies():
    """Boundary: >= on both sides, pinned so a future refactor can't flip to >."""
    assert _qualifies(_MIN_PREMIUM, baseline=_MIN_PREMIUM / _REL_MULTIPLE) is True


def test_filter_is_strictly_tighter_than_before():
    """Whatever the baseline, the new filter can never admit a print the old one rejected."""
    for premium in (500_000, 999_999, 1_000_000, 5_000_000, 50_000_000):
        for baseline in (None, 10_000, 800_000, 5_000_000):
            old = premium >= _MIN_PREMIUM
            new = _qualifies(premium, baseline)
            assert not (new and not old), f"new admitted {premium} that old rejected"
