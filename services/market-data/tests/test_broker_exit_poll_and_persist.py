"""Tests for AUD-B02-EXITIDPERSISTED — _place_broker_exit() previously held a broker-returned
SELL order's ID in a local variable long enough to log it twice and then discarded it: no
column existed to persist it to, so poll_broker_order_fills() (entry-only) had no equivalent
exit poller to exist for. If the immediate-fill check inside _place_broker_exit() didn't
resolve the fill right away (after-hours, partial fill, a slow response), the exit order was
orphaned — real and live at the broker, with no durable link back to the paper trade the UI had
already marked closed. See docs/audits/2026-09-18-a01-a03-broker-lifecycle-scoping.md (A02/B02).

Fixed by adding trade.broker_exit_order_id/broker_exit_fill_confirmed (migration 015, mirroring
migration 012's entry-leg columns) and a new poll_broker_exit_fills(), symmetric to the existing
poll_broker_order_fills().

paper_trading_engine.py can't be imported directly in this test environment — matching
test_broker_poll_fill_confirmed.py's own established source-text-extraction convention for
this exact file.
"""
import pathlib

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _extract(start_marker: str, end_marker: str) -> str:
    start = _pte_source.index(start_marker)
    end = _pte_source.index(end_marker, start)
    return _pte_source[start:end]


_EXIT_FN_SOURCE = _extract("def _place_broker_exit(", "\n\n\ndef poll_broker_order_fills(")
_POLL_EXIT_FN_SOURCE = _extract("def poll_broker_exit_fills(", "\n\n\n# ── T230-PORTFOLIO-BROKER-SYNC")


# ── _place_broker_exit() now persists the exit order ID ─────────────────────────────────────

def test_exit_order_id_is_assigned_to_the_trade():
    assert "trade.broker_exit_order_id = order.order_id" in _EXIT_FN_SOURCE


def test_exit_order_id_is_persisted_before_the_immediate_fill_check_not_after():
    """The exact bug this fix closes: the ID must be durable BEFORE the immediate-fill-check
    try block runs, so it survives even if that inner block raises or the fill isn't ready —
    persisting it only after would leave the same crash window the original defect had."""
    assign_idx = _EXIT_FN_SOURCE.index("trade.broker_exit_order_id = order.order_id")
    fill_check_idx = _EXIT_FN_SOURCE.index("filled = broker.get_order(order.order_id)")
    assert assign_idx < fill_check_idx


def test_immediate_sandbox_fill_marks_the_exit_confirmed_flag():
    section = _extract("filled = broker.get_order(order.order_id)", "\n        except Exception as exc:")
    assert "trade.broker_exit_fill_confirmed = True" in section


# ── poll_broker_exit_fills() — symmetric to poll_broker_order_fills() ────────────────────────

def test_poll_exit_query_filters_on_closed_stage():
    """Unlike the entry poller (which polls OPEN trades), the exit poller must poll CLOSED
    trades — the paper trade is already closed by the time a broker exit order exists at all."""
    assert 'PaperTrade.stage == "closed"' in _POLL_EXIT_FN_SOURCE


def test_poll_exit_query_filters_on_exit_order_id_and_not_yet_confirmed():
    assert "PaperTrade.broker_exit_order_id.isnot(None)" in _POLL_EXIT_FN_SOURCE
    assert "PaperTrade.broker_exit_fill_confirmed.is_(False)" in _POLL_EXIT_FN_SOURCE


def test_poll_exit_query_does_not_reuse_the_entry_columns():
    """Regression guard against copy-paste drift: the exit poller must query its OWN columns,
    not accidentally the entry-leg broker_order_id/broker_fill_confirmed."""
    assert "PaperTrade.broker_order_id.isnot(None)" not in _POLL_EXIT_FN_SOURCE
    assert "PaperTrade.broker_fill_confirmed.is_(False)" not in _POLL_EXIT_FN_SOURCE


def test_poll_exit_reconciles_cash_pnl_and_marks_confirmed_on_a_real_fill():
    section = _extract('if filled.status == "filled" and filled.filled_avg_price:', "\n                # A terminal")
    assert "port.current_cash = round(port.current_cash + delta, 2)" in section
    assert "trade.exit_price = fill_p" in section
    assert "trade.pnl = total_pnl_dollar" in section
    # T401-RATCHET: assert the reconciliation reassigns pct_return from total_pnl_pct — not a
    # source-text substring pinning the exact multiplier/rounding constants, which would still
    # pass if those constants silently changed.
    assert "trade.pct_return = round(total_pnl_pct" in section
    assert "trade.broker_exit_fill_confirmed = True" in section


def test_poll_exit_marks_confirmed_even_when_the_fill_price_matches_exactly():
    """A fill that matches the trade's existing exit_price to the penny still needs
    broker_exit_fill_confirmed set — otherwise it gets re-polled forever despite already
    being resolved, the exact AUD-PT1-BROKERPOLLNEVERCLEARS bug class on the exit leg."""
    section = _extract("else:\n                        trade.broker_exit_fill_confirmed = True",
                        "\n                # A terminal")
    assert "trade.broker_exit_fill_confirmed = True" in section


def test_poll_exit_does_not_clear_broker_exit_order_id_itself():
    """Regression guard mirroring test_broker_order_id_itself_is_never_cleared_by_the_fix for
    the entry side: the ID must remain on the row as a permanent record of which order this
    was, even after broker_exit_fill_confirmed flips to True."""
    assert "trade.broker_exit_order_id = None" not in _POLL_EXIT_FN_SOURCE


def test_poll_exit_uses_the_same_cashrace_lock_discipline_as_the_entry_poller():
    assert "session.refresh(port, with_for_update=True)" in _POLL_EXIT_FN_SOURCE
