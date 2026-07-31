"""Tests for the 2026-07-31 signal-testing-framework review fixes in gate_harness.py:

- BUG233-BACKTESTHARNESS-EMPTYVALIDATION: _resolvable_window_end() / _HORIZON_RESOLUTION_LAG_DAYS
  — the validation slice was structurally empty at the default 60-day window for 3 of 4 styles
  (SWING/LONG/GROWTH) because window_end was never pulled back to account for each style's own
  SignalOutcome bucket-resolution lag before the 70/30 split.
- BUG233-BACKTESTHARNESS-COINFLIP: _passes_promotion_margin() — a bare
  `best_val.avg_return_pct > baseline_val.avg_return_pct` promotion criterion was simulated to be
  a ~50% false-promotion rate under the null hypothesis at every realistic sample size.

gate_harness.py can't be imported directly in this test environment (conftest.py stubs
sqlalchemy itself as a MagicMock) — both functions under test are pure (no DB/session
dependency), so they're extracted via exec() from the real source text, matching this file's
own established sibling test (test_gate_harness_extended.py) and the repo-wide source-text-
extraction convention documented in CLAUDE.md.
"""
import pathlib
from dataclasses import dataclass, field
from datetime import date, timedelta

_GH_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "backtest" / "gate_harness.py"
_GH_SOURCE = _GH_PATH.read_text()


@dataclass
class _FakeBacktestResult:
    """Minimal stand-in for gate_harness.BacktestResult carrying only the fields
    _passes_promotion_margin() actually reads."""
    avg_return_pct: float | None = None
    skipped_reason: str | None = None
    returns: list[float] = field(default_factory=list)


