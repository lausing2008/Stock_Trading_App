"""AUD-B05-MANUALEXITBROKERGAP — manual_exit_trade() previously marked a broker-backed position
closed and credited simulated cash WITHOUT ever selling it at the broker: the UI reported the
position closed while it silently remained open at the broker, with no warning. Fixed by reusing
the SAME shared _place_broker_exit() helper _monitor_positions()'s automatic exit flow and
conditional_orders.py's own close path already call.

manual_exit_trade() calls yf.Ticker(...).fast_info for a live price, which needs a real network
stub to exercise behaviorally end-to-end — narrower than the value of a full harness here, so
this file source-scans the real function (same technique as test_broker_buying_power_gate.py's
own "narrow, single-guard fix" precedent) rather than building a second yfinance-stubbed
extraction harness alongside test_liquidate_portfolio.py's already-thorough behavioral coverage
of the identical guard shape on the sibling liquidate_portfolio() endpoint.
"""
import pathlib

_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
)
_SOURCE = _PATH.read_text()


def _manual_exit_trade_body() -> str:
    start = _SOURCE.index('@router.post("/trades/{trade_id}/exit")')
    end = _SOURCE.index('\n\n\n@router.post("/{portfolio_id}/liquidate")', start)
    return _SOURCE[start:end]


_BODY = _manual_exit_trade_body()


def test_calls_the_shared_place_broker_exit_helper():
    """Must reuse _place_broker_exit() — the SAME helper _monitor_positions() and
    conditional_orders.py already call — not a new, independent broker-routing path."""
    assert "_place_broker_exit(session, trade, p)" in _BODY


def test_broker_exit_call_is_guarded_on_both_portfolio_link_and_trade_broker_id():
    """Belt-and-suspenders guard matching the existing two callers' own convention: check both
    the portfolio's broker connection AND this specific trade's own broker-entered flag, not
    just one or the other."""
    assert "if p.broker_connection_id and trade.broker_order_id:" in _BODY


def test_broker_exit_call_happens_after_the_simulated_close_not_before():
    """_place_broker_exit() reconciles trade.exit_price/pnl against the real fill, which only
    makes sense once _close_one_paper_trade() has already set the simulated baseline values —
    matching _monitor_positions()'s and conditional_orders.py's own established sequencing."""
    close_idx = _BODY.index("_close_one_paper_trade(session, p, trade, exit_price,")
    guard_idx = _BODY.index("if p.broker_connection_id and trade.broker_order_id:")
    exit_call_idx = _BODY.index("_place_broker_exit(session, trade, p)")
    assert close_idx < guard_idx < exit_call_idx


def test_broker_exit_failure_is_caught_and_logged_not_left_to_crash_the_endpoint():
    """A broker-side reconciliation failure must surface in logs, not silently undo (by
    crashing) the manual close the user just explicitly requested — matching
    conditional_orders.py's own try/except-and-log posture around this same helper."""
    guard_idx = _BODY.index("if p.broker_connection_id and trade.broker_order_id:")
    following = _BODY[guard_idx:guard_idx + 500]
    assert "try:" in following
    assert "except Exception as exc:" in following
    assert "log.error(" in following


def test_broker_exit_call_commits_in_the_same_transaction_as_the_simulated_close():
    """The broker-exit call must happen BEFORE session.commit(), so a successful broker fill
    reconciliation (trade.exit_price/pnl overwritten with the real fill) is durable in the
    same commit as the simulated close, not lost to a crash between two separate commits."""
    exit_call_idx = _BODY.index("_place_broker_exit(session, trade, p)")
    commit_idx = _BODY.index("session.commit()")
    assert exit_call_idx < commit_idx
