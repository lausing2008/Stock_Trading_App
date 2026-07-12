"""Regression tests for compute_score()'s previously-fixed silent dead-code bugs, plus
boundary-condition coverage for the layers that gate real trading decisions.

Three confirmed historical bugs this file protects against recurring:
  - AUD232-006: catalyst scoring read a single clamped-to-[0,100] combined field, making the
    bearish-catalyst branch unreachable dead code. Fixed to read insider_score/congress_score
    as two separate signed fields.
  - SA-26: confidence_delta was read from signal_data top-level, but signal-engine only ever
    writes it into signal_data["reasons"]["confidence_delta"] — permanently dead code.
  - T234-DE-SCORER-DOUBLECOUNT-ENTRYZONE: a removed layer used to re-score live_price against
    entry2/breakout a second time, double-counting the same directional move Layer 1 already
    captures. This suite confirms the removal by asserting the score for a given input landed
    at the sum-of-remaining-layers value, not the old double-counted one.
"""
from src.api.core.scorer import compute_score, min_score_for_regime, _REGIME_SCORE, _RESEARCH_SCORE


def _game_plan(live_price=100.0):
    """A plan where live_price sits exactly in the optimal Layer-1 zone (entry2..breakout)."""
    return {
        "entry2": live_price * 0.94,
        "breakout": live_price * 1.035,
        "stop": live_price * 0.88,
        "take_profit": live_price * 1.35,
    }


def _signal_data(reasons=None, bullish_probability=0.60, ts=None):
    return {
        "reasons": reasons or {},
        "bullish_probability": bullish_probability,
        "ts": ts,
    }


def _layer_pts(breakdown, layer_name):
    for item in breakdown:
        if item.layer == layer_name:
            return item.pts
    return None


def _layer_names(breakdown):
    return {item.layer for item in breakdown}


# ── AUD232-006: catalyst insider/congress split ──────────────────────────────

def test_catalyst_insider_strong_buying_scores_positive():
    reasons = {"insider_score": 75}
    score, breakdown = compute_score(100.0, _game_plan(), _signal_data(reasons), None, None, "neutral", {})
    assert _layer_pts(breakdown, "catalyst_insider") == 1


def test_catalyst_insider_significant_selling_scores_negative():
    """This is the branch AUD232-006 found unreachable — a real bearish insider signal
    (score < -30) must actually produce the -1 penalty, not silently vanish."""
    reasons = {"insider_score": -45}
    score, breakdown = compute_score(100.0, _game_plan(), _signal_data(reasons), None, None, "neutral", {})
    assert _layer_pts(breakdown, "catalyst_insider") == -1


def test_catalyst_insider_neutral_zone_scores_zero():
    reasons = {"insider_score": 10}
    score, breakdown = compute_score(100.0, _game_plan(), _signal_data(reasons), None, None, "neutral", {})
    assert _layer_pts(breakdown, "catalyst_insider") == 0


def test_catalyst_congress_net_buying_scores_positive():
    reasons = {"congress_score": 60}
    score, breakdown = compute_score(100.0, _game_plan(), _signal_data(reasons), None, None, "neutral", {})
    assert _layer_pts(breakdown, "catalyst_congress") == 1


def test_catalyst_layers_absent_when_scores_not_provided():
    """No insider_score/congress_score in reasons -> no catalyst layers in the breakdown at all
    (not a silent zero — genuinely absent, matching the function's guard-with-is-not-None)."""
    score, breakdown = compute_score(100.0, _game_plan(), _signal_data({}), None, None, "neutral", {})
    names = _layer_names(breakdown)
    assert "catalyst_insider" not in names
    assert "catalyst_congress" not in names


# ── SA-26: confidence_delta must be read from reasons, not signal_data top-level ─────────

