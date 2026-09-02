"""Tests for MPE-01's compute_short_squeeze_score() — the composite 0-100 score replacing
short-squeeze.tsx's own binary "Prime Candidate" heuristic. Also covers MPE-07's short-
interest UTILIZATION component and the LOW/MEDIUM/HIGH covering-pressure classification added
once Unusual Whales became available.

routes.py can't be imported directly in this test environment (its import chain pulls in
common.config/db, none of which conftest.py stubs for real) — compute_short_squeeze_score()'s
real source is extracted and exec()'d, matching test_max_pain.py's established source-text-
extraction technique for pure functions in this exact file. _short_covering_pressure() is a
real dependency compute_short_squeeze_score() calls, so both are extracted together in one
exec() namespace — a namespace missing the helper would raise a real NameError the moment the
function under test tries to call it, not silently produce a wrong result.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_compute_short_squeeze_score():
    start = _ROUTES_SOURCE.index("_SQUEEZE_PRESSURE_LOW_MAX = ")
    end = _ROUTES_SOURCE.index('\n\n@router.get("/{symbol}/options-chain")', start)
    func_source = _ROUTES_SOURCE[start:end]
    namespace = {}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of these pure functions' real source
    return namespace["compute_short_squeeze_score"]


compute_short_squeeze_score = _extract_compute_short_squeeze_score()


def test_returns_none_when_short_percent_of_float_is_missing():
    """The one load-bearing input — never a fabricated score with zero real short-interest
    data behind it."""
    result = compute_short_squeeze_score(
        short_percent_of_float=None, days_to_cover=5.0, momentum_score=80.0, change_pct=5.0,
    )
    assert result is None


def test_a_real_candidate_that_clears_every_bar_scores_high():
    """15%+ short float (the alert's own floor), a real p10-beating days-to-cover, strong
    momentum, and a real move — should land well into the upper score range."""
    result = compute_short_squeeze_score(
        short_percent_of_float=30.0, days_to_cover=1.0, momentum_score=90.0, change_pct=8.0,
    )
    assert result is not None
    assert result["score"] >= 80.0


def test_a_weak_candidate_barely_clearing_the_short_float_floor_scores_low():
    """A stock just above 5% short float with nothing else going for it (no days-to-cover
    data, no momentum, no move) should score near the bottom of the range."""
    result = compute_short_squeeze_score(
        short_percent_of_float=6.0, days_to_cover=None, momentum_score=None, change_pct=None,
    )
    assert result is not None
    assert result["score"] < 10.0


def test_short_float_component_hand_computed_at_the_midpoint():
    """spf_pts = min(40, max(0, (spf-5)/(30-5)*40)) — at spf=17.5 (the exact midpoint of the
    5-30 scoring range), spf_pts should be exactly 20.0 (half of 40)."""
    result = compute_short_squeeze_score(
        short_percent_of_float=17.5, days_to_cover=None, momentum_score=None, change_pct=None,
    )
    assert result["components"]["short_float_pts"] == 20.0


def test_short_float_component_caps_at_40_points_beyond_30_pct():
    result_at_30 = compute_short_squeeze_score(
        short_percent_of_float=30.0, days_to_cover=None, momentum_score=None, change_pct=None,
    )
    result_at_60 = compute_short_squeeze_score(
        short_percent_of_float=60.0, days_to_cover=None, momentum_score=None, change_pct=None,
    )
    assert result_at_30["components"]["short_float_pts"] == 40.0
    assert result_at_60["components"]["short_float_pts"] == 40.0


def test_short_float_component_floors_at_0_below_5_pct():
    result = compute_short_squeeze_score(
        short_percent_of_float=2.0, days_to_cover=None, momentum_score=None, change_pct=None,
    )
    assert result["components"]["short_float_pts"] == 0.0


def test_days_to_cover_is_inverse_lower_is_more_acute():
    """A lower days-to-cover (shorts can't exit quietly) should score HIGHER, not lower — the
    exact opposite direction of a naive 'bigger number = more points' assumption."""
    acute = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=1.13, momentum_score=None, change_pct=None,
    )
    mild = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=4.65, momentum_score=None, change_pct=None,
    )
    assert acute["components"]["days_to_cover_pts"] > mild["components"]["days_to_cover_pts"]


def test_days_to_cover_at_the_real_p10_scores_near_the_full_30_points():
    result = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=1.13, momentum_score=None, change_pct=None,
    )
    assert result["components"]["days_to_cover_pts"] == 30.0


def test_days_to_cover_at_or_beyond_the_real_p50_scores_zero():
    result_at_p50 = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=4.65, momentum_score=None, change_pct=None,
    )
    result_beyond = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=10.0, momentum_score=None, change_pct=None,
    )
    assert result_at_p50["components"]["days_to_cover_pts"] == 0.0
    assert result_beyond["components"]["days_to_cover_pts"] == 0.0


def test_days_to_cover_missing_contributes_zero_not_a_crash():
    result = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
    )
    assert result["components"]["days_to_cover_pts"] == 0.0


def test_momentum_score_is_a_straight_1_to_5_scale():
    """momentum_score is already 0-100 (K-Score's own momentum sub-score) — mom_pts should be
    exactly 1/5th of it, capped at 20."""
    result = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=50.0, change_pct=None,
    )
    assert result["components"]["momentum_pts"] == 10.0


def test_momentum_score_missing_contributes_zero_not_a_crash():
    result = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
    )
    assert result["components"]["momentum_pts"] == 0.0


def test_change_pct_caps_at_10_points_for_a_10_pct_move():
    result_at_10 = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=10.0,
    )
    result_beyond = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=25.0,
    )
    assert result_at_10["components"]["change_pct_pts"] == 10.0
    assert result_beyond["components"]["change_pct_pts"] == 10.0


def test_a_negative_change_pct_never_produces_negative_points():
    """A down day must never SUBTRACT from the score — the max(0, ...) floor must catch this."""
    result = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=-8.0,
    )
    assert result["components"]["change_pct_pts"] == 0.0
    assert result["score"] >= 0.0


def test_uw_fee_rate_enrichment_adds_up_to_5_points_when_present():
    without_uw = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
    )
    with_uw = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
        fee_rate=20.0,
    )
    assert "uw_borrow_fee_pts" not in without_uw["components"]
    assert with_uw["components"]["uw_borrow_fee_pts"] == 5.0
    assert with_uw["score"] == without_uw["score"] + 5.0


def test_uw_enrichment_never_pushes_the_score_above_100():
    """A degenerate case: every component maxed AND a maxed UW fee-rate bonus must still clamp
    at 100, never overshoot."""
    result = compute_short_squeeze_score(
        short_percent_of_float=100.0, days_to_cover=0.0, momentum_score=100.0, change_pct=100.0,
        fee_rate=100.0,
    )
    assert result["score"] == 100.0


def test_the_score_is_a_real_dict_shape_not_a_bare_number():
    """Callers (the endpoint wiring, the frontend) rely on 'score'/'components'/
    'covering_pressure' all being present — a regression collapsing this to a bare float
    would break all three silently."""
    result = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=3.0, momentum_score=60.0, change_pct=4.0,
    )
    assert set(result.keys()) == {"score", "components", "covering_pressure"}
    assert isinstance(result["score"], float)
    assert isinstance(result["components"], dict)
    assert isinstance(result["covering_pressure"], dict)


# ── MPE-07: real short-interest UTILIZATION component ──────────────────────────────────────

def test_utilization_absent_when_either_uw_input_is_missing():
    """Both short_interest AND short_shares_available must be present — a lone value from
    only one source can't compute a real ratio, and the two must never be mixed with a
    DIFFERENT provider's own value for the other half."""
    result_neither = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
    )
    result_only_shares_avail = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
        short_shares_available=1_000_000.0,
    )
    result_only_short_interest = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
        short_interest=800_000.0,
    )
    assert "uw_utilization_pts" not in result_neither["components"]
    assert "uw_utilization_pts" not in result_only_shares_avail["components"]
    assert "uw_utilization_pts" not in result_only_short_interest["components"]


