"""Tests for BUG-BROKERROUTE-STALEAUTH — broker.py's Load Balance / Order History routes
never detected an expired/rejected E*Trade token, leaving conn.is_authorized stuck at True
while the real token was dead, with no re-auth email sent.

Live production incident: E*Trade access tokens hard-expire at midnight ET daily (documented
in T257-ETRADE-PROD-SYSTEMATIC). The paper-trading engine's own broker call sites
(_place_broker_entry/_place_broker_exit/poll_broker_order_fills) already detect this in-loop
via _is_token_rejected_error()/_mark_broker_unauthorized_and_notify() (scheduler.py) — but
GET /broker/connections/{id}/account and .../orders (added later, for the Load Balance button
and the E*Trade Transactions dashboard) never wired in the same detection, so a genuinely
expired token there just produced a generic 502 with the DB still claiming the connection was
authorized, and no re-auth email — reproduced live: a real 401
'oauth_problem=token_expired' response surfaced as a bare "Failed to load" in the UI with no
indication the user needed to re-authorize.

broker.py can't be imported directly in this test environment (needs a real db/Postgres
session) — tested via source-text extraction, matching test_broker_account_key_wiring.py's
established technique.
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


class TestGetAccountInfoStaleAuthDetection:
    def test_checks_for_a_token_rejected_error(self):
        body = _route_body("get_account_info")
        assert "_is_token_rejected_error(exc)" in body

    def test_marks_unauthorized_and_notifies_on_token_rejection(self):
        body = _route_body("get_account_info")
        assert "_mark_broker_unauthorized_and_notify(session, conn)" in body

    def test_raises_401_not_a_generic_502_on_token_rejection(self):
        body = _route_body("get_account_info")
        # The 401 branch must appear BEFORE the generic 502 fallback, inside the
        # `if _is_token_rejected_error(exc):` block — not just present anywhere in the body.
        rejected_idx = body.index("_is_token_rejected_error(exc)")
        the_401_idx = body.index("HTTPException(401")
        the_502_idx = body.index("HTTPException(502")
        assert rejected_idx < the_401_idx < the_502_idx


class TestGetOrderHistoryStaleAuthDetection:
    def test_checks_for_a_token_rejected_error(self):
        body = _route_body("get_order_history")
        assert "_is_token_rejected_error(exc)" in body

    def test_marks_unauthorized_and_notifies_on_token_rejection(self):
        body = _route_body("get_order_history")
        assert "_mark_broker_unauthorized_and_notify(session, conn)" in body

    def test_raises_401_not_a_generic_502_on_token_rejection(self):
        body = _route_body("get_order_history")
        rejected_idx = body.index("_is_token_rejected_error(exc)")
        the_401_idx = body.index("HTTPException(401")
        the_502_idx = body.index("HTTPException(502")
        assert rejected_idx < the_401_idx < the_502_idx

    def test_not_implemented_error_still_raises_501_unaffected_by_this_fix(self):
        """Regression guard: the pre-existing NotImplementedError -> 501 branch (for brokers
        like fidelity_manual with no real order-history API) must be untouched by this fix."""
        body = _route_body("get_order_history")
        assert "except NotImplementedError:" in body
        assert "HTTPException(501" in body
