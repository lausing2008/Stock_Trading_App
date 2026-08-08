"""Tests for T230-DATA-BROKERQUOTE's route layer — GET /broker/connections/{id}/quote.

broker.py can't be imported directly in this test environment (needs a real db/Postgres
session) — tested via source-text extraction, matching test_broker_route_staleauth_detection.py's
established technique for this exact constraint.
"""
import pathlib

_broker_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "broker.py"
_broker_source = _broker_path.read_text()


def _route_body(func_name: str) -> str:
    start = _broker_source.index(f"def {func_name}(")
    next_def = _broker_source.index("\ndef ", start + 1)
    next_router = _broker_source.find("\n@router", start + 1)
    end = min(x for x in (next_def, next_router) if x != -1)
    return _broker_source[start:end]


class TestGetBrokerQuoteRoute:
    def test_route_is_registered_at_the_documented_path(self):
        assert '@router.get("/connections/{conn_id}/quote")' in _broker_source

    def test_returns_400_when_not_authorized(self):
        body = _route_body("get_broker_quote")
        assert "conn.is_authorized" in body
        assert "HTTPException(400" in body

    def test_returns_400_on_an_empty_symbols_list(self):
        """A caller passing an empty/whitespace-only symbols string must get a clear 400,
        not silently call get_quote([]) and return an empty quotes list that looks identical
        to 'authorized but genuinely no data'."""
        body = _route_body("get_broker_quote")
        assert "if not sym_list:" in body
        assert "HTTPException(400" in body

    def test_symbols_param_is_split_and_uppercased(self):
        body = _route_body("get_broker_quote")
        assert "symbols.split(\",\")" in body
        assert ".strip().upper()" in body

    def test_returns_501_for_brokers_that_dont_support_get_quote(self):
        """Matches list_orders/get_order_history's own established convention — 501 (not a
        silently empty list) for a broker type with no real quote API."""
        body = _route_body("get_broker_quote")
        assert "except NotImplementedError:" in body
        assert "HTTPException(501" in body


class TestGetBrokerQuoteStaleAuthDetection:
    """Matches get_account_info/get_order_history's own already-established BUG-BROKERROUTE-
    STALEAUTH fix exactly — a genuinely expired token must flip is_authorized and notify the
    user, not silently 502 while the DB keeps claiming the connection is still authorized."""

    def test_checks_for_a_token_rejected_error(self):
        body = _route_body("get_broker_quote")
        assert "_is_token_rejected_error(exc)" in body

    def test_marks_unauthorized_and_notifies_on_token_rejection(self):
        body = _route_body("get_broker_quote")
        assert "_mark_broker_unauthorized_and_notify(session, conn)" in body

    def test_raises_401_not_a_generic_502_on_token_rejection(self):
        body = _route_body("get_broker_quote")
        rejected_idx = body.index("_is_token_rejected_error(exc)")
        the_401_idx = body.index("HTTPException(401")
        the_502_idx = body.index("HTTPException(502")
        assert rejected_idx < the_401_idx < the_502_idx
