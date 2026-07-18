"""Tests for T257-BROKER-ORDER-HISTORY.

EtradeBroker.list_orders() calls E*Trade's real orders.json endpoint (the same one
get_order() already uses, without the orderId filter) and parses the OrdersResponse.Order[]
array into BrokerOrder instances, including converting E*Trade's epoch-millisecond
placedTime into an ISO8601 string. Tested directly with requests.get mocked — EtradeBroker
itself is dependency-light (only requests/requests_oauthlib, both real packages, not part of
this repo's conftest.py stub list) so it imports and runs normally under pytest.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.services.broker.etrade_broker import EtradeBroker
from src.services.broker.interface import OrderSide


def _make_broker(sandbox=True):
    return EtradeBroker(
        config={
            "consumer_key": "test_key", "consumer_secret": "test_secret",
            "oauth_token": "test_token", "oauth_token_secret": "test_token_secret",
            "account_id_key": "test_account_key",
        },
        sandbox=sandbox,
    )


def _order_json(order_id="123", symbol="AAPL", action="BUY", qty=10,
                 status="EXECUTED", filled_qty=10, avg_price=150.25, placed_ms=1700000000000):
    return {
        "orderId": order_id,
        "orderStatus": status,
        "OrderDetail": [{
            "placedTime": placed_ms,
            "averageExecutionPrice": avg_price,
            "Instrument": [{
                "Product": {"symbol": symbol},
                "orderAction": action,
                "quantity": qty,
                "filledQuantity": filled_qty,
            }],
        }],
    }


def test_list_orders_parses_multiple_orders():
    broker = _make_broker()
    mock_resp = MagicMock(ok=True)
    mock_resp.json.return_value = {"OrdersResponse": {"Order": [
        _order_json(order_id="1", symbol="AAPL", action="BUY"),
        _order_json(order_id="2", symbol="MSFT", action="SELL", status="OPEN", filled_qty=0, avg_price=0),
    ]}}
    with patch("src.services.broker.etrade_broker.requests.get", return_value=mock_resp) as mock_get:
        orders = broker.list_orders()
    assert len(orders) == 2
    assert orders[0].order_id == "1" and orders[0].symbol == "AAPL" and orders[0].side == OrderSide.BUY
    assert orders[1].order_id == "2" and orders[1].symbol == "MSFT" and orders[1].side == OrderSide.SELL
    mock_get.assert_called_once()


def test_list_orders_maps_etrade_status_to_internal_vocabulary():
    broker = _make_broker()
    mock_resp = MagicMock(ok=True)
    mock_resp.json.return_value = {"OrdersResponse": {"Order": [
        _order_json(status="EXECUTED"),
    ]}}
    with patch("src.services.broker.etrade_broker.requests.get", return_value=mock_resp):
        orders = broker.list_orders()
    assert orders[0].status == "filled"  # EXECUTED -> filled, not the raw E*Trade string


def test_list_orders_converts_placed_time_from_epoch_millis_to_iso8601():
    broker = _make_broker()
    mock_resp = MagicMock(ok=True)
    mock_resp.json.return_value = {"OrdersResponse": {"Order": [
        _order_json(placed_ms=1700000000000),  # 2023-11-14T22:13:20+00:00
    ]}}
    with patch("src.services.broker.etrade_broker.requests.get", return_value=mock_resp):
        orders = broker.list_orders()
    assert orders[0].placed_at is not None
    assert orders[0].placed_at.startswith("2023-11-14")


def test_list_orders_missing_placed_time_does_not_crash():
    """A malformed/missing placedTime must degrade to None, not raise."""
    broker = _make_broker()
    order_no_time = _order_json()
    del order_no_time["OrderDetail"][0]["placedTime"]
    mock_resp = MagicMock(ok=True)
    mock_resp.json.return_value = {"OrdersResponse": {"Order": [order_no_time]}}
    with patch("src.services.broker.etrade_broker.requests.get", return_value=mock_resp):
        orders = broker.list_orders()
    assert orders[0].placed_at is None


def test_list_orders_status_filter_maps_to_etrade_param():
    """status="open" must translate to E*Trade's own "OPEN" param — a literal pass-through
    of our internal vocabulary would silently return zero results against the real API."""
    broker = _make_broker()
    mock_resp = MagicMock(ok=True)
    mock_resp.json.return_value = {"OrdersResponse": {"Order": []}}
    with patch("src.services.broker.etrade_broker.requests.get", return_value=mock_resp) as mock_get:
        broker.list_orders(status="open")
    _, kwargs = mock_get.call_args
    assert kwargs["params"]["status"] == "OPEN"


def test_list_orders_all_status_omits_the_status_param():
    """status="all" must NOT send a status filter at all — sending an invalid/unmapped
    value to E*Trade's API could silently return zero results instead of everything."""
    broker = _make_broker()
    mock_resp = MagicMock(ok=True)
    mock_resp.json.return_value = {"OrdersResponse": {"Order": []}}
    with patch("src.services.broker.etrade_broker.requests.get", return_value=mock_resp) as mock_get:
        broker.list_orders(status="all")
    _, kwargs = mock_get.call_args
    assert "status" not in kwargs["params"]


def test_list_orders_raises_runtimeerror_on_http_failure():
    broker = _make_broker()
    mock_resp = MagicMock(ok=False, status_code=500, text="Internal Server Error")
    with patch("src.services.broker.etrade_broker.requests.get", return_value=mock_resp):
        with pytest.raises(RuntimeError):
            broker.list_orders()


def test_list_orders_empty_response_returns_empty_list_not_none():
    broker = _make_broker()
    mock_resp = MagicMock(ok=True)
    mock_resp.json.return_value = {"OrdersResponse": {}}
    with patch("src.services.broker.etrade_broker.requests.get", return_value=mock_resp):
        orders = broker.list_orders()
    assert orders == []


def test_manual_broker_does_not_override_list_orders_and_raises_not_implemented():
    """ManualBroker (fidelity_manual) has no real API at all — must inherit the base
    interface's NotImplementedError rather than silently returning an empty list, which
    would look identical to 'authorized but genuinely zero orders' to the API caller."""
    from src.services.broker.manual_broker import ManualBroker
    broker = ManualBroker(config={})
    with pytest.raises(NotImplementedError):
        broker.list_orders()
