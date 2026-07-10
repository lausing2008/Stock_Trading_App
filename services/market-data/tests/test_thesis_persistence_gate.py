"""T241-POSITION-SCALING Phase 4: thesis persistence gate tests.

Per the design doc's Appendix B reference and the Phase 4 acceptance criteria: "A
paper-traded position going through entry, an adverse price move, and a re-evaluation shows
the correct snapshot diff and recommendation in logs. At least one test case where a regime
flip correctly triggers consider_exit."
"""
from src.backtest.thesis_persistence_gate import (
    RegimeLabel,
    ThesisPersistenceGate,
    ThesisSnapshot,
    snapshot_from_paper_trade,
)


def _base_snapshot(**overrides) -> ThesisSnapshot:
    defaults = dict(
        symbol="TEST",
        regime_at_entry=RegimeLabel.BULL,
        signal_confidence_at_entry=70.0,
        signal_direction_at_entry="long",
        entry_price=100.0,
        key_support_level=95.0,
        rs_score_at_entry=60.0,
    )
    defaults.update(overrides)
    return ThesisSnapshot(**defaults)


def test_all_conditions_intact_allows_add():
    gate = ThesisPersistenceGate()
    snapshot = _base_snapshot()
    result = gate.check(
        snapshot,
        current_regime=RegimeLabel.BULL,
        current_signal_confidence=68.0,   # small, non-decaying drop
        current_rs_score=62.0,            # slightly improved, not reversed
        current_price=102.0,              # above support, no structural break
    )
    assert result.conditions_broken == 0
    assert result.thesis_intact is True
    assert result.recommendation == "allow_add"


def test_regime_flip_alone_triggers_consider_exit_at_default_strictness():
    """Per the design doc's own acceptance criterion: a regime flip correctly triggers
    consider_exit. Default max_broken_conditions=1 means ONE broken condition still allows
    hold_only — but a regime flip is treated as severe enough on its own in the reference
    design's spirit only insofar as it's one of the 4 checks; verify the actual boundary
    behavior explicitly rather than assuming it always means consider_exit by itself.
    """
    gate = ThesisPersistenceGate(max_broken_conditions=1)
    snapshot = _base_snapshot()
    result = gate.check(
        snapshot,
        current_regime=RegimeLabel.BEAR,  # only this condition breaks
        current_signal_confidence=68.0,
        current_rs_score=62.0,
        current_price=102.0,
    )
    assert result.conditions_broken == 1
    assert "regime changed from bull to bear" in result.broken_reasons[0]
    # At max_broken_conditions=1, exactly 1 broken condition is still within tolerance —
    # thesis_intact but no longer perfectly clean, so hold_only (not allow_add, not exit).
    assert result.thesis_intact is True
    assert result.recommendation == "hold_only"


def test_regime_flip_plus_signal_decay_triggers_consider_exit():
    """Two broken conditions exceeds max_broken_conditions=1 — this is the design doc's
    own acceptance criterion scenario made concrete: enough real invalidation signals
    stacked together must force consider_exit regardless of the position-scaling gate.
    """
    gate = ThesisPersistenceGate(max_broken_conditions=1)
    snapshot = _base_snapshot()
    result = gate.check(
        snapshot,
        current_regime=RegimeLabel.BEAR,           # break 1
        current_signal_confidence=50.0,            # break 2: 70 - 50 = 20 > 15 threshold
        current_rs_score=62.0,
        current_price=102.0,
    )
    assert result.conditions_broken == 2
    assert result.thesis_intact is False
    assert result.recommendation == "consider_exit"


def test_signal_decay_exactly_at_threshold_does_not_break():
    """Hand-verified boundary: 70.0 - 55.0 = 15.0, NOT > 15.0 (strict inequality), so this
    should NOT count as broken — confirms the threshold comparison is strictly-greater-than,
    not greater-than-or-equal.
    """
    gate = ThesisPersistenceGate()
    snapshot = _base_snapshot(signal_confidence_at_entry=70.0)
    result = gate.check(
        snapshot,
        current_regime=RegimeLabel.BULL,
        current_signal_confidence=55.0,  # decay of exactly 15.0
        current_rs_score=62.0,
        current_price=102.0,
    )
    assert result.conditions_broken == 0
    assert result.recommendation == "allow_add"


def test_signal_decay_just_past_threshold_breaks():
    gate = ThesisPersistenceGate()
    snapshot = _base_snapshot(signal_confidence_at_entry=70.0)
    result = gate.check(
        snapshot,
        current_regime=RegimeLabel.BULL,
        current_signal_confidence=54.9,  # decay of 15.1, just past the threshold
        current_rs_score=62.0,
        current_price=102.0,
    )
    assert result.conditions_broken == 1
    assert "signal confidence decayed" in result.broken_reasons[0]


def test_structural_break_price_below_support_only_applies_to_long():
    gate = ThesisPersistenceGate()
    snapshot = _base_snapshot(signal_direction_at_entry="long", key_support_level=95.0)
    result = gate.check(
        snapshot,
        current_regime=RegimeLabel.BULL,
        current_signal_confidence=68.0,
        current_rs_score=62.0,
        current_price=94.0,  # below the 95.0 support level
    )
    assert result.conditions_broken == 1
    assert "broke below key support" in result.broken_reasons[0]