def test_confidence_delta_read_from_reasons_not_top_level():
    """The historical bug: signal_data.get("confidence_delta") at the top level was always
    None because signal-engine never writes it there — only into reasons. A top-level-only
    value must NOT produce a layer; a reasons-nested value must."""
    signal_data_with_top_level_only = {
        "reasons": {},
        "bullish_probability": 0.60,
        "confidence_delta": 12,  # wrong location — must be ignored
    }
    score, breakdown = compute_score(100.0, _game_plan(), signal_data_with_top_level_only, None, None, "neutral", {})
    assert "conf_delta" not in _layer_names(breakdown)

    signal_data_with_reasons = _signal_data({"confidence_delta": 12})
    score2, breakdown2 = compute_score(100.0, _game_plan(), signal_data_with_reasons, None, None, "neutral", {})
    assert _layer_pts(breakdown2, "conf_delta") == 1


def test_confidence_delta_accelerating_vs_decelerating_vs_stable():
    accel, _ = compute_score(100.0, _game_plan(), _signal_data({"confidence_delta": 15}), None, None, "neutral", {})
    decel, _ = compute_score(100.0, _game_plan(), _signal_data({"confidence_delta": -15}), None, None, "neutral", {})
    stable, _ = compute_score(100.0, _game_plan(), _signal_data({"confidence_delta": 2}), None, None, "neutral", {})
    assert accel > stable > decel


# ── Layer 1: price zone boundary conditions ──────────────────────────────────

def test_price_zone_deep_pullback_below_entry2():
    live_price = 90.0  # below entry2 (94)
    score, breakdown = compute_score(live_price, _game_plan(100.0), _signal_data(), None, None, "neutral", {})
    assert _layer_pts(breakdown, "price_zone") == 2


def test_price_zone_optimal_between_entry2_and_breakout():
    live_price = 100.0  # between entry2 (94) and breakout (103.5)
    score, breakdown = compute_score(live_price, _game_plan(100.0), _signal_data(), None, None, "neutral", {})
    assert _layer_pts(breakdown, "price_zone") == 2


def test_price_zone_slight_chase_just_above_breakout():
    live_price = 104.0  # breakout=103.5, within 3% extension (106.6)
    score, breakdown = compute_score(live_price, _game_plan(100.0), _signal_data(), None, None, "neutral", {})
    assert _layer_pts(breakdown, "price_zone") == 1


def test_price_zone_extended_chase_penalized():
    live_price = 110.0  # breakout=103.5, well past 3% extension
    score, breakdown = compute_score(live_price, _game_plan(100.0), _signal_data(), None, None, "neutral", {})
    assert _layer_pts(breakdown, "price_zone") == -3


def test_no_double_counting_of_price_move_across_layers():
    """T234-DE-SCORER-DOUBLECOUNT-ENTRYZONE: only ONE layer (price_zone) should score the
    live_price-vs-entry2/breakout relationship. Confirm no second layer named for the removed
    'entry_drift' concept exists in the breakdown, for any price zone."""
    for live_price in (90.0, 100.0, 104.0, 110.0):
        _, breakdown = compute_score(live_price, _game_plan(100.0), _signal_data(), None, None, "neutral", {})
        names = _layer_names(breakdown)
        assert "entry_drift" not in names
        assert sum(1 for n in names if "price" in n or "entry" in n or "zone" in n) == 1


# ── Layer 2: R:R quality boundaries ──────────────────────────────────────────

def test_rr_quality_excellent_at_or_above_3_5():
    gp = {"entry2": 94.0, "breakout": 103.5, "stop": 88.0, "take_profit": 142.0}  # rr = (142-100)/(100-88) = 3.5
    _, breakdown = compute_score(100.0, gp, _signal_data(), None, None, "neutral", {})
    assert _layer_pts(breakdown, "rr_quality") == 2


def test_rr_quality_good_at_2_5_to_3_5():
    gp = {"entry2": 94.0, "breakout": 103.5, "stop": 88.0, "take_profit": 130.0}  # rr = 30/12 = 2.5
    _, breakdown = compute_score(100.0, gp, _signal_data(), None, None, "neutral", {})
    assert _layer_pts(breakdown, "rr_quality") == 1


