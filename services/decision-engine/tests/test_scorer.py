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


def _layer_note(breakdown, layer_name):
    for item in breakdown:
        if item.layer == layer_name:
            return item.note
    return None


def test_research_score_of_zero_is_shown_in_the_note_not_dropped():
    """T247-DECISIONENGINE-RESEARCHSCORE-FALSY regression guard: a genuine overall_score of
    0 (the worst possible score) must still appear in the breakdown note — the previous
    `if research_score_val else ""` falsy check silently dropped it exactly when it mattered
    most, making SELL-with-score-0 indistinguishable from SELL-with-no-score-at-all."""
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(), "SELL", 0.0, "neutral", {})
    note = _layer_note(breakdown, "research")
    assert "(score 0)" in note, f"expected score 0 to appear in the note, got: {note!r}"


def test_research_score_of_none_omits_the_score_suffix():
    """A genuinely absent score (None, not 0) must still omit the "(score N)" suffix."""
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(), "SELL", None, "neutral", {})
    note = _layer_note(breakdown, "research")
    assert "(score" not in note


def test_research_score_of_a_real_nonzero_value_still_shown():
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(), "BUY", 72.0, "neutral", {})
    note = _layer_note(breakdown, "research")
    assert "(score 72)" in note


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


# ── T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group A: 6 constants made cfg-driven ───────────────
#
# Each test proves the SAME pair used by the default-cfg tests above still applies for {} —
# no behavioral change for any existing caller — and that a non-default cfg value genuinely
# moves the score, confirming the value is truly read from cfg rather than a hardcoded literal
# hiding behind an unused-looking default parameter.

def test_chase_ceiling_item8_default_matches_original_3pct_and_is_overridable():
    live_price = 103.0  # breakout=103.5 (via _game_plan(100)); 100*1.035=103.5, so 103 <= breakout
    # Use a price strictly between breakout and breakout*1.03 to hit the "slight chase" branch.
    plan = _game_plan(100.0)
    price = plan["breakout"] * 1.02  # 2% above breakout, within the default 3% ceiling
    _, breakdown = compute_score(price, plan, _signal_data(), None, None, "neutral", {})
    assert _layer_pts(breakdown, "price_zone") == 1  # "slight chase" branch, not the -3 penalty

    # Tightening the ceiling to 1% pushes this exact same price into the -3 "chasing" branch.
    _, breakdown2 = compute_score(price, plan, _signal_data(), None, None, "neutral", {"chase_ceiling_pct": 1.0})
    assert _layer_pts(breakdown2, "price_zone") == -3


def test_rr_tiers_item9_default_matches_original_and_is_overridable():
    plan = _game_plan(100.0)  # rr = (135-100)/(100-88) = 2.9166 -> "good" tier under the default 2.5
    _, breakdown = compute_score(100.0, plan, _signal_data(), None, None, "neutral", {})
    assert _layer_pts(breakdown, "rr_quality") == 1

    # Raising the "good" floor above the real rr (2.9166) demotes it to the 0-point tier.
    _, breakdown2 = compute_score(100.0, plan, _signal_data(), None, None, "neutral", {"rr_good_threshold": 3.0})
    assert _layer_pts(breakdown2, "rr_quality") == 0


def test_volume_z_bands_item10_default_matches_original_and_is_overridable():
    reasons = {"volume_z": 0.8}  # between the default -0.5/1.0 bounds -> neutral (0)
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(reasons), None, None, "neutral", {})
    assert _layer_pts(breakdown, "volume") == 0

    # Lowering the "strong" threshold to 0.5 makes this same 0.8 clear it -> +1.
    _, breakdown2 = compute_score(
        100.0, _game_plan(), _signal_data(reasons), None, None, "neutral",
        {"volume_z_strong_threshold": 0.5},
    )
    assert _layer_pts(breakdown2, "volume") == 1


def test_bull_prob_thresholds_item11_default_matches_original_and_is_overridable():
    _, breakdown = compute_score(
        100.0, _game_plan(), _signal_data(bullish_probability=0.65), None, None, "neutral", {}
    )
    assert _layer_pts(breakdown, "ml_signal") == 0  # between the default 0.58/0.70 -> moderate

    _, breakdown2 = compute_score(
        100.0, _game_plan(), _signal_data(bullish_probability=0.65), None, None, "neutral",
        {"ml_bull_prob_strong_threshold": 0.60},
    )
    assert _layer_pts(breakdown2, "ml_signal") == 1