def test_utilization_hand_computed_at_a_real_ratio():
    """800,000 shares short / 1,000,000 shares available = 80% utilization — squarely inside
    the 50-90% scoring range: util_pts = (80-50)/(90-50)*5 = 3.75, rounded to 1dp = 3.8
    (matching the function's own round(util_pts, 1) — the hand-computed raw value, not the
    displayed one, is 3.75)."""
    result = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
        short_interest=800_000.0, short_shares_available=1_000_000.0,
    )
    assert result["components"]["uw_utilization_pct"] == 80.0
    assert result["components"]["uw_utilization_pts"] == 3.8


def test_utilization_floors_at_zero_points_below_50_pct():
    result = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
        short_interest=300_000.0, short_shares_available=1_000_000.0,
    )
    assert result["components"]["uw_utilization_pct"] == 30.0
    assert result["components"]["uw_utilization_pts"] == 0.0


def test_utilization_caps_at_5_points_at_or_beyond_90_pct():
    result_at_90 = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
        short_interest=900_000.0, short_shares_available=1_000_000.0,
    )
    result_beyond = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
        short_interest=1_500_000.0, short_shares_available=1_000_000.0,
    )
    assert result_at_90["components"]["uw_utilization_pts"] == 5.0
    assert result_beyond["components"]["uw_utilization_pts"] == 5.0
    # a real ratio can legitimately exceed 100% (more shares reported short than currently
    # available to borrow, e.g. between settlement dates) — the reported PCT itself must still
    # clamp at 100, never a nonsensical 150%.
    assert result_beyond["components"]["uw_utilization_pct"] == 100.0