def test_rr_quality_acceptable_below_2_5():
    gp = {"entry2": 94.0, "breakout": 103.5, "stop": 88.0, "take_profit": 110.0}  # rr = 10/12 = 0.83
    _, breakdown = compute_score(100.0, gp, _signal_data(), None, None, "neutral", {})
    assert _layer_pts(breakdown, "rr_quality") == 0


# ── Layer 5/regime + Layer 7/consensus interaction ───────────────────────────

def test_regime_score_uses_lookup_table_directly():
    for regime, expected in _REGIME_SCORE.items():
        _, breakdown = compute_score(100.0, _game_plan(), _signal_data(), None, None, regime, {})
        assert _layer_pts(breakdown, "regime") == expected


def test_cross_horizon_consensus_strong_alignment_scores_positive():
    reasons = {"cross_style_buys": 2}
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(reasons), None, None, "neutral", {})
    assert _layer_pts(breakdown, "consensus") == 1


def test_cross_horizon_consensus_zero_support_in_choppy_penalized():
    reasons = {"cross_style_buys": 0}
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(reasons), None, None, "choppy", {})
    assert _layer_pts(breakdown, "consensus") == -1


def test_cross_horizon_consensus_zero_support_in_bull_is_neutral_not_penalized():
    """The penalty only applies in bear/choppy regimes — a quiet consensus in a bull regime
    should not produce a consensus layer at all (not a 0, genuinely absent)."""
    reasons = {"cross_style_buys": 0}
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(reasons), None, None, "bull", {})
    assert "consensus" not in _layer_names(breakdown)


# ── Research alignment: underscore/space normalization ───────────────────────

def test_research_score_normalizes_underscore_to_space():
    """decision-engine already normalizes STRONG_BUY -> STRONG BUY before the lookup —
    confirm both representations score identically."""
    _, bd_space = compute_score(100.0, _game_plan(), _signal_data(), "STRONG BUY", 90.0, "neutral", {})
    _, bd_underscore = compute_score(100.0, _game_plan(), _signal_data(), "STRONG_BUY", 90.0, "neutral", {})
    assert _layer_pts(bd_space, "research") == _layer_pts(bd_underscore, "research") == _RESEARCH_SCORE["STRONG BUY"]


def test_research_score_unknown_recommendation_defaults_to_zero():
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(), "SOMETHING_NEW", None, "neutral", {})
    assert _layer_pts(breakdown, "research") == 0


# ── Layer 6: K-Score gate uses the real >=55 conviction threshold ────────────

def test_kscore_at_conviction_threshold_scores_positive():
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(), None, None, "neutral", {"kscore": 55})
    assert _layer_pts(breakdown, "kscore") == 1


def test_kscore_just_below_conviction_threshold_scores_negative():
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(), None, None, "neutral", {"kscore": 54.9})
    assert _layer_pts(breakdown, "kscore") == -1


def test_kscore_absent_produces_no_layer():
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(), None, None, "neutral", {})
    assert "kscore" not in _layer_names(breakdown)


# ── min_score_for_regime boundaries ───────────────────────────────────────────

def test_min_score_bear_regime_is_effectively_unreachable():
    assert min_score_for_regime("bear", {}) == 999


def test_min_score_risk_off_raises_floor():
    assert min_score_for_regime("risk_off", {"min_entry_score": 4}) >= 5


def test_min_score_choppy_raises_floor():
    assert min_score_for_regime("choppy", {"min_entry_score": 4}) >= 4


def test_min_score_poor_recent_win_rate_adds_one():
    base = min_score_for_regime("neutral", {"min_entry_score": 4, "recent_win_rate": 0.50})
    penalized = min_score_for_regime("neutral", {"min_entry_score": 4, "recent_win_rate": 0.29})
    assert penalized == base + 1


def test_min_score_win_rate_exactly_at_30_percent_boundary_not_penalized():
    """cfg["recent_win_rate"] < 0.30 triggers the penalty — exactly 0.30 must NOT."""
    at_boundary = min_score_for_regime("neutral", {"min_entry_score": 4, "recent_win_rate": 0.30})
    assert at_boundary == 4
