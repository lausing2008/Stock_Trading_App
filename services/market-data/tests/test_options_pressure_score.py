"""Tests for MPE-02's compute_options_pressure_score() — a composite 0-100 conviction/
intensity score built from get_options_flow()'s already-computed cp_ratio/sentiment/
whale_count/volume, with optional Unusual Whales GEX-proximity enrichment (MPE-07).

routes.py can't be imported directly in this test environment — source-text extraction,
matching test_max_pain.py's/test_short_squeeze_score.py's established technique.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_compute_options_pressure_score():
    start = _ROUTES_SOURCE.index("def compute_options_pressure_score(")
    end = _ROUTES_SOURCE.index("\n\n_OPTIONS_CHAIN_TTL", start)
    func_source = _ROUTES_SOURCE[start:end]
    namespace = {}
    exec(func_source, namespace)  # noqa: S102
    return namespace["compute_options_pressure_score"]


compute_options_pressure_score = _extract_compute_options_pressure_score()


def test_returns_none_when_cp_ratio_is_missing():
    result = compute_options_pressure_score(
        cp_ratio=None, sentiment=None, whale_count=0, total_call_vol=0, total_put_vol=0,
    )
    assert result is None


def test_neutral_cp_ratio_of_1_scores_zero_on_that_component():
    """cp_ratio=1.0 (perfectly balanced call/put volume) is the score's own neutral point —
    zero conviction/intensity from this component, regardless of direction."""
    result = compute_options_pressure_score(
        cp_ratio=1.0, sentiment="neutral", whale_count=0, total_call_vol=0, total_put_vol=0,
    )
    assert result["components"]["cp_ratio_pts"] == 0.0


def test_a_low_cp_ratio_below_1_still_scores_high_conviction_not_zero():
    """This is a CONVICTION score, not a directional one — cp_ratio=0.2 (extreme put skew,
    below the 1.0 neutral point) must still score a real, high cp_ratio_pts value, not collapse
    toward 0 the way a naive "bigger ratio = more points" (with no distance-from-1.0 framing)
    formula would. AUD-OPTIONS6-CPRATIOASYMMETRY: cp_ratio=0.2 is this score's own documented
    EXTREME on the bearish side (matching cp_ratio=5.0's documented extreme on the bullish
    side) and must score the FULL 40, not a partial value — the two sides are scaled separately
    ((1.0-0.2)=0.8 below neutral, (5.0-1.0)=4.0 above) precisely so both extremes reach the same
    max, since 0.2 and 5.0 are a 5x fold-change from 1.0 in either direction despite NOT being
    equidistant in absolute linear terms."""
    low = compute_options_pressure_score(
        cp_ratio=0.2, sentiment="bearish", whale_count=0, total_call_vol=0, total_put_vol=0,
    )
    assert low["components"]["cp_ratio_pts"] == 40.0


def test_cp_ratio_below_1_scaling_uses_the_08_denominator_not_the_above_1_side_denominator():
    """A real, hand-computed midpoint on the below-1.0 side, distinct from the extreme (0.2)
    and neutral (1.0) cases above — confirms the below-1.0 side genuinely uses its OWN
    (1.0-0.2)=0.8 denominator, not accidentally sharing the above-1.0 side's (5.0-1.0)=4.0."""
    result = compute_options_pressure_score(
        cp_ratio=0.6, sentiment="bearish", whale_count=0, total_call_vol=0, total_put_vol=0,
    )
    # (1.0 - 0.6) / (1.0 - 0.2) * 40 = 0.4 / 0.8 * 40 = 20.0, hand-verified.
    assert result["components"]["cp_ratio_pts"] == 20.0


def test_the_two_documented_extremes_now_score_identically():
    """The exact bug this fix closes: cp_ratio=0.2 (bearish extreme) and cp_ratio=5.0 (bullish
    extreme) are BOTH explicitly documented as the score's own two "full 40 points" extremes —
    before the fix, 0.2 only reached 8.0 while 5.0 correctly reached 40.0."""
    bearish_extreme = compute_options_pressure_score(
        cp_ratio=0.2, sentiment="bearish", whale_count=0, total_call_vol=0, total_put_vol=0,
    )
    bullish_extreme = compute_options_pressure_score(
        cp_ratio=5.0, sentiment="bullish", whale_count=0, total_call_vol=0, total_put_vol=0,
    )
    assert bearish_extreme["components"]["cp_ratio_pts"] == 40.0
    assert bullish_extreme["components"]["cp_ratio_pts"] == 40.0


def test_cp_ratio_component_hand_computed_at_a_midpoint():
    """cpr_pts = min(40, max(0, |cp_ratio-1|/(5-1)*40)) — at cp_ratio=2.0, |2-1|/4*40 = 10.0."""
    result = compute_options_pressure_score(
        cp_ratio=2.0, sentiment="bullish", whale_count=0, total_call_vol=0, total_put_vol=0,
    )
    assert result["components"]["cp_ratio_pts"] == 10.0