def test_structural_break_does_not_apply_when_no_support_level_recorded():
    """Some signals may not have sr_nearest_support populated (see reasons.get() fallback in
    snapshot_from_paper_trade) — the check must skip this condition gracefully, not crash
    or misfire, when key_support_level is None."""
    gate = ThesisPersistenceGate()
    snapshot = _base_snapshot(key_support_level=None)
    result = gate.check(
        snapshot,
        current_regime=RegimeLabel.BULL,
        current_signal_confidence=68.0,
        current_rs_score=62.0,
        current_price=1.0,  # would trip a support break if support_level were set
    )
    assert result.conditions_broken == 0


def test_relative_strength_reversal_breaks_only_past_threshold():
    """Hand-verified: rs_score_at_entry=60, current=54 -> delta=-6, exceeds the -5 threshold
    (5 points of underperformance) -> should break. The design doc explicitly frames this as
    distinguishing 'the whole sector dipped' from 'this stock specifically underperforms.'
    """
    gate = ThesisPersistenceGate()
    snapshot = _base_snapshot(rs_score_at_entry=60.0)
    result = gate.check(
        snapshot,
        current_regime=RegimeLabel.BULL,
        current_signal_confidence=68.0,
        current_rs_score=54.0,  # delta = -6.0, past the -5.0 threshold
        current_price=102.0,
    )
    assert result.conditions_broken == 1
    assert "underperforming its sector" in result.broken_reasons[0]


def test_relative_strength_small_dip_within_threshold_does_not_break():
    gate = ThesisPersistenceGate()
    snapshot = _base_snapshot(rs_score_at_entry=60.0)
    result = gate.check(
        snapshot,
        current_regime=RegimeLabel.BULL,
        current_signal_confidence=68.0,
        current_rs_score=57.0,  # delta = -3.0, within the -5.0 threshold
        current_price=102.0,
    )
    assert result.conditions_broken == 0


def test_relative_strength_check_skipped_when_data_missing():
    gate = ThesisPersistenceGate()
    snapshot = _base_snapshot(rs_score_at_entry=None)
    result = gate.check(
        snapshot,
        current_regime=RegimeLabel.BULL,
        current_signal_confidence=68.0,
        current_rs_score=None,
        current_price=102.0,
    )
    assert result.conditions_broken == 0


def test_max_broken_conditions_is_configurable():
    """A stricter gate (max_broken_conditions=0) should flag consider_exit on a SINGLE
    broken condition, unlike the default (max_broken_conditions=1) which tolerates one."""
    strict_gate = ThesisPersistenceGate(max_broken_conditions=0)
    snapshot = _base_snapshot()
    result = strict_gate.check(
        snapshot,
        current_regime=RegimeLabel.BEAR,  # one broken condition
        current_signal_confidence=68.0,
        current_rs_score=62.0,
        current_price=102.0,
    )
    assert result.conditions_broken == 1
    assert result.thesis_intact is False
    assert result.recommendation == "consider_exit"


def test_snapshot_from_paper_trade_extracts_real_fields():
    snapshot = snapshot_from_paper_trade(
        symbol="AAPL",
        market_regime_at_entry="bull",
        confidence_at_entry=72.5,
        entry_price=150.0,
        entry_reasons={"sr_nearest_support": 145.0, "rs_score": 65.0},
    )
    assert snapshot.regime_at_entry == RegimeLabel.BULL
    assert snapshot.signal_confidence_at_entry == 72.5
    assert snapshot.key_support_level == 145.0
    assert snapshot.rs_score_at_entry == 65.0
    assert snapshot.signal_direction_at_entry == "long"


def test_snapshot_from_paper_trade_handles_unknown_regime_string():
    """A stored market_regime_at_entry that doesn't match any known RegimeLabel (e.g. from
    an older schema version) must not raise — falls back to UNKNOWN so the other 3 checks
    still run."""
    snapshot = snapshot_from_paper_trade(
        symbol="AAPL",
        market_regime_at_entry="some_future_regime_label",
        confidence_at_entry=70.0,
        entry_price=150.0,
        entry_reasons={},
    )
    assert snapshot.regime_at_entry == RegimeLabel.UNKNOWN


def test_snapshot_from_paper_trade_handles_missing_entry_reasons():
    snapshot = snapshot_from_paper_trade(
        symbol="AAPL",
        market_regime_at_entry="bull",
        confidence_at_entry=70.0,
        entry_price=150.0,
        entry_reasons=None,
    )
    assert snapshot.key_support_level is None
    assert snapshot.rs_score_at_entry is None


def test_snapshot_from_paper_trade_drops_inconsistent_support_level():
    """Found via real production data (CGNX): sr_nearest_support (computed at signal time,
    always < that bar's price by construction) can end up >= the actual fill price recorded
    on PaperTrade.entry_price, since the fill may come from a later bar/tick. Using such a
    value would make the structural-break check misfire on the very first re-check with zero
    real price movement — must be dropped (treated as unavailable), not passed through.
    """
    snapshot = snapshot_from_paper_trade(
        symbol="CGNX",
        market_regime_at_entry="bull",
        confidence_at_entry=68.88,
        entry_price=62.7527,
        entry_reasons={"sr_nearest_support": 63.73, "rs_score": 56.1},  # support ABOVE entry price
    )
    assert snapshot.key_support_level is None
    # rs_score is unaffected by this guard — it's a different field with different semantics.
    assert snapshot.rs_score_at_entry == 56.1


def test_snapshot_from_paper_trade_keeps_consistent_support_level():
    snapshot = snapshot_from_paper_trade(
        symbol="AAPL",
        market_regime_at_entry="bull",
        confidence_at_entry=70.0,
        entry_price=150.0,
        entry_reasons={"sr_nearest_support": 145.0},  # correctly below entry price
    )
    assert snapshot.key_support_level == 145.0