def test_confidence_delta_threshold_item12_default_matches_original_and_is_overridable():
    reasons = {"confidence_delta": 6.0}  # under the default +-8 -> "stable" (0)
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(reasons), None, None, "neutral", {})
    assert _layer_pts(breakdown, "conf_delta") == 0

    _, breakdown2 = compute_score(
        100.0, _game_plan(), _signal_data(reasons), None, None, "neutral",
        {"confidence_delta_threshold": 5.0},
    )
    assert _layer_pts(breakdown2, "conf_delta") == 1


def test_catalyst_thresholds_item14_default_matches_original_and_is_overridable():
    reasons = {"insider_score": 55, "congress_score": 45}  # under the default 60/50 -> both 0
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(reasons), None, None, "neutral", {})
    assert _layer_pts(breakdown, "catalyst_insider") == 0
    assert _layer_pts(breakdown, "catalyst_congress") == 0

    _, breakdown2 = compute_score(
        100.0, _game_plan(), _signal_data(reasons), None, None, "neutral",
        {"insider_score_strong_threshold": 50.0, "congress_score_threshold": 40.0},
    )
    assert _layer_pts(breakdown2, "catalyst_insider") == 1
    assert _layer_pts(breakdown2, "catalyst_congress") == 1


# ── AUD-DECIDE-CALIBRATIONFEEDBACK-NOTPORTED: Layer 8, calibrated win-rate feedback ─────
#
# reasons["calibrated_win_rate"] is already forwarded to decision-engine wholesale (a free
# port — paper_trading_engine.py sends the FULL reasons dict, no write-side change needed) but
# this scoring layer itself was never added on the read side, so _should_enter()'s identical
# layer had zero decision-engine equivalent. Gated behind cfg["calibration_feedback_enabled"],
# defaulting to False/absent — a pure no-op for every existing caller that never sets it,
# matching the fallback gate's own promotion-gate discipline (this layer must stay off until a
# real walk-forward sweep validates it).

def test_calibration_feedback_disabled_by_default_even_with_a_real_high_win_rate():
    """The flag being absent entirely must be a strict no-op, not an implicit opt-in — a real,
    strongly-positive calibrated_win_rate must NOT silently boost the score unless the caller
    explicitly turned the flag on."""
    reasons = {"calibrated_win_rate": 0.90, "calibrated_win_rate_count": 200}
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(reasons), None, None, "neutral", {})
    assert "calibration_feedback" not in _layer_names(breakdown)


def test_calibration_feedback_high_win_rate_boosts_score_when_enabled():
    reasons = {"calibrated_win_rate": 0.62, "calibrated_win_rate_count": 85}
    _, breakdown = compute_score(
        100.0, _game_plan(), _signal_data(reasons), None, None, "neutral",
        {"calibration_feedback_enabled": True},
    )
    assert _layer_pts(breakdown, "calibration_feedback") == 1


def test_calibration_feedback_low_win_rate_penalizes_score_when_enabled():
    reasons = {"calibrated_win_rate": 0.20, "calibrated_win_rate_count": 60}
    _, breakdown = compute_score(
        100.0, _game_plan(), _signal_data(reasons), None, None, "neutral",
        {"calibration_feedback_enabled": True},
    )
    assert _layer_pts(breakdown, "calibration_feedback") == -1


def test_calibration_feedback_middle_band_is_neutral_when_enabled():
    """Between the two thresholds (0.35 < wr < 0.55) — neither boosts nor penalizes, matching
    _should_enter()'s own identical dead-zone."""
    reasons = {"calibrated_win_rate": 0.45, "calibrated_win_rate_count": 50}
    _, breakdown = compute_score(
        100.0, _game_plan(), _signal_data(reasons), None, None, "neutral",
        {"calibration_feedback_enabled": True},
    )
    assert "calibration_feedback" not in _layer_names(breakdown)


def test_calibration_feedback_enabled_but_no_measured_value_produces_no_layer():
    """The flag alone is not enough — calibrated_win_rate itself must be present (a real,
    measured value with >=30 samples per _calibrated_win_rate()'s own upstream contract).
    A symbol/horizon/band combo with no calibration data yet must not fabricate one."""
    _, breakdown = compute_score(
        100.0, _game_plan(), _signal_data({}), None, None, "neutral",
        {"calibration_feedback_enabled": True},
    )
    assert "calibration_feedback" not in _layer_names(breakdown)


