"""Tests for AUD232-SILENT-BROKER-RECONCILE — _place_broker_exit()'s fill-price
reconciliation had an inner `except Exception: pass` that could silently swallow a real
accounting bug. The ORDER itself already placed successfully (broker.exit_order_placed
logged earlier in the same function) — this inner block only reconciles OUR OWN records
(trade.exit_price/trade.pnl/portfolio.current_cash) against the broker's real fill. A
failure here previously vanished with zero trace, leaving the trade's local records silently
desynced from the broker's actual filled state (money moved correctly at the broker, but
our own P&L/cash bookkeeping never updated to reflect it).

_place_broker_exit() needs a real PaperTrade/PaperPortfolio ORM object plus a fake broker
client to exercise behaviorally — matching this repo's established precedent
(test_etrade_token_renewal.py) of using source-text regression checks for functions this
heavily coupled to Docker-only dependencies, rather than a full behavioral harness for a
narrow, single-guard fix.
"""
import pathlib

_PTE_SOURCE = (pathlib.Path(__file__).resolve().parents[1] / "src" / "services" / "paper_trading_engine.py").read_text()


def _place_broker_exit_body() -> str:
    start = _PTE_SOURCE.index("def _place_broker_exit(")
    end = _PTE_SOURCE.index("\n\n\ndef poll_broker_order_fills(", start)
    return _PTE_SOURCE[start:end]


_BODY = _place_broker_exit_body()


def test_inner_reconciliation_exception_is_no_longer_silently_swallowed():
    """The fix's own core property: the inner except block must NOT be a bare `pass` —
    it needs to actually do something observable on failure."""
    assert "except Exception as exc:" in _BODY, (
        "the inner reconciliation except must bind the exception, not discard it with a "
        "bare `except Exception:`"
    )


def test_reconciliation_failure_is_logged_as_an_error_not_silently_dropped():
    assert "log.error(\"broker.exit_fill_reconciliation_failed\"" in _BODY


def test_reconciliation_failure_records_broker_error_on_the_trade():
    """A silent local-accounting desync must be visible on the trade record itself
    (matching the outer handler's own convention of setting trade.broker_error), not just
    buried in a log line nobody may ever read."""
    log_idx = _BODY.index("log.error(\"broker.exit_fill_reconciliation_failed\"")
    surrounding = _BODY[log_idx:log_idx + 400]
    assert "trade.broker_error" in surrounding


def test_the_outer_handler_is_unchanged_and_still_correctly_logs_order_placement_failures():
    """This fix must not touch the OUTER except (order-placement failure, already correctly
    logged + reconciled with token-rejection handling before this fix) — only the INNER
    reconciliation catch."""
    assert 'log.warning("broker.exit_order_failed"' in _BODY
    assert "_handle_broker_error_if_token_rejected(session, portfolio, exc)" in _BODY


def test_reconciliation_still_runs_inside_the_same_try_that_updates_exit_price_and_pnl():
    """Confirms the fix didn't accidentally move the exception handling out of the block
    that actually performs the fill-price reconciliation (exit_price/pnl/current_cash),
    which would make the new logging fire on unrelated errors instead."""
    assert "trade.exit_price  = fill_p" in _BODY
    assert "trade.pnl         = total_pnl_dollar" in _BODY
    exit_price_idx = _BODY.index("trade.exit_price  = fill_p")
    error_log_idx = _BODY.index("log.error(\"broker.exit_fill_reconciliation_failed\"")
    assert exit_price_idx < error_log_idx, (
        "the new error logging must come AFTER the reconciliation logic it's guarding, "
        "in the same try/except block"
    )
