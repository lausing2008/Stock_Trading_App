"""Tests for MPE-01's compute_short_squeeze_score() — the composite 0-100 score replacing
short-squeeze.tsx's own binary "Prime Candidate" heuristic.

routes.py can't be imported directly in this test environment (its import chain pulls in
common.config/db, none of which conftest.py stubs for real) — compute_short_squeeze_score()'s
real source is extracted and exec()'d, matching test_max_pain.py's established source-text-
extraction technique for pure functions in this exact file.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_compute_short_squeeze_score():
    start = _ROUTES_SOURCE.index("def compute_short_squeeze_score(")
    end = _ROUTES_SOURCE.index('\n\n@router.get("/{symbol}/options-chain")', start)
    func_source = _ROUTES_SOURCE[start:end]
    namespace = {}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of one pure function's real source
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
    """Callers (the endpoint wiring, the frontend) rely on both 'score' and 'components' being
    present — a regression collapsing this to a bare float would break both silently."""
    result = compute_short_squeeze_score(
        short_percent_of_float=20.0, days_to_cover=3.0, momentum_score=60.0, change_pct=4.0,
    )
    assert set(result.keys()) == {"score", "components"}
    assert isinstance(result["score"], float)
    assert isinstance(result["components"], dict)
