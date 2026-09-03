"""Regression test for AUD-PT1-BROKERPOLLNEVERCLEARS (Paper Trading deep audit, 2026-09-03):
poll_broker_order_fills() selected pending broker orders on broker_order_id IS NOT NULL, but
broker_order_id is never cleared once a fill is confirmed — it's separately relied on by
_place_broker_exit() to decide whether a position needs a real broker SELL on exit. Without a
distinct "still needs polling" flag, every broker-entered position would be silently re-polled
against the real broker API forever, not just until its fill is confirmed. Confirmed live: 0
open broker-order trades exist in production today, so this was a dormant-but-real bug, not yet
observed firing.

paper_trading_engine.py can't be imported directly in this test environment (its import chain
pulls in apscheduler/db.models) — matching test_de_shadow_min_score_fix.py's own established
source-text-extraction convention for this exact file.
"""
import pathlib

_pte_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py"
_pte_source = _pte_path.read_text()


def _extract(start_marker: str, end_marker: str) -> str:
    start = _pte_source.index(start_marker)
    end = _pte_source.index(end_marker, start)
    return _pte_source[start:end]


_POLL_FN_SOURCE = _extract("def poll_broker_order_fills(", "\ndef ")
_ENTRY_FN_SOURCE = _extract("def _place_broker_entry(", "\ndef _place_broker_exit(")


def test_poll_query_filters_on_broker_fill_confirmed_is_false():
    """The exact bug: the pending-orders query only checked broker_order_id.isnot(None), which
    is never cleared — must also require broker_fill_confirmed.is_(False) so a confirmed fill
    stops being re-polled."""
    assert "PaperTrade.broker_fill_confirmed.is_(False)" in _POLL_FN_SOURCE


def test_poll_marks_fill_confirmed_on_a_real_fill():
    section = _extract('if filled.status == "filled" and filled.filled_avg_price:', "\n                # NOTE:")
    assert "trade.broker_fill_confirmed = True" in section


def test_immediate_sandbox_fill_also_marks_fill_confirmed():
    """_place_broker_entry's own immediate-fill check (sandbox fills market orders instantly)
    must set the same flag — otherwise every sandbox-filled position would still get re-polled
    at least once before poll_broker_order_fills' own fill-check sets it."""
    section = _extract('filled = broker.get_order(order.order_id)', "\n        except Exception:")
    assert "trade.broker_fill_confirmed = True" in section


def test_broker_order_id_itself_is_never_cleared_by_the_fix():
    """Regression guard: broker_order_id's presence is separately relied on by
    _place_broker_exit() to route a real broker SELL — the fix must not repurpose or clear it."""
    assert "trade.broker_order_id = None" not in _POLL_FN_SOURCE
    assert "trade.broker_order_id = None" not in _ENTRY_FN_SOURCE


def test_place_broker_exit_still_gates_on_broker_order_id_presence():
    exit_gate_section = _extract("def _place_broker_exit(", "\n    try:")
    assert 'if not trade.broker_order_id' in exit_gate_section