def test_whale_points_are_10_per_whale_capped_at_3():
    one_whale = compute_options_pressure_score(
        cp_ratio=1.0, sentiment="neutral", whale_count=1, total_call_vol=0, total_put_vol=0,
    )
    three_whales = compute_options_pressure_score(
        cp_ratio=1.0, sentiment="neutral", whale_count=3, total_call_vol=0, total_put_vol=0,
    )
    five_whales = compute_options_pressure_score(
        cp_ratio=1.0, sentiment="neutral", whale_count=5, total_call_vol=0, total_put_vol=0,
    )
    assert one_whale["components"]["whale_pts"] == 10.0
    assert three_whales["components"]["whale_pts"] == 30.0
    assert five_whales["components"]["whale_pts"] == 30.0  # capped, not 50


def test_volume_points_cap_at_5000_combined_contracts():
    at_cap = compute_options_pressure_score(
        cp_ratio=1.0, sentiment="neutral", whale_count=0, total_call_vol=3000, total_put_vol=2000,
    )
    beyond_cap = compute_options_pressure_score(
        cp_ratio=1.0, sentiment="neutral", whale_count=0, total_call_vol=10000, total_put_vol=10000,
    )
    assert at_cap["components"]["volume_pts"] == 10.0
    assert beyond_cap["components"]["volume_pts"] == 10.0


def test_volume_points_use_call_plus_put_combined_not_just_one_side():
    result = compute_options_pressure_score(
        cp_ratio=1.0, sentiment="neutral", whale_count=0, total_call_vol=1250, total_put_vol=1250,
    )
    # combined = 2500, 2500/5000*10 = 5.0
    assert result["components"]["volume_pts"] == 5.0


def test_zero_volume_contributes_zero_not_a_crash():
    result = compute_options_pressure_score(
        cp_ratio=1.0, sentiment="neutral", whale_count=0, total_call_vol=0, total_put_vol=0,
    )
    assert result["components"]["volume_pts"] == 0.0


def test_sentiment_passes_through_verbatim_for_the_caller_to_read_direction_separately():
    result = compute_options_pressure_score(
        cp_ratio=3.0, sentiment="strongly_bullish", whale_count=0, total_call_vol=0, total_put_vol=0,
    )
    assert result["sentiment"] == "strongly_bullish"


# ── MPE-07: GEX-proximity enrichment ────────────────────────────────────────────────────────

def test_no_gex_argument_means_no_uw_component_in_the_response():
    result = compute_options_pressure_score(
        cp_ratio=1.0, sentiment="neutral", whale_count=0, total_call_vol=0, total_put_vol=0,
    )
    assert "uw_gex_proximity_pts" not in result["components"]


def test_gex_argument_with_no_distance_field_is_treated_as_absent():
    result = compute_options_pressure_score(
        cp_ratio=1.0, sentiment="neutral", whale_count=0, total_call_vol=0, total_put_vol=0,
        gex={"gamma_flip": None, "distance_to_flip_pct": None},
    )
    assert "uw_gex_proximity_pts" not in result["components"]


def test_price_at_the_flip_level_scores_the_full_20_gex_points():
    result = compute_options_pressure_score(
        cp_ratio=1.0, sentiment="neutral", whale_count=0, total_call_vol=0, total_put_vol=0,
        gex={"gamma_flip": 100.0, "distance_to_flip_pct": 0.0},
    )
    assert result["components"]["uw_gex_proximity_pts"] == 20.0


def test_price_10_pct_or_more_from_the_flip_level_scores_zero_gex_points():
    at_10 = compute_options_pressure_score(
        cp_ratio=1.0, sentiment="neutral", whale_count=0, total_call_vol=0, total_put_vol=0,
        gex={"gamma_flip": 100.0, "distance_to_flip_pct": 10.0},
    )
    beyond = compute_options_pressure_score(
        cp_ratio=1.0, sentiment="neutral", whale_count=0, total_call_vol=0, total_put_vol=0,
        gex={"gamma_flip": 100.0, "distance_to_flip_pct": 25.0},
    )
    assert at_10["components"]["uw_gex_proximity_pts"] == 0.0
    assert beyond["components"]["uw_gex_proximity_pts"] == 0.0


def test_gex_points_add_on_top_of_the_free_tier_score():
    without_gex = compute_options_pressure_score(
        cp_ratio=1.0, sentiment="neutral", whale_count=0, total_call_vol=0, total_put_vol=0,
    )
    with_gex = compute_options_pressure_score(
        cp_ratio=1.0, sentiment="neutral", whale_count=0, total_call_vol=0, total_put_vol=0,
        gex={"gamma_flip": 100.0, "distance_to_flip_pct": 0.0},
    )
    assert with_gex["score"] == without_gex["score"] + 20.0


def test_score_never_exceeds_100_even_with_every_component_maxed():
    result = compute_options_pressure_score(
        cp_ratio=5.0, sentiment="strongly_bullish", whale_count=10, total_call_vol=10000, total_put_vol=10000,
        gex={"gamma_flip": 100.0, "distance_to_flip_pct": 0.0},
    )
    assert result["score"] == 100.0
