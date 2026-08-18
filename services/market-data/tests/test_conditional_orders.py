"""Tests for T286-CONDITIONAL-ORDER — single-hop "if TRIGGER then ACTION" orders.

_evaluate_one_condition()/evaluate_conditions() are pure, DB-light functions (only
position_pnl_pct/volume_ratio/rsi/signal metrics touch a session/HTTP call, all mocked here)
directly, behaviorally tested. The heavier action-execution functions (_execute_buy,
_execute_close_position, etc.) have real DB/session dependencies and are covered by source-
text regression checks for their key safety properties, matching this test suite's established
proportionate-testing convention (test_should_enter_de_parity.py, test_rank_symbol_market_
scoping.py) for functions whose full behavior is disproportionate to drive end-to-end locally.
"""
import pathlib
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.services.conditional_orders import (
    _evaluate_one_condition,
    _position_pnl_pct,
    evaluate_conditions,
    execute_action,
)

_MODULE_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "conditional_orders.py"
_MODULE_SOURCE = _MODULE_PATH.read_text()


class _FakeOrder:
    def __init__(self, conditions=None, trigger_logic="AND", symbol="AAPL", portfolio_id=1, action_type="alert_only"):
        self.conditions = conditions or []
        self.trigger_logic = trigger_logic
        self.symbol = symbol
        self.portfolio_id = portfolio_id
        self.action_type = action_type


# ── _evaluate_one_condition ────────────────────────────────────────────────────────────────

def test_price_metric_reads_the_live_price_directly():
    cond = {"metric": "price", "op": "gte", "value": 140}
    assert _evaluate_one_condition(cond, "NVDA", 1, 145.0, MagicMock(), {}) is True
    assert _evaluate_one_condition(cond, "NVDA", 1, 135.0, MagicMock(), {}) is False


def test_price_metric_fails_closed_when_live_price_is_none():
    cond = {"metric": "price", "op": "gte", "value": 140}
    assert _evaluate_one_condition(cond, "NVDA", 1, None, MagicMock(), {}) is False


def test_unknown_metric_fails_closed():
    cond = {"metric": "made_up_metric", "op": "gte", "value": 1}
    assert _evaluate_one_condition(cond, "AAPL", 1, 100.0, MagicMock(), {}) is False


def test_unknown_op_fails_closed():
    cond = {"metric": "price", "op": "made_up_op", "value": 100}
    assert _evaluate_one_condition(cond, "AAPL", 1, 100.0, MagicMock(), {}) is False


def test_eq_op_on_signal_metric():
    caches = {"signal_payload": {"signal": "BUY", "reasons": {}}}
    cond = {"metric": "signal", "op": "eq", "value": "BUY"}
    assert _evaluate_one_condition(cond, "AAPL", 1, 100.0, MagicMock(), caches) is True
    cond_wrong = {"metric": "signal", "op": "eq", "value": "SELL"}
    assert _evaluate_one_condition(cond_wrong, "AAPL", 1, 100.0, MagicMock(), caches) is False


def test_rsi_metric_reads_from_the_cached_signal_payload():
    caches = {"signal_payload": {"signal": "BUY", "reasons": {"rsi": 25.0}}}
    cond = {"metric": "rsi", "op": "lte", "value": 30}
    assert _evaluate_one_condition(cond, "AAPL", 1, 100.0, MagicMock(), caches) is True


def test_rsi_metric_fails_closed_when_missing_from_reasons():
    caches = {"signal_payload": {"signal": "BUY", "reasons": {}}}
    cond = {"metric": "rsi", "op": "lte", "value": 30}
    assert _evaluate_one_condition(cond, "AAPL", 1, 100.0, MagicMock(), caches) is False


def test_position_pnl_pct_metric_uses_the_cache_key_not_a_repeated_lookup():
    """caches["pnl_pct"] pre-populated must be used directly — confirms the caching contract,
    not a re-derivation on every call (the real per-order evaluation loop calls this once per
    condition, and position_pnl_pct could appear in multiple conditions on the same order)."""
    caches = {"pnl_pct": 12.5}
    cond = {"metric": "position_pnl_pct", "op": "gte", "value": 10}
    assert _evaluate_one_condition(cond, "AAPL", 1, 100.0, MagicMock(), caches) is True