def test_calibration_feedback_does_not_assume_higher_confidence_means_higher_score():
    """Real production data shows non-monotonic inversions (documented in the code's own
    comment) — this layer must read whichever measured win rate is present, never derive one
    from the signal's own bullish_probability/confidence."""
    reasons_low_conf_high_wr = {"calibrated_win_rate": 0.70, "calibrated_win_rate_count": 40}
    _, breakdown = compute_score(
        100.0, _game_plan(), _signal_data(reasons_low_conf_high_wr, bullish_probability=0.51),
        None, None, "neutral", {"calibration_feedback_enabled": True},
    )
    assert _layer_pts(breakdown, "calibration_feedback") == 1


# ── MPE-05: Market Pressure — composite short-squeeze / options-pressure score ──────────────
# Sent via config_overrides (a generic passthrough), not signal_data["reasons"] — unlike
# calibration_feedback above, there is no "already forwarded wholesale" free port for these;
# a caller must explicitly set squeeze_score/pressure_score for either layer to ever fire.

def test_market_pressure_squeeze_absent_by_default_produces_no_layer():
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(), None, None, "neutral", {})
    assert "market_pressure_squeeze" not in _layer_names(breakdown)


def test_market_pressure_options_absent_by_default_produces_no_layer():
    _, breakdown = compute_score(100.0, _game_plan(), _signal_data(), None, None, "neutral", {})
    assert "market_pressure_options" not in _layer_names(breakdown)


def test_market_pressure_squeeze_high_score_adds_one_point():
    _, breakdown = compute_score(
        100.0, _game_plan(), _signal_data(), None, None, "neutral", {"squeeze_score": 70.0},
    )
    assert _layer_pts(breakdown, "market_pressure_squeeze") == 1


def test_market_pressure_squeeze_below_threshold_produces_no_layer():
    _, breakdown = compute_score(
        100.0, _game_plan(), _signal_data(), None, None, "neutral", {"squeeze_score": 40.0},
    )
    assert "market_pressure_squeeze" not in _layer_names(breakdown)


def test_market_pressure_squeeze_at_exactly_the_boundary_fires():
    _, breakdown = compute_score(
        100.0, _game_plan(), _signal_data(), None, None, "neutral", {"squeeze_score": 65.0},
    )
    assert _layer_pts(breakdown, "market_pressure_squeeze") == 1


def test_market_pressure_options_high_score_adds_one_point():
    _, breakdown = compute_score(
        100.0, _game_plan(), _signal_data(), None, None, "neutral", {"pressure_score": 75.0},
    )
    assert _layer_pts(breakdown, "market_pressure_options") == 1


def test_market_pressure_options_below_threshold_produces_no_layer():
    _, breakdown = compute_score(
        100.0, _game_plan(), _signal_data(), None, None, "neutral", {"pressure_score": 30.0},
    )
    assert "market_pressure_options" not in _layer_names(breakdown)


def test_market_pressure_options_at_exactly_the_boundary_fires():
    _, breakdown = compute_score(
        100.0, _game_plan(), _signal_data(), None, None, "neutral", {"pressure_score": 60.0},
    )
    assert _layer_pts(breakdown, "market_pressure_options") == 1


def test_both_market_pressure_layers_can_fire_together_capped_at_2_total_points():
    _, breakdown = compute_score(
        100.0, _game_plan(), _signal_data(), None, None, "neutral",
        {"squeeze_score": 90.0, "pressure_score": 90.0},
    )
    assert _layer_pts(breakdown, "market_pressure_squeeze") == 1
    assert _layer_pts(breakdown, "market_pressure_options") == 1


def test_market_pressure_layers_never_penalize_a_low_score_only_corroborate_a_high_one():
    """This is corroborating evidence for an already-qualifying entry, not a bidirectional
    signal — a LOW squeeze/pressure score must never subtract points, only a high one adds."""
    _, breakdown = compute_score(
        100.0, _game_plan(), _signal_data(), None, None, "neutral",
        {"squeeze_score": 5.0, "pressure_score": 2.0},
    )
    assert "market_pressure_squeeze" not in _layer_names(breakdown)
    assert "market_pressure_options" not in _layer_names(breakdown)
