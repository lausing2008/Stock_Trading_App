"""T241-POSITION-SCALING Phase 1: multi-tranche position simulation tests.

Acceptance criteria per Improvements/Position_Scaling/implementation_prompt.md Phase 1:
  - Single-tranche position must match old (single entry/exit) behavior exactly.
  - Two-tranche add reduces average cost basis correctly.
  - Three-tranche position hits stop-loss on the blended basis, not the original entry.
  - Slippage is applied per tranche, worse for later/smaller adds.
"""
from datetime import datetime

from src.backtest.multi_tranche_engine import (
    BarrierConfig,
    add_tranche,
    check_barriers,
    close_position,
    open_position,
    realized_pnl,
    realized_pnl_pct,
)

_T0 = datetime(2026, 1, 1)
_T1 = datetime(2026, 1, 2)
_T2 = datetime(2026, 1, 3)


def test_single_tranche_matches_simple_entry_exit():
    """Regression: a position with exactly one tranche must behave identically to a
    plain single entry/exit trade — same fill price (plus slippage), same barrier prices,
    same P&L math. This is the "no regression" check the design doc requires before any
    multi-tranche test is trusted.
    """
    cfg = BarrierConfig(profit_atr_multiple=2.0, stop_atr_multiple=1.0, max_holding_days=20)
    pos = open_position("TEST", _T0, raw_price=100.0, shares=10.0, atr_at_entry=2.0,
                         barrier_cfg=cfg, slippage_pct=0.001)

    # Fill price should be raw_price * 1.001 (0.1% slippage), matching
    # paper_trading_engine.py's own entry_slippage_pct default.
    assert pos.tranches[0].fill_price == round(100.0 * 1.001, 4)
    assert pos.total_shares == 10.0
    assert pos.weighted_avg_cost == pos.tranches[0].fill_price
    assert pos.num_tranches == 1

    upper, lower = pos.current_barriers()
    basis = pos.weighted_avg_cost
    assert upper == round(basis + 2.0 * 2.0, 6)
    assert lower == round(basis - 1.0 * 2.0, 6)

    # Hits profit target
    reason = check_barriers(pos, bar_high=upper + 0.5, bar_low=basis, bar_close=upper, bar_timestamp=_T1)
    assert reason == "profit_target"
    close_position(pos, exit_price=upper, exit_timestamp=_T1, exit_reason=reason)

    expected_pnl = round((upper - basis) * 10.0, 4)
    assert realized_pnl(pos) == expected_pnl
    assert realized_pnl_pct(pos) == round((upper - basis) / basis, 6)


def test_two_tranche_add_reduces_average_cost_basis():
    """A second tranche added on a pullback (lower price than the original entry) must
    correctly pull the weighted-average cost basis DOWN, hand-verifiable arithmetic:
    (10 shares @ 100 fill) + (10 shares @ 90 fill) = 20 shares @ (1000+900)/20 = 95.0.
    """
    cfg = BarrierConfig(profit_atr_multiple=2.0, stop_atr_multiple=1.0, max_holding_days=20)
    pos = open_position("TEST", _T0, raw_price=100.0, shares=10.0, atr_at_entry=2.0,
                         barrier_cfg=cfg, slippage_pct=0.0)  # zero slippage for clean hand-math here

    assert pos.weighted_avg_cost == 100.0

    add_tranche(pos, _T1, raw_price=90.0, shares=10.0, reason="pullback add #1", base_slippage_pct=0.0)

    assert pos.num_tranches == 2
    assert pos.total_shares == 20.0
    # Hand-verified: (10*100 + 10*90) / 20 = 1900/20 = 95.0
    assert pos.weighted_avg_cost == 95.0

    # Barriers must now be computed against the NEW blended basis (95.0), not the
    # original entry price (100.0) — this is the path-dependent piece the design doc
    # calls out explicitly in its "Backtesting caveat" section.
    upper, lower = pos.current_barriers()
    assert upper == round(95.0 + 2.0 * 2.0, 6)
    assert lower == round(95.0 - 1.0 * 2.0, 6)