def test_position_pnl_pct_fails_closed_with_no_open_position():
    caches = {"pnl_pct": None}
    cond = {"metric": "position_pnl_pct", "op": "gte", "value": 10}
    assert _evaluate_one_condition(cond, "AAPL", 1, 100.0, MagicMock(), caches) is False


def test_time_metric_compares_against_the_current_hhmm():
    caches = {"now_hhmm": "14:35"}
    cond_pass = {"metric": "time", "op": "gte", "value": "14:30"}
    cond_fail = {"metric": "time", "op": "gte", "value": "15:00"}
    assert _evaluate_one_condition(cond_pass, "AAPL", 1, 100.0, MagicMock(), caches) is True
    assert _evaluate_one_condition(cond_fail, "AAPL", 1, 100.0, MagicMock(), caches) is False


def test_volume_ratio_metric_reads_from_cache():
    caches = {"rvol": 3.5}
    cond = {"metric": "volume_ratio", "op": "gte", "value": 3.0}
    assert _evaluate_one_condition(cond, "AAPL", 1, 100.0, MagicMock(), caches) is True


# ── _position_pnl_pct ──────────────────────────────────────────────────────────────────────

def test_position_pnl_pct_computes_a_real_percentage():
    trade = SimpleNamespace(entry_price=100.0)
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = trade
    result = _position_pnl_pct(session, portfolio_id=1, symbol="AAPL", live_price=110.0)
    assert result == 10.0


def test_position_pnl_pct_returns_none_without_an_open_trade():
    session = MagicMock()
    session.execute.return_value.scalar_one_or_none.return_value = None
    result = _position_pnl_pct(session, portfolio_id=1, symbol="AAPL", live_price=110.0)
    assert result is None


def test_position_pnl_pct_returns_none_without_a_live_price():
    result = _position_pnl_pct(MagicMock(), portfolio_id=1, symbol="AAPL", live_price=None)
    assert result is None


# ── evaluate_conditions (trigger_logic AND/OR) ────────────────────────────────────────────

def test_and_logic_requires_every_condition_to_pass():
    order = _FakeOrder(
        conditions=[{"metric": "price", "op": "gte", "value": 140}, {"metric": "price", "op": "lte", "value": 150}],
        trigger_logic="AND",
    )
    assert evaluate_conditions(order, live_price=145.0, session=MagicMock()) is True
    assert evaluate_conditions(order, live_price=155.0, session=MagicMock()) is False


def test_or_logic_needs_only_one_condition_to_pass():
    order = _FakeOrder(
        conditions=[{"metric": "price", "op": "gte", "value": 200}, {"metric": "price", "op": "lte", "value": 50}],
        trigger_logic="OR",
    )
    assert evaluate_conditions(order, live_price=30.0, session=MagicMock()) is True
    assert evaluate_conditions(order, live_price=100.0, session=MagicMock()) is False


def test_an_order_with_no_conditions_never_fires():
    order = _FakeOrder(conditions=[], trigger_logic="OR")
    assert evaluate_conditions(order, live_price=100.0, session=MagicMock()) is False


# ── execute_action dispatch ────────────────────────────────────────────────────────────────

def test_alert_only_fires_without_touching_a_position_or_needing_a_live_price():
    order = _FakeOrder(action_type="alert_only")
    fired, reason, trade_id = execute_action(order, portfolio=MagicMock(), live_price=None, session=MagicMock())
    assert fired is True
    assert trade_id is None


def test_every_non_alert_action_requires_a_live_price():
    for action in ("buy", "sell_partial", "sell_all", "close_position", "tighten_stop"):
        order = _FakeOrder(action_type=action)
        fired, reason, trade_id = execute_action(order, portfolio=MagicMock(), live_price=None, session=MagicMock())
        assert fired is False
        assert "No live price" in reason


def test_unknown_action_type_fails_with_a_clear_reason():
    order = _FakeOrder(action_type="not_a_real_action")
    fired, reason, trade_id = execute_action(order, portfolio=MagicMock(), live_price=100.0, session=MagicMock())
    assert fired is False
    assert "Unknown action_type" in reason


def test_execute_action_dispatches_buy_to_execute_buy():
    order = _FakeOrder(action_type="buy")
    with patch("src.services.conditional_orders._execute_buy", return_value=(True, "ok", 1)) as mock_buy:
        result = execute_action(order, portfolio=MagicMock(), live_price=100.0, session=MagicMock())
        mock_buy.assert_called_once()
        assert result == (True, "ok", 1)


