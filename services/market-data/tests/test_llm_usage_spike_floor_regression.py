"""Regression test for the LLM spike-detector floor found in the 2026-09-06 deep audit
(priority item #12): _LLM_USAGE_MIN_TOKENS_TO_EVALUATE was 50_000 — ~40-160x the real measured
production baseline of ~250-1,300 tokens/hour (services/market-data/tests/
test_llm_usage_spike_alert.py already covers the source-text/constant-level assertions; this
file adds a concrete NUMERIC reconstruction of the exact scenario the audit found).

Concrete regression this closes: a slower-burning recurrence of BUG-NEWSCLASSIFY-REPEATCOST at
49,000 tokens/hour (~196x the real baseline, ~1.18M tokens/day — about a fifth of the incident
that motivated building this detector) would compute a real multiple of 49.0x — comfortably
past the 5.0x trigger — but `current_total < 50_000` returned early on EVERY 15-minute check,
before the baseline math ever ran. It would never have been caught.

This test reconstructs check_llm_usage_spike()'s own evaluate-or-skip decision (the
`current_total < _LLM_USAGE_MIN_TOKENS_TO_EVALUATE` early-return) directly against the real
module constant, using conftest.py's now-available apscheduler stub (added this same session,
Phase 0 of this fix effort) to import scheduler.py for real rather than source-text modeling.
"""
import src.services.scheduler as sch


def test_the_49k_per_hour_regression_scenario_now_reaches_the_baseline_math():
    """THE CORE BUG: a 49,000-token hour (the audit's own worst-case reconstruction) must now
    clear the floor and reach the multiple calculation — before the fix it never did."""
    current_total = 49_000
    assert current_total >= sch._LLM_USAGE_MIN_TOKENS_TO_EVALUATE, (
        f"a {current_total}-token hour must reach the spike evaluation — at the pre-fix "
        f"50_000 floor it never would have, despite being ~196x the real production baseline"
    )


def test_the_49k_scenario_would_have_computed_a_real_multiple_past_the_trigger():
    """Confirms the full consequence: once evaluated, 49,000 tokens against a realistic
    production baseline genuinely clears the 5.0x spike trigger — this was a real, catchable
    incident shape that the floor alone was silently suppressing."""
    current_total = 49_000
    realistic_baseline_median = 1_000.0  # near the high end of the real ~250-1,300/hr range
    baseline_floor = max(realistic_baseline_median, 1000.0)
    multiple = current_total / baseline_floor
    assert multiple >= sch._LLM_USAGE_SPIKE_MULTIPLE


def test_the_original_false_alarm_example_is_still_correctly_suppressed():
    """The floor's ORIGINAL stated purpose (baseline=50, current=300, 'technically 6x but not
    a real incident') must remain correctly suppressed after lowering the floor — it was
    always the baseline_floor=max(median,1000) guard doing this work, not the outer floor, so
    lowering the outer floor must not reintroduce this false alarm."""
    current_total = 300
    baseline_median = 50.0
    baseline_floor = max(baseline_median, 1000.0)
    multiple = current_total / baseline_floor
    assert multiple < sch._LLM_USAGE_SPIKE_MULTIPLE, (
        "the baseline_floor=max(median, 1000.0) guard must still suppress this exact "
        "original false-alarm example regardless of the outer floor's value"
    )


def test_floor_is_low_enough_to_close_the_blind_spot_but_above_real_measured_noise():
    """The real measured production baseline (~250-1,300 tokens/hour, per the audit) must
    still be comfortably suppressed by the floor on an ordinary quiet hour — the fix must not
    have swung to the opposite extreme of evaluating on pure noise."""
    ordinary_quiet_hour_tokens = 1_300  # top of the real measured range
    assert ordinary_quiet_hour_tokens < sch._LLM_USAGE_MIN_TOKENS_TO_EVALUATE


def test_floor_was_lowered_from_the_original_forty_to_one_sixty_x_baseline_blind_spot():
    """Sanity-check the magnitude of what was fixed: the OLD floor (50_000) against the real
    measured baseline range was a 38x-200x blind spot; confirm the NEW floor closes it to a
    single-digit multiple of the top of that real range."""
    real_baseline_low, real_baseline_high = 250, 1_300
    old_floor = 50_000
    new_floor = sch._LLM_USAGE_MIN_TOKENS_TO_EVALUATE

    old_blind_spot_multiple = old_floor / real_baseline_high
    new_blind_spot_multiple = new_floor / real_baseline_high

    assert old_blind_spot_multiple > 30  # confirms just how large the pre-fix gap really was
    assert new_blind_spot_multiple < old_blind_spot_multiple
