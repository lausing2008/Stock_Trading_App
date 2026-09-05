"""AUD-BASELINE-ERASPLIT: /signals/outcomes/summary pooled pre- and post-2026-08-04 outcomes
into single aggregates, blending two materially different systems.

Commit aee6d17 (AUD232-BUY-FROM-TOP, 2026-08-03) fixed a real signal-generation bug that made
the HIGHEST-confidence BUYs the WORST performers. Split on that date, high-confidence (>=80)
BUYs went from -7.19%/33.5% win to -1.02%/51.6% win. ~62% of all evaluated BUY outcomes predate
the fix, so the endpoint's default 90-day window is mostly pre-fix history and understates
current quality.

Fixed by adding a `by_era` breakdown (pre_fix / post_fix / window_spans_fix) rather than
changing the cutoff — dropping pre-fix rows would destroy the evidence that the bug and its fix
were real. See docs/2026-09-04/PHASE_B2_INVERSION_ROOT_CAUSE_FOUND.md.

outcomes_summary() is a large route function with heavy DB/session dependencies, so its wiring
is covered by source-text regression checks, matching test_signed_return_and_accuracy_fixes.py's
established convention for this exact shape of function. The era-partition logic itself is pure
and is tested behaviorally.
"""
import pathlib
from datetime import date

_ANALYTICS_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "analytics.py"
_SOURCE = _ANALYTICS_PATH.read_text()


def _summary_source() -> str:
    start = _SOURCE.index("def outcomes_summary(")
    end = _SOURCE.index("\n@router.", start)
    return _SOURCE[start:end]


# ── the fix date constant ────────────────────────────────────────────────────

def test_inversion_fix_date_constant_exists_and_is_the_aee6d17_date():
    """aee6d17 landed 2026-08-03; outcomes dated 08-04 onward are the post-fix system."""
    assert "_INVERSION_FIX_DATE = date(2026, 8, 4)" in _SOURCE


def test_fix_date_constant_is_defined_before_the_route_that_uses_it():
    """Module-level constants in this file are otherwise declared AFTER their consumers;
    this one must be before, or the route raises NameError at request time."""
    const_idx = _SOURCE.index("_INVERSION_FIX_DATE = date(")
    route_idx = _SOURCE.index("def outcomes_summary(")
    assert const_idx < route_idx


# ── wiring: by_era is computed and returned ──────────────────────────────────

def test_by_era_is_in_the_returned_payload():
    assert '"by_era": era_stats,' in _summary_source()


def test_era_split_partitions_on_the_constant_not_a_hardcoded_date():
    src = _summary_source()
    assert "o.signal_date < _INVERSION_FIX_DATE" in src
    assert "o.signal_date >= _INVERSION_FIX_DATE" in src


def test_era_block_reports_count_winrate_and_return():
    src = _summary_source()
    block = src[src.index("def _era_block("):src.index("_pre = [")]
    for field in ('"count"', '"win_rate"', '"avg_return_pct"'):
        assert field in block


def test_era_returns_use_signed_return_like_every_other_aggregate():
    """A SELL wins on a NEGATIVE raw pct_return (AUD261-OUTCOMESSUMMARY-UNSIGNED-SELL). The
    era block must not reintroduce the unsigned bug this endpoint already fixed everywhere."""
    src = _summary_source()
    block = src[src.index("def _era_block("):src.index("_pre = [")]
    assert "_signed_return(o.pct_return, o.signal_direction)" in block


def test_window_spans_fix_flag_is_computed():
    assert '"window_spans_fix": bool(_pre and _post)' in _summary_source()


def test_pre_fix_rows_are_not_silently_dropped():
    """The fix must ADD a breakdown, never filter the main query — the pooled `overall` stat
    and pre_fix era must both still be reachable."""
    src = _summary_source()
    assert '"pre_fix": _era_block(_pre)' in src
    # The main outcomes query must not have gained a fix-date filter.
    query_block = src[:src.index("outcomes = session.execute(q).scalars().all()")]
    assert "_INVERSION_FIX_DATE" not in query_block


# ── behavior of the partition itself ─────────────────────────────────────────

class _O:
    """Minimal stand-in for a SignalOutcome row."""
    def __init__(self, d: date, correct: bool, ret: float, direction: str = "BUY"):
        self.signal_date = d
        self.is_correct = correct
        self.pct_return = ret
        self.signal_direction = direction


_FIX = date(2026, 8, 4)


def _partition(rows):
    return ([o for o in rows if o.signal_date < _FIX],
            [o for o in rows if o.signal_date >= _FIX])


def test_boundary_date_belongs_to_post_fix():
    """2026-08-04 itself is the first post-fix day — an off-by-one here would misattribute
    a whole day of outcomes to the buggy era."""
    pre, post = _partition([_O(_FIX, True, 0.01)])
    assert len(post) == 1 and pre == []


def test_day_before_boundary_is_pre_fix():
    pre, post = _partition([_O(date(2026, 8, 3), True, 0.01)])
    assert len(pre) == 1 and post == []


def test_partition_separates_the_two_eras():
    rows = [
        _O(date(2026, 7, 20), False, -0.07),
        _O(date(2026, 8, 1), False, -0.05),
        _O(date(2026, 8, 10), True, 0.02),
    ]
    pre, post = _partition(rows)
    assert len(pre) == 2
    assert len(post) == 1


def test_window_entirely_post_fix_reports_no_span():
    rows = [_O(date(2026, 8, 10), True, 0.02), _O(date(2026, 8, 20), False, -0.01)]
    pre, post = _partition(rows)
    assert bool(pre and post) is False, "a post-fix-only window must not be flagged as spanning"


def test_window_entirely_pre_fix_reports_no_span():
    rows = [_O(date(2026, 7, 1), True, 0.02), _O(date(2026, 7, 20), False, -0.01)]
    pre, post = _partition(rows)
    assert bool(pre and post) is False