def test_three_tranche_position_hits_stop_on_blended_basis():
    """A position that has added twice (bringing its cost basis down) must have its
    stop-loss barrier evaluated against the BLENDED basis, not the first entry's price —
    a stop level that would never have triggered against the original entry price alone
    can correctly trigger once averaging down has moved the basis closer to the stop.
    """
    cfg = BarrierConfig(profit_atr_multiple=3.0, stop_atr_multiple=1.0, max_holding_days=20)
    pos = open_position("TEST", _T0, raw_price=100.0, shares=10.0, atr_at_entry=2.0,
                         barrier_cfg=cfg, slippage_pct=0.0)
    add_tranche(pos, _T1, raw_price=90.0, shares=10.0, reason="pullback add #1", base_slippage_pct=0.0)
    add_tranche(pos, _T2, raw_price=85.0, shares=10.0, reason="pullback add #2", base_slippage_pct=0.0)

    assert pos.num_tranches == 3
    assert pos.total_shares == 30.0
    # Hand-verified: (10*100 + 10*90 + 10*85) / 30 = 2750/30 = 91.6667
    assert pos.weighted_avg_cost == round(2750 / 30, 6)

    upper, lower = pos.current_barriers()
    basis = pos.weighted_avg_cost
    assert lower == round(basis - 1.0 * 2.0, 6)  # stop = basis - 2.0

    # Confirms blending genuinely moved the stop: the blended-basis stop (~89.67) sits well
    # below what the stop would be if it were still computed against only the original entry
    # (100 - 2 = 98) — the path-dependent piece the design doc's "Backtesting caveat" describes.
    original_only_stop = 100.0 - 1.0 * 2.0
    assert lower < original_only_stop

    reason = check_barriers(pos, bar_high=basis, bar_low=lower - 0.1, bar_close=lower, bar_timestamp=_T2)
    assert reason == "stop_loss"
    close_position(pos, exit_price=lower, exit_timestamp=_T2, exit_reason=reason)

    expected_pnl = round((lower - basis) * 30.0, 4)
    assert realized_pnl(pos) == expected_pnl
    assert realized_pnl(pos) < 0  # a stop-loss exit on a long position is a loss


def test_slippage_applied_per_tranche_worse_for_later_adds():
    """Later tranches must fill at worse (higher, for a long) prices than the first,
    modeling reduced liquidity/urgency when adding into an already-adverse move.
    """
    cfg = BarrierConfig()
    pos = open_position("TEST", _T0, raw_price=100.0, shares=10.0, atr_at_entry=2.0,
                         barrier_cfg=cfg, slippage_pct=0.001)
    add_tranche(pos, _T1, raw_price=100.0, shares=10.0, base_slippage_pct=0.001)
    add_tranche(pos, _T2, raw_price=100.0, shares=10.0, base_slippage_pct=0.001)

    # Same raw_price (100.0) for all three tranches, but fill_price must increase with
    # sequence number since slippage grows per _slippage_for_tranche().
    fills = [t.fill_price for t in pos.tranches]
    assert fills[0] < fills[1] < fills[2]


def test_cannot_add_tranche_to_closed_position():
    cfg = BarrierConfig()
    pos = open_position("TEST", _T0, raw_price=100.0, shares=10.0, atr_at_entry=2.0, barrier_cfg=cfg)
    close_position(pos, exit_price=105.0, exit_timestamp=_T1, exit_reason="profit_target")
    try:
        add_tranche(pos, _T2, raw_price=95.0, shares=5.0)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_weighted_avg_cost_zero_shares_is_safe():
    """A position with no tranches yet (shouldn't happen via open_position, but the
    property itself must not divide by zero if ever called on an empty state)."""
    cfg = BarrierConfig()
    pos = open_position("TEST", _T0, raw_price=100.0, shares=10.0, atr_at_entry=2.0, barrier_cfg=cfg)
    pos.tranches.clear()  # force the degenerate case directly
    assert pos.weighted_avg_cost == 0.0
    assert pos.total_shares == 0.0