def _extract_resolution_lag_map():
    start = _GH_SOURCE.index("_HORIZON_RESOLUTION_LAG_DAYS = {")
    end = _GH_SOURCE.index("\n}", start) + len("\n}")
    namespace = {}
    exec(_GH_SOURCE[start:end], namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_HORIZON_RESOLUTION_LAG_DAYS"]


def _extract_resolvable_window_end():
    start = _GH_SOURCE.index("def _resolvable_window_end(")
    end = _GH_SOURCE.index("\n\n\n# BUG233-BACKTESTHARNESS-COINFLIP", start)
    func_source = _GH_SOURCE[start:end]
    namespace = {
        "date": date, "timedelta": timedelta,
        "_HORIZON_RESOLUTION_LAG_DAYS": _extract_resolution_lag_map(),
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_resolvable_window_end"]


def _extract_passes_promotion_margin():
    start = _GH_SOURCE.index("def _passes_promotion_margin(")
    end = _GH_SOURCE.index("\ndef _result_dict(", start)
    func_source = _GH_SOURCE[start:end]
    # Pull the two threshold constants directly out of source too, rather than hardcoding a
    # copy in this test file that could silently drift from the real values.
    const_start = _GH_SOURCE.index("_MIN_PROMOTION_EV_LIFT_PCT = ")
    const_end = _GH_SOURCE.index("\n\n\ndef _passes_promotion_margin(")
    namespace = {"BacktestResult": _FakeBacktestResult}
    exec(_GH_SOURCE[const_start:const_end], namespace)  # noqa: S102
    exec(func_source, namespace)  # noqa: S102
    return namespace["_passes_promotion_margin"], namespace["_MIN_PROMOTION_EV_LIFT_PCT"], namespace["_MIN_PROMOTION_LIFT_SD_RATIO"]


_resolvable_window_end = _extract_resolvable_window_end()
_passes_promotion_margin, _MIN_LIFT_PCT, _MIN_LIFT_SD_RATIO = _extract_passes_promotion_margin()


# ── _resolvable_window_end() / _HORIZON_RESOLUTION_LAG_DAYS ───────────────────────────────────

def test_short_pulls_back_by_7_days():
    assert _resolvable_window_end(date(2026, 7, 31), "SHORT") == date(2026, 7, 24)


def test_swing_pulls_back_by_14_days():
    assert _resolvable_window_end(date(2026, 7, 31), "SWING") == date(2026, 7, 17)


def test_growth_pulls_back_by_14_days_same_as_swing():
    """GROWTH shares SWING's 10d SignalOutcome bucket (_HORIZON_BUCKET), so it must share the
    same 14-day resolution lag — a real regression here would silently reintroduce an empty
    validation slice for GROWTH specifically even if SWING/SHORT/LONG were each fixed."""
    assert _resolvable_window_end(date(2026, 7, 31), "GROWTH") == date(2026, 7, 17)


def test_long_pulls_back_by_20_days():
    assert _resolvable_window_end(date(2026, 7, 31), "LONG") == date(2026, 7, 11)


def test_unknown_style_falls_back_to_14_days_not_zero():
    """A style outside the known 4 must NOT silently fall back to a 0-day lag (which would
    reintroduce the exact empty-validation-slice bug for that style) — the fallback in the real
    _resolvable_window_end() call site (.get(style, 14)) must resolve to a real, non-zero lag."""
    result = _resolvable_window_end(date(2026, 7, 31), "UNKNOWN_STYLE")
    assert result == date(2026, 7, 17)


# ── _passes_promotion_margin() ─────────────────────────────────────────────────────────────────

def _result(avg_return_pct=None, skipped_reason=None, returns=None):
    return _FakeBacktestResult(avg_return_pct=avg_return_pct, skipped_reason=skipped_reason, returns=returns or [])


def test_bare_positive_difference_below_the_minimum_lift_does_not_promote():
    """The exact coin-flip scenario this fix closes: a candidate that's only marginally better
    (well under the 0.5pp floor) must NOT promote, even though the bare `>` comparison the old
    code used would have said yes."""
    best = _result(avg_return_pct=1.01, returns=[1.0] * 20)
    baseline = _result(avg_return_pct=1.0, returns=[1.0] * 20)
    assert _passes_promotion_margin(best, baseline) is False


def test_lift_at_or_above_both_thresholds_promotes():
    # Lift of 5.0pp on returns with essentially zero dispersion (all-identical fractions, so the
    # combined SD is 0) clears both the absolute floor and the SD-ratio requirement (SD=0
    # short-circuits to True per the real function's own explicit "zero dispersion means the
    # lift is real" branch). `returns` are stored as raw fractions (0.06 == 6%), not pct points —
    # avg_return_pct is the already-*100 percent value, matching how BacktestResult itself
    # computes it (`sum(returns) / len(returns) * 100`).
    best = _result(avg_return_pct=6.0, returns=[0.06] * 20)
    baseline = _result(avg_return_pct=1.0, returns=[0.01] * 20)
    assert _passes_promotion_margin(best, baseline) is True


def test_lift_above_absolute_floor_but_small_relative_to_dispersion_does_not_promote():
    """A candidate can clear the flat 0.5pp floor yet still be indistinguishable from noise if
    the underlying returns are highly dispersed — the SD-ratio requirement must catch this even
    when the absolute-lift requirement alone would have passed it."""
    # Realistic fraction-scale returns (matching real production per-trade dispersion, ~10pp SD
    # on 10-day returns per this fix's own investigation) with a large spread, so a 0.6pp lift
    # (just above the absolute floor) is small relative to that dispersion and fails the
    # SD-ratio check. `returns` are raw fractions (0.20 == 20%), matching BacktestResult's own
    # convention.
    wide_returns = [-0.20, -0.10, 0.0, 0.10, 0.20] * 6  # combined SD ~= 15pp once *100
    best = _result(avg_return_pct=0.6, returns=wide_returns)
    baseline = _result(avg_return_pct=0.0, returns=wide_returns)
    assert _passes_promotion_margin(best, baseline) is False


def test_missing_baseline_avg_return_never_promotes():
    best = _result(avg_return_pct=5.0, returns=[5.0] * 20)
    baseline = _result(avg_return_pct=None, skipped_reason="only 3 signals passed the gate (need 15)")
    assert _passes_promotion_margin(best, baseline) is False


def test_missing_candidate_avg_return_never_promotes():
    best = _result(avg_return_pct=None, skipped_reason="only 3 signals passed the gate (need 15)")
    baseline = _result(avg_return_pct=1.0, returns=[1.0] * 20)
    assert _passes_promotion_margin(best, baseline) is False


def test_a_skipped_reason_blocks_promotion_even_if_avg_return_pct_happens_to_be_set():
    best = _result(avg_return_pct=5.0, skipped_reason="only 3 signals passed the gate (need 15)", returns=[5.0] * 20)
    baseline = _result(avg_return_pct=1.0, returns=[1.0] * 20)
    assert _passes_promotion_margin(best, baseline) is False


def test_negative_lift_never_promotes():
    best = _result(avg_return_pct=-1.0, returns=[-1.0] * 20)
    baseline = _result(avg_return_pct=5.0, returns=[5.0] * 20)
    assert _passes_promotion_margin(best, baseline) is False


def test_empty_combined_returns_never_promotes():
    """Guards the len(combined_returns) < 2 branch — an avg_return_pct present with an empty
    returns list (shouldn't happen in practice, but must fail safe, not raise or divide by
    zero)."""
    best = _result(avg_return_pct=5.0, returns=[])
    baseline = _result(avg_return_pct=1.0, returns=[])
    assert _passes_promotion_margin(best, baseline) is False


def test_thresholds_match_the_documented_values():
    """A drift-guard: if these module constants are ever tuned, this test's own expectations
    should be revisited deliberately, not silently invalidated by an unrelated future edit."""
    assert _MIN_LIFT_PCT == 0.5
    assert _MIN_LIFT_SD_RATIO == 0.5