def test_utilization_never_divides_by_a_zero_shares_available():
    result = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
        short_interest=500_000.0, short_shares_available=0.0,
    )
    assert "uw_utilization_pts" not in result["components"]


def test_utilization_and_borrow_fee_stack_additively():
    """Both UW enrichment components can fire together — 5 (fee) + 5 (utilization) = 10 extra
    points on top of the free-tier score, not one silently overwriting the other."""
    without_uw = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
    )
    with_both = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
        fee_rate=20.0, short_interest=900_000.0, short_shares_available=1_000_000.0,
    )
    assert with_both["score"] == without_uw["score"] + 5.0 + 5.0


def test_utilization_enrichment_never_pushes_the_score_above_100():
    result = compute_short_squeeze_score(
        short_percent_of_float=100.0, days_to_cover=0.0, momentum_score=100.0, change_pct=100.0,
        fee_rate=100.0, short_interest=2_000_000.0, short_shares_available=1_000_000.0,
    )
    assert result["score"] == 100.0


# ── MPE-07: LOW/MEDIUM/HIGH short-covering-pressure classification ─────────────────────────

def test_covering_pressure_is_low_below_40():
    result = compute_short_squeeze_score(
        short_percent_of_float=6.0, days_to_cover=None, momentum_score=None, change_pct=None,
    )
    assert result["score"] < 40.0
    assert result["covering_pressure"]["pressure"] == "LOW"


def test_covering_pressure_is_medium_between_40_and_70():
    result = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=2.5, momentum_score=40.0, change_pct=3.0,
    )
    assert 40.0 <= result["score"] < 70.0
    assert result["covering_pressure"]["pressure"] == "MEDIUM"


def test_covering_pressure_is_high_at_or_above_70():
    result = compute_short_squeeze_score(
        short_percent_of_float=30.0, days_to_cover=1.0, momentum_score=90.0, change_pct=8.0,
    )
    assert result["score"] >= 70.0
    assert result["covering_pressure"]["pressure"] == "HIGH"


def test_covering_pressure_boundaries_are_inclusive_on_the_lower_edge():
    """Exactly 40.0 must read MEDIUM (not LOW); exactly 70.0 must read HIGH (not MEDIUM) — the
    real >= comparison, not a > that would misclassify the exact boundary value. Both inputs
    are hand-derived to sum to the exact boundary: spf=17.5 -> spf_pts=20.0, momentum=100.0
    (capped) -> mom_pts=20.0, summing to exactly 40.0; adding days_to_cover=1.13 (the real p10,
    already scoring the full 30 points elsewhere in this file) brings the same combination to
    exactly 70.0."""
    at_40 = compute_short_squeeze_score(
        short_percent_of_float=17.5, days_to_cover=None, momentum_score=100.0, change_pct=None,
    )
    assert at_40["score"] == 40.0
    assert at_40["covering_pressure"]["pressure"] == "MEDIUM"

    at_70 = compute_short_squeeze_score(
        short_percent_of_float=17.5, days_to_cover=1.13, momentum_score=100.0, change_pct=None,
    )
    assert at_70["score"] == 70.0
    assert at_70["covering_pressure"]["pressure"] == "HIGH"


def test_covering_pressure_confidence_is_higher_with_real_uw_enrichment():
    """The doc's own instruction: confidence reflects how many real inputs informed the score,
    never a fabricated statistical estimate (this app's alert family has nowhere near enough
    resolved outcomes to fit a real model)."""
    without_uw = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
    )
    with_uw = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=None, momentum_score=None, change_pct=None,
        fee_rate=10.0,
    )
    assert with_uw["covering_pressure"]["confidence"] > without_uw["covering_pressure"]["confidence"]


def test_covering_pressure_confidence_never_exceeds_95():
    result = compute_short_squeeze_score(
        short_percent_of_float=100.0, days_to_cover=0.0, momentum_score=100.0, change_pct=100.0,
        fee_rate=100.0, short_interest=2_000_000.0, short_shares_available=1_000_000.0,
    )
    assert result["covering_pressure"]["confidence"] <= 95.0


def test_covering_pressure_confidence_never_negative_on_a_zero_score():
    result = compute_short_squeeze_score(
        short_percent_of_float=5.0, days_to_cover=None, momentum_score=None, change_pct=None,
    )
    assert result["score"] == 0.0
    assert result["covering_pressure"]["confidence"] >= 0.0
