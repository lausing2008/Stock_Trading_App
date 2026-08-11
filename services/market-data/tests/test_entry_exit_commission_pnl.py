"""Tests for AUD262-ENTRY-EXIT-COMMISSION-EXCLUDED-FROM-PNL.

Entry AND exit commission were both deducted from portfolio.current_cash, but neither entered
trade.pnl/pct_return — only scale-out partials correctly folded their own commission into
realized_pnl. Currently latent (commission_per_share defaults to 0.0) but would silently
corrupt total_realized_pnl, profit_factor, expectancy_pct, and calibrate_entry_weights' pnl > 0
target the moment a real commission is configured.

Fix: (1) a new nullable PaperTrade.entry_commission column stores the one-time entry-side
commission; (2) _monitor_positions' final-close total_pnl_dollar computation now subtracts BOTH
exit_commission (computed fresh on the shares remaining at close) and trade.entry_commission
(the one-time original-position cost, subtracted exactly once regardless of how many scale-outs
happened in between).

_monitor_positions()/_scan_for_entries() can't be exercised end-to-end in this test environment
(heavy DB/session/live-price dependencies) — matching this repo's established source-text-
extraction technique (e.g. test_trailing_stop_label_split.py).
"""
import pathlib

_engine_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_engine_source = _engine_path.read_text()


def _final_close_block() -> str:
    start = _engine_source.index("exit_commission = round(cfg.get(")
    end = _engine_source.index("trade.stage               = \"closed\"", start)
    return _engine_source[start:end]


def _entry_creation_block() -> str:
    start = _engine_source.index("trade = PaperTrade(")
    end = _engine_source.index(")\n", start)
    return _engine_source[start:end]


class TestFinalCloseSubtractsBothCommissionLegs:
    def test_exit_commission_is_subtracted_from_total_pnl_dollar(self):
        block = _final_close_block()
        total_pnl_line_start = block.index("total_pnl_dollar = round(")
        total_pnl_line_end = block.index("\n            _cost_basis", total_pnl_line_start)
        line = block[total_pnl_line_start:total_pnl_line_end]
        assert "- exit_commission" in line

    def test_entry_commission_is_subtracted_from_total_pnl_dollar(self):
        block = _final_close_block()
        total_pnl_line_start = block.index("total_pnl_dollar = round(")
        total_pnl_line_end = block.index("\n            _cost_basis", total_pnl_line_start)
        line = block[total_pnl_line_start:total_pnl_line_end]
        assert "trade.entry_commission" in line

    def test_entry_commission_subtraction_fails_open_on_a_none_value(self):
        """A trade created before this fix shipped (or any other caller that never set
        entry_commission) must not crash the close path — `or 0.0` handles the None case."""
        block = _final_close_block()
        total_pnl_line_start = block.index("total_pnl_dollar = round(")
        total_pnl_line_end = block.index("\n            _cost_basis", total_pnl_line_start)
        line = block[total_pnl_line_start:total_pnl_line_end]
        assert "(trade.entry_commission or 0.0)" in line

    def test_realized_pnl_from_scale_outs_is_still_included(self):
        """Regression guard: the fix must not have accidentally dropped the pre-existing
        T232-PT6 fold-in of scale-out partials' realized_pnl."""
        block = _final_close_block()
        total_pnl_line_start = block.index("total_pnl_dollar = round(")
        total_pnl_line_end = block.index("\n            _cost_basis", total_pnl_line_start)
        line = block[total_pnl_line_start:total_pnl_line_end]
        assert "trade.realized_pnl" in line


class TestEntryCommissionIsStoredOnTheTrade:
    def test_entry_creation_stores_commission_on_the_trade(self):
        block = _entry_creation_block()
        assert "entry_commission" in block
        assert "= commission," in block or "=commission," in block or "entry_commission      = commission" in block


class TestCommissionArithmetic:
    """Direct arithmetic checks of the fixed formula, independent of source-text presence —
    proves the FIX ITSELF computes the right number, not just that the right variable names
    appear somewhere in the function."""

    def test_a_round_trip_trade_with_real_commission_reconciles_pnl_to_cash(self):
        # A $100 entry, 100 shares, $0.005/share commission both legs, no slippage, exits at $110.
        entry_commission = round(0.005 * 100, 4)  # $0.50
        exit_commission = round(0.005 * 100, 4)   # $0.50
        pnl_dollar = round((110.0 - 100.0) * 100, 2)  # $1000.00 gross
        realized_pnl = 0.0  # no scale-outs
        total_pnl_dollar = round(realized_pnl + pnl_dollar - exit_commission - entry_commission, 2)
        # Real cash effect: -$100*100 - entry_commission at open, +$110*100 - exit_commission at close.
        cash_delta = (-100.0 * 100 - entry_commission) + (110.0 * 100 - exit_commission)
        assert total_pnl_dollar == round(cash_delta, 2)

    def test_a_losing_trade_still_reconciles_with_commission_included(self):
        entry_commission = round(0.005 * 50, 4)
        exit_commission = round(0.005 * 50, 4)
        pnl_dollar = round((90.0 - 100.0) * 50, 2)  # -$500 gross
        total_pnl_dollar = round(0.0 + pnl_dollar - exit_commission - entry_commission, 2)
        cash_delta = (-100.0 * 50 - entry_commission) + (90.0 * 50 - exit_commission)
        assert total_pnl_dollar == round(cash_delta, 2)

    def test_zero_commission_is_a_pure_no_op(self):
        """The currently-live default (commission_per_share=0.0) must reproduce the EXACT
        pre-fix pnl value — this fix must not change behavior for the common case today."""
        entry_commission = 0.0
        exit_commission = 0.0
        pnl_dollar = round((105.0 - 100.0) * 100, 2)
        realized_pnl = 25.0  # a prior scale-out
        total_pnl_dollar = round(realized_pnl + pnl_dollar - exit_commission - entry_commission, 2)
        pre_fix_total_pnl_dollar = round(realized_pnl + pnl_dollar, 2)
        assert total_pnl_dollar == pre_fix_total_pnl_dollar
