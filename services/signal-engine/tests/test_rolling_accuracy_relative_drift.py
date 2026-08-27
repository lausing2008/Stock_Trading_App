"""Tests for AUD261-DRIFT-ALARM-ALWAYS-RED.

rolling_accuracy()'s drift_warning previously fired on a fixed absolute latest_accuracy < 55%
threshold — but this system's real BUY win rate operates at ~34-41% (per the T232 EV-hurdle-
adjusted "win" definition), so the alarm was permanently, unreachably red next to metrics that
DO clear their own (flattered) thresholds. Now relative to the series' own trailing baseline
(median of every prior point) — a real additional drop of 10 percentage points below that
baseline is what genuinely means "something got worse recently," not a fixed number nobody in
this system's real operating range has ever cleared.

rolling_accuracy() can't be imported directly in this test environment (it needs common.jwt_auth
+ a real DB session for the query-building half) — the drift-computation block itself is pure
(depends only on the already-built `series` list and `latest_accuracy`), so it's extracted via
source-text extraction and exercised directly with synthetic series, matching this repo's
established technique for pure sub-logic inside a DB-heavy function.
"""
import pathlib
import textwrap

_outcomes_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "analytics.py"
_outcomes_source = _outcomes_path.read_text()


def _drift_block() -> str:
    start = _outcomes_source.index("latest_accuracy = series[-1][\"accuracy\"] if series else None")
    end = _outcomes_source.index("\n\n    return {", start)
    # .index() lands right at the start of the first line's own text, past its leading
    # whitespace — re-add 4 spaces so dedent() sees uniform indentation across every line.
    return textwrap.dedent("    " + _outcomes_source[start:end])


def _compute_drift(series: list[dict]):
    """Runs the real drift-computation block against a synthetic `series`."""
    namespace = {"series": series}
    exec(_drift_block(), namespace)  # noqa: S102 — isolated eval of real source
    return namespace["drift_warning"], namespace["baseline_accuracy"]


def _pts(*accs: float) -> list[dict]:
    return [{"date": f"2026-01-{i+1:02d}", "accuracy": a, "signal_count": 10} for i, a in enumerate(accs)]


class TestBaselineComputation:
    def test_baseline_is_the_median_of_all_prior_points_excluding_the_latest(self):
        # prior = [30, 35, 40] -> median 35; latest (50) is excluded from the baseline itself.
        _, baseline = _compute_drift(_pts(30.0, 40.0, 35.0, 50.0))
        assert baseline == 35.0

    def test_baseline_averages_the_two_middle_values_for_an_even_count_of_prior_points(self):
        # prior = [30, 34, 36, 40] -> median (34+36)/2 = 35.0
        _, baseline = _compute_drift(_pts(30.0, 40.0, 34.0, 36.0, 20.0))
        assert baseline == 35.0

    def test_no_baseline_when_fewer_than_3_points_total(self):
        _, baseline = _compute_drift(_pts(40.0, 35.0))
        assert baseline is None


class TestDriftWarningIsRelativeNotAbsolute:
    def test_no_warning_when_latest_matches_a_low_baseline(self):
        """The exact regression this fix targets: a system whose baseline operates at ~37%
        must NOT permanently alarm just because 37% < the old fixed 55% floor."""
        series = _pts(36.0, 38.0, 37.0, 39.0, 37.5)  # baseline ~37.5, latest 37.5
        drift_warning, baseline = _compute_drift(series)
        assert baseline == 37.5
        assert drift_warning is False

    def test_warns_on_a_real_additional_drop_below_the_low_baseline(self):
        """The same low-baseline system SHOULD still alarm on a genuine further degradation —
        this isn't a blanket suppression, just a relative one."""
        series = _pts(36.0, 38.0, 37.0, 39.0, 20.0)  # baseline ~37.5, latest drops to 20
        drift_warning, baseline = _compute_drift(series)
        assert baseline == 37.5
        assert drift_warning is True

    def test_does_not_warn_on_a_drop_smaller_than_the_relative_threshold(self):
        series = _pts(50.0, 50.0, 50.0, 42.0)  # baseline 50, latest drops 8pp (< 10pp threshold)
        drift_warning, _ = _compute_drift(series)
        assert drift_warning is False

    def test_warns_at_exactly_the_relative_threshold_boundary(self):
        series = _pts(50.0, 50.0, 50.0, 39.9)  # baseline 50, latest drops just over 10pp
        drift_warning, _ = _compute_drift(series)
        assert drift_warning is True

    def test_falls_back_to_the_absolute_floor_when_no_real_baseline_exists_yet(self):
        """Fewer than 3 total points means no real trailing baseline — must still warn on the
        original absolute 55% floor rather than silently never warning during early history."""
        drift_warning_low, _ = _compute_drift(_pts(40.0, 45.0))
        assert drift_warning_low is True
        drift_warning_high, _ = _compute_drift(_pts(60.0, 65.0))
        assert drift_warning_high is False

    def test_no_warning_when_series_is_empty(self):
        drift_warning, baseline = _compute_drift([])
        assert drift_warning is False
        assert baseline is None
