"""Tests for AUD262-SIGNALOUTCOME-LASTTRANCHE-WRITEBACK (Deep Audit #2, Tier 262).

_monitor_positions()'s PT-J1 writeback to SignalOutcome previously used the UNBLENDED
final-tranche pnl_pct/pnl_dollar instead of the blended total_pnl_pct/total_pnl_dollar that
T232-PT6 already computes and that trade.pct_return/trade.pnl already use. This meant a trade
that scaled out profitably on the way up and trailed the remainder to a small loss — a real
winner — was recorded as a LOSER in the SignalOutcome ground truth (signal accuracy, confidence
calibration, entry-gate tuning, and the Top-3 conviction alert all read these fields).

_monitor_positions() can't be imported/exercised directly in this test environment (it has heavy
DB/session/live-price/broker dependencies far beyond this fix's actual scope) — matching this
repo's established source-text-extraction technique for functions of this shape (e.g.
test_min_ta_score_config_wiring.py, test_index_trend_config_wiring.py) rather than a full
in-memory-DB harness.
"""
import pathlib

_engine_path = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
)
_engine_source = _engine_path.read_text()


def _writeback_block() -> str:
    """The PT-J1 writeback block inside _monitor_positions()'s exit-execution branch."""
    start = _engine_source.index('# PT-J1: write actual trade result back to signal_outcomes')
    end = _engine_source.index("except Exception as _soe:", start)
    return _engine_source[start:end]


def test_writeback_uses_the_blended_total_pnl_pct_not_unblended_pnl_pct():
    """The whole point of the fix: return_{bucket} must be set from total_pnl_pct (the
    scale-out-blended value trade.pct_return already uses), never the raw unblended pnl_pct."""
    block = _writeback_block()
    assert 'setattr(_so, f"return_{_bucket}", round(total_pnl_pct, 4))' in block
    assert 'setattr(_so, f"return_{_bucket}", round(pnl_pct, 4))' not in block


def test_writeback_uses_the_blended_total_pnl_dollar_not_unblended_pnl_dollar():
    """is_correct_{bucket} must be set from total_pnl_dollar > 0, never pnl_dollar > 0 —
    a trade that scaled out profitably but trailed the remainder to a small loss must be
    recorded as correct (it IS a real winner), not incorrect."""
    block = _writeback_block()
    assert 'setattr(_so, f"is_correct_{_bucket}", total_pnl_dollar > 0)' in block
    assert 'setattr(_so, f"is_correct_{_bucket}", pnl_dollar > 0)' not in block


def test_total_pnl_pct_and_total_pnl_dollar_are_computed_before_the_writeback():
    """The blended values must already exist (via the pre-existing T232-PT6 computation)
    at the point the writeback runs — this fix reuses them, it does not recompute them."""
    assert "total_pnl_dollar = round((trade.realized_pnl or 0.0) + pnl_dollar, 2)" in _engine_source
    assert "total_pnl_pct = (total_pnl_dollar / _cost_basis) if _cost_basis else pnl_pct" in _engine_source
    total_pnl_idx = _engine_source.index("total_pnl_pct = (total_pnl_dollar / _cost_basis)")
    writeback_idx = _engine_source.index('# PT-J1: write actual trade result back to signal_outcomes')
    assert total_pnl_idx < writeback_idx


def test_trade_pct_return_and_pnl_still_use_the_same_blended_values():
    """Regression guard: trade.pct_return/trade.pnl (the values shown in the UI/postmortem)
    must still be set from the blended totals — this fix only touches the SignalOutcome
    writeback, not the trade's own recorded result."""
    assert "trade.pnl                 = total_pnl_dollar" in _engine_source
    assert "trade.pct_return          = round(total_pnl_pct * 100, 4)" in _engine_source
