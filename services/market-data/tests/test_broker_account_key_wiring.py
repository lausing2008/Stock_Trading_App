"""Tests for BUG-BROKERACCTKEY — broker.py's Load Balance / Order History routes silently
passed the wrong value as the E*Trade account key.

Live production incident (found while verifying the new /etrade-transactions dashboard):
GET /broker/connections/{id}/orders (and .../account, the "Load Balance" route) both called
broker.list_orders(conn.account_id or None, ...) / broker.get_account(conn.account_id or None).
conn.account_id is the PLAIN E*Trade account NUMBER (e.g. "823145980"), not the opaque
accountIdKey E*Trade's own API actually requires for the orders/portfolio endpoints.

EtradeBroker._account_id_key(account_id) treats ANY non-empty account_id argument as an
OVERRIDE for the real key, only falling back to self._config["account_id_key"] (the real
key, captured once during OAuth-complete via list_accounts()) when the caller passes None.
Since conn.account_id is always truthy once a connection is authorized, every single call
sent the raw account number to E*Trade instead of the real key — reproduced live and
confirmed via a direct call: E*Trade returned "code":102, "Please enter valid Account Key"
for every affected connection. Fixed by always passing None so EtradeBroker's own correct
fallback is used.

broker.py can't be imported directly in this test environment (its import chain needs a real
db/Postgres session, which conftest.py stubs wholesale) — tested via source-text extraction,
matching test_regime_min_rr_config_wiring.py's/test_min_kscore_config_wiring.py's established
technique for this exact constraint.
"""
import pathlib

_broker_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "broker.py"
_broker_source = _broker_path.read_text()


def _route_body(func_name: str) -> str:
    start = _broker_source.index(f"def {func_name}(")
    # Slice to the next top-level "def " or "@router" after this function's own body starts,
    # matching the established boundary-finding convention in this repo's other source-text
    # extraction tests.
    next_def = _broker_source.index("\ndef ", start + 1)
    next_router = _broker_source.find("\n@router", start + 1)
    end = min(x for x in (next_def, next_router) if x != -1)
    return _broker_source[start:end]


class TestGetAccountInfoAccountKeyWiring:
    def test_calls_get_account_with_none_not_conn_account_id(self):
        body = _route_body("get_account_info")
        assert "broker.get_account(None)" in body
        assert "conn.account_id or None" not in body

    def test_does_not_pass_account_id_as_positional_arg_at_all(self):
        """Guards against a regression that swaps None for conn.account_id in a different,
        equally-wrong form (e.g. conn.account_id directly, no `or None` fallback)."""
        body = _route_body("get_account_info")
        assert "broker.get_account(conn.account_id)" not in body


class TestGetOrderHistoryAccountKeyWiring:
    def test_calls_list_orders_with_none_not_conn_account_id(self):
        body = _route_body("get_order_history")
        assert "broker.list_orders(None, status=status)" in body
        assert "conn.account_id or None" not in body

    def test_does_not_pass_account_id_as_positional_arg_at_all(self):
        body = _route_body("get_order_history")
        assert "broker.list_orders(conn.account_id, status=status)" not in body
