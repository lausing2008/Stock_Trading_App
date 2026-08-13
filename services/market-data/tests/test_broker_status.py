"""Tests for T260-BROKERSTATUS — _broker_status() in paper_portfolio.py.

Distinguishes 3 states that were previously indistinguishable from stored data alone (both
"never attempted" and "attempted and failed" left broker_order_id null, requiring a log dig
to tell apart — see the /etrade-transactions dashboard investigation that surfaced this gap):
"not_attempted" (broker-linked portfolio, no order ever tried for this trade — e.g. it
predates the link), "failed" (a real attempt genuinely failed at the broker), "synced" (a
real order was placed). Returns None when the portfolio has no broker link at all, so the UI
can omit the column entirely for portfolios that were never meant to place real orders.

paper_portfolio.py can't be imported directly in this test environment (its import chain needs
a real db/Postgres session, which conftest.py stubs wholesale) — _broker_status() is a pure,
dependency-free function, so it's extracted via source-text exec() rather than fighting the
import chain for a ~12-line function, matching this repo's established technique for exactly
this situation (e.g. test_min_kscore_config_wiring.py).
"""
import pathlib
from types import SimpleNamespace

_source_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_source = _source_path.read_text()

_start = _source.index("def _broker_status(")
_end = _source.index('    return "not_attempted"', _start) + len('    return "not_attempted"')
_func_source = _source[_start:_end]

class _AnyType:
    """Stand-in for the real PaperTrade/PaperPortfolio types referenced only as type-hint
    annotations in _broker_status()'s signature — never actually used at runtime, so a bare
    placeholder is sufficient to let exec() evaluate the annotations without error."""


_namespace: dict = {"PaperTrade": _AnyType, "PaperPortfolio": _AnyType}
exec(_func_source, _namespace)
_broker_status = _namespace["_broker_status"]


def _trade(broker_error=None, broker_order_id=None):
    return SimpleNamespace(broker_error=broker_error, broker_order_id=broker_order_id)


def _portfolio(broker_connection_id=None):
    return SimpleNamespace(broker_connection_id=broker_connection_id)


class TestBrokerStatus:
    def test_returns_none_when_portfolio_has_no_broker_link(self):
        t = _trade()
        p = _portfolio(broker_connection_id=None)
        assert _broker_status(t, p) is None

    def test_returns_none_even_if_trade_somehow_has_broker_fields_set(self):
        """A broker link check must come first — an unlinked portfolio's trades should never
        show a broker status at all, regardless of what stray data a trade might carry."""
        t = _trade(broker_order_id="123")
        p = _portfolio(broker_connection_id=None)
        assert _broker_status(t, p) is None

    def test_returns_failed_when_broker_error_is_set(self):
        t = _trade(broker_error="E*Trade place_order failed: 400 ...")
        p = _portfolio(broker_connection_id=1)
        assert _broker_status(t, p) == "failed"

    def test_returns_synced_when_broker_order_id_is_set_and_no_error(self):
        t = _trade(broker_order_id="479")
        p = _portfolio(broker_connection_id=1)
        assert _broker_status(t, p) == "synced"

    def test_returns_not_attempted_when_neither_is_set(self):
        t = _trade()
        p = _portfolio(broker_connection_id=1)
        assert _broker_status(t, p) == "not_attempted"

    def test_failed_takes_priority_over_a_stale_order_id(self):
        """A defensive case: if broker_error is set alongside an order_id (shouldn't normally
        happen given the write-side always clears one when setting the other, but the read
        side should still degrade sensibly), failed must win — an error is the more actionable
        signal to surface."""
        t = _trade(broker_error="timeout", broker_order_id="479")
        p = _portfolio(broker_connection_id=1)
        assert _broker_status(t, p) == "failed"


# ── Real-message threading: the raw broker_error text must reach both API responses ─────────
#
# _broker_status() alone only derives an enum ("failed"/"synced"/"not_attempted"). Before this
# fix, the real error message text (e.g. a specific E*Trade rejection reason) was NEVER sent to
# the frontend at all — only used server-side to derive the enum. This meant a user who saw the
# "Broker ✗" badge and hovered for the reason got a generic static tooltip that actively told
# them to check the E*Trade Transactions dashboard for the reason — which can never show it,
# since a failed order never gets a broker_order_id and therefore never reaches E*Trade's own
# order history at all. Both get_positions()/get_trades() must include the raw broker_error
# string alongside broker_status so the real reason can actually be surfaced.

def _route_handler_source(func_name: str) -> str:
    start = _source.index(f"def {func_name}(")
    end = _source.index("\n\n\n", start)
    return _source[start:end]


class TestBrokerErrorThreadedIntoApiResponses:
    def test_get_positions_includes_raw_broker_error_alongside_broker_status(self):
        body = _route_handler_source("get_positions")
        assert '"broker_status": _broker_status(t, p)' in body
        assert '"broker_error": t.broker_error' in body

    def test_get_trades_includes_raw_broker_error_alongside_broker_status(self):
        body = _route_handler_source("get_trades")
        assert '"broker_status": _broker_status(t, p)' in body
        assert '"broker_error": t.broker_error' in body
