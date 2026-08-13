"""Tests for T270-ETRADE-PROD-REAL-MONEY (gate 1) — _place_broker_entry() computed a real
order's size ENTIRELY from the simulated PaperPortfolio ledger, with no check against the
REAL linked broker account's actual buying power before placing the order. A simulated
ledger sized larger than the real account (e.g. after a manual withdrawal, or a real
position already open from outside this app) would size a real order the account can't
actually support, relying entirely on the broker's own margin rejection as the only backstop.

_place_broker_entry() needs a real PaperTrade/PaperPortfolio ORM object plus a fake broker
client to exercise fully behaviorally — matching this repo's established precedent
(test_broker_fill_reconciliation_logging.py, test_etrade_token_renewal.py) of using
source-text regression checks for functions this heavily coupled to Docker-only
dependencies, rather than a full behavioral harness for a narrow, single-guard fix.
"""
import pathlib

_PTE_SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py").read_text()


def _place_broker_entry_body() -> str:
    start = _PTE_SOURCE.index("def _place_broker_entry(")
    end = _PTE_SOURCE.index("\n\n\ndef _place_broker_exit(", start)
    return _PTE_SOURCE[start:end]


_BODY = _place_broker_entry_body()


def test_fetches_the_real_account_before_placing_any_order():
    """The buying-power check must call broker.get_account() — the same method that
    already exists and already returns a real buying_power field — before broker.place_order
    is ever reached."""
    assert "broker.get_account()" in _BODY
    get_account_idx = _BODY.index("broker.get_account()")
    place_order_idx = _BODY.index("broker.place_order(")
    assert get_account_idx < place_order_idx


def test_applies_a_safety_margin_not_the_bare_reported_buying_power():
    """A flat 100%-of-reported-buying-power check would still be fragile against the real
    fill price moving between this check and the broker actually executing the market order
    — a safety margin keeps real headroom against that gap."""
    assert "_BROKER_BUYING_POWER_SAFETY_MARGIN" in _BODY
    start = _PTE_SOURCE.index("_BROKER_BUYING_POWER_SAFETY_MARGIN = ")
    line_end = _PTE_SOURCE.index("\n", start)
    margin_line = _PTE_SOURCE[start:line_end]
    assert "0.95" in margin_line


def test_order_value_is_computed_from_the_same_price_and_shares_the_order_will_actually_use():
    """The dollar value checked against buying power must be trade.entry_price *
    trade.shares — the exact same size the subsequent broker.place_order() call submits —
    not some other, potentially stale, quantity."""
    assert "float(trade.entry_price) * int(trade.shares)" in _BODY


def test_skips_the_real_order_when_order_value_exceeds_the_margined_buying_power():
    """The gating comparison itself: order_value strictly greater than buying_power times
    the safety margin must return before broker.place_order() is ever called."""
    check_idx = _BODY.index("if order_value > max_order_value:")
    return_idx = _BODY.index("return", check_idx)
    place_order_idx = _BODY.index("broker.place_order(")
    assert check_idx < return_idx < place_order_idx


def test_insufficient_buying_power_records_a_broker_error_on_the_trade():
    """A skipped real order must be visible on the trade record itself (matching every
    other broker-failure path in this function's own established convention), not just a
    log line nobody may ever read."""
    check_idx = _BODY.index("if order_value > max_order_value:")
    following = _BODY[check_idx:check_idx + 600]
    assert "trade.broker_error" in following


def test_insufficient_buying_power_is_logged_distinctly_from_a_real_order_failure():
    """This is a distinct, pre-flight REJECTION, not an order-placement failure — it must
    get its own event name, not be folded into broker.entry_order_failed (which would make
    the two indistinguishable in logs)."""
    assert 'log.warning(\n                "broker.entry_skipped_insufficient_buying_power"' in _BODY


def test_buying_power_check_fails_open_on_a_fetch_error_not_open_on_a_real_shortfall():
    """Two DIFFERENT failure modes must be handled DIFFERENTLY: a genuine, successfully-
    measured insufficient-buying-power result must BLOCK the order (tested above); a failure
    to even FETCH the account (e.g. a transient network blip) must fail OPEN — matching this
    function's own pre-existing fail-open posture for every other broker call — rather than
    also blocking every real order whenever the account-fetch call itself is flaky."""
    fetch_try_idx = _BODY.index("account = broker.get_account()")
    outer_except_idx = _BODY.index("except Exception as _bp_exc:")
    fetch_block = _BODY[fetch_try_idx:outer_except_idx]
    assert "return" not in fetch_block or _BODY.index("return", fetch_try_idx) < outer_except_idx
    # The fetch-error branch itself must NOT return early (i.e. must fall through to placing
    # the order) — confirmed by checking the except body has no bare `return` of its own.
    except_body_start = _BODY.index("except Exception as _bp_exc:")
    except_body_end = _BODY.index("\n    try:", except_body_start)
    except_body = _BODY[except_body_start:except_body_end]
    assert "return" not in except_body


def test_buying_power_check_runs_before_the_order_placement_try_block():
    """The buying-power check must be its OWN try/except, separate from the order-placement
    try/except below it — confirmed by finding two independent try blocks in the function,
    not one shared block where a buying-power-check failure could be mistaken for (or mask)
    an order-placement failure."""
    assert _BODY.count("    try:") >= 2


def test_docstring_explains_the_fix_and_the_fail_open_rationale():
    assert "T270-ETRADE-PROD-REAL-MONEY" in _BODY
    assert "buying power" in _BODY.lower()


# ── Found via code review (2026-08-13): the buying-power fetch's except block never checked
# for a token-rejection error, unlike every other broker call site in this same function
# (order placement, order-status polling). An expired E*Trade token hitting THIS specific call
# would be silently swallowed as a generic warning and fall through to attempt a real order
# placement on a connection already known to be dead, instead of being marked unauthorized +
# the user notified immediately (T257-ETRADE-PROD-SYSTEMATIC's own stated purpose).

def test_buying_power_fetch_error_checks_for_token_rejection():
    """The buying-power fetch's except block must call _handle_broker_error_if_token_rejected
    — the same helper every other broker call site in this function already uses — before
    falling through to its own generic fail-open warning."""
    except_body_start = _BODY.index("except Exception as _bp_exc:")
    except_body_end = _BODY.index("\n    try:", except_body_start)
    except_body = _BODY[except_body_start:except_body_end]
    assert "_handle_broker_error_if_token_rejected(session, portfolio, _bp_exc)" in except_body


def test_buying_power_fetch_error_still_fails_open_on_a_non_token_error():
    """A genuine token rejection must not change the overall fail-open contract for this
    check — the function must still fall through to attempt a real order placement on ANY
    exception here (transient network blip or a genuine token rejection alike), since the
    token-rejection handling only marks the connection unauthorized/notifies the user; it does
    not (and must not) itself block this specific order attempt, matching the pre-existing
    fail-open posture confirmed by test_buying_power_check_fails_open_on_a_fetch_error_not_
    open_on_a_real_shortfall above."""
    except_body_start = _BODY.index("except Exception as _bp_exc:")
    except_body_end = _BODY.index("\n    try:", except_body_start)
    except_body = _BODY[except_body_start:except_body_end]
    assert "return" not in except_body