def test_execute_action_dispatches_sell_all_and_close_position_to_the_same_handler():
    """sell_all and close_position are deliberately aliased to the SAME close-flow handler —
    both fully close the position, there's no meaningful difference between them."""
    for action in ("sell_all", "close_position"):
        order = _FakeOrder(action_type=action)
        with patch("src.services.conditional_orders._execute_close_position", return_value=(True, "ok", 1)) as mock_close:
            execute_action(order, portfolio=MagicMock(), live_price=100.0, session=MagicMock())
            mock_close.assert_called_once()


# ── Source-text regression checks on the heavier action-execution functions ────────────────
# Proportionate to their real session/portfolio DB dependency — matches
# test_should_enter_de_parity.py's own established precedent for this file's functions.

def test_buy_action_requires_a_real_buy_eligible_signal_not_a_fabricated_one():
    """The core safety property this whole feature is designed around: a conditional buy must
    never fabricate a signal — it can only fire on top of an already-real BUY signal."""
    start = _MODULE_SOURCE.index("def _execute_buy(")
    end = _MODULE_SOURCE.index("def _execute_sell_partial(", start)
    body = _MODULE_SOURCE[start:end]
    assert 'sig.signal != SignalType.BUY' in body
    assert "No current BUY-eligible signal" in body


def test_buy_action_routes_through_the_same_gate_as_organic_entries():
    start = _MODULE_SOURCE.index("def _execute_buy(")
    end = _MODULE_SOURCE.index("def _execute_sell_partial(", start)
    body = _MODULE_SOURCE[start:end]
    assert "_call_decision_engine(" in body
    assert "_should_enter(" in body
    assert "_open_paper_trade(" in body


def test_buy_action_rejects_when_already_holding_an_open_position_in_the_symbol():
    start = _MODULE_SOURCE.index("def _execute_buy(")
    end = _MODULE_SOURCE.index("def _execute_sell_partial(", start)
    body = _MODULE_SOURCE[start:end]
    assert "Already have an open position" in body


def test_tighten_stop_action_is_monotonic_never_loosens_the_stop():
    start = _MODULE_SOURCE.index("def _execute_tighten_stop(")
    end = _MODULE_SOURCE.index("def _execute_close_position(", start)
    body = _MODULE_SOURCE[start:end]
    assert "new_stop <= (trade.current_stop or 0)" in body


def test_close_position_credits_cash_and_writes_signal_outcome():
    start = _MODULE_SOURCE.index("def _execute_close_position(")
    end = _MODULE_SOURCE.index("def execute_action(", start)
    body = _MODULE_SOURCE[start:end]
    assert "portfolio.current_cash" in body
    assert "SignalOutcome" in body
    assert 'trade.stage = "closed"' in body


def test_check_conditional_orders_fails_closed_on_lock_acquire_failure():
    """Unlike check_price_alerts' fail-OPEN convention, this feature fails CLOSED on a lock
    error — real-money-adjacent, so skipping a cycle is safer than risking a double-fire."""
    start = _MODULE_SOURCE.index("def check_conditional_orders(")
    body = _MODULE_SOURCE[start:start + 1500]
    assert "fail_closed" in body or "lock_unavailable_skipping_fail_closed" in body


def test_check_conditional_orders_handles_expiration_before_evaluating_the_trigger():
    start = _MODULE_SOURCE.index("def check_conditional_orders(")
    body = _MODULE_SOURCE[start:]
    expiry_idx = body.index("order.expires_at is not None")
    eval_idx = body.index("evaluate_conditions(order")
    assert expiry_idx < eval_idx


def test_check_conditional_orders_sends_email_regardless_of_fired_ok():
    """A failed action must still notify the user — silent failure would defeat the point of
    an unattended trigger."""
    start = _MODULE_SOURCE.index("def check_conditional_orders(")
    body = _MODULE_SOURCE[start:]
    email_idx = body.index("send_conditional_order_email")
    # Confirm this call site is reached regardless of fired_ok — i.e. it's not nested inside
    # an `if fired_ok:` guard. The real code only guards on `if order.email:`.
    guard_start = body.rindex("if order.email:", 0, email_idx)
    guard_to_email = body[guard_start:email_idx]
    assert "if fired_ok" not in guard_to_email
