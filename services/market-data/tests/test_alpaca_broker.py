"""Tests for TIER84-BROKER-ALPACA's AlpacaBroker adapter.

AlpacaBroker only depends on `requests` (a real, installed package — not part of this repo's
conftest.py stub list), so it imports and runs normally under pytest, matching
test_broker_order_history.py's own established technique for EtradeBroker. Fixtures are built
directly from Alpaca's own documented, stable v2 Trading/Market-Data API response schemas
(https://docs.alpaca.markets/reference) rather than hand-idealized guesses — matching this
repo's own standing lesson (see CLAUDE.md's CAPE-feature entry) that a fixture matching a buggy
implementation's own assumptions can silently certify the bug as correct.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.services.broker.alpaca_broker import AlpacaBroker
from src.services.broker.interface import OrderSide, OrderType


def _make_broker(paper=True):
    return AlpacaBroker(config={"key_id": "test_key", "secret_key": "test_secret"}, paper=paper)


def _account_json(cash="10000.00", equity="15000.00", buying_power="20000.00",
                   daytrading_bp="40000.00", account_number="PA123ABC456", acct_id="abc-123"):
    """Matches Alpaca's real GET /v2/account response shape."""
    return {
        "id": acct_id,
        "account_number": account_number,
        "cash": cash,
        "equity": equity,
        "buying_power": buying_power,
        "daytrading_buying_power": daytrading_bp,
    }


def _position_json(symbol="AAPL", qty="10", avg_entry_price="150.00",
                    market_value="1600.00", unrealized_pl="100.00", unrealized_plpc="0.0667"):
    """Matches Alpaca's real GET /v2/positions response shape (a list of these)."""
    return {
        "symbol": symbol, "qty": qty, "avg_entry_price": avg_entry_price,
        "market_value": market_value, "unrealized_pl": unrealized_pl,
        "unrealized_plpc": unrealized_plpc,
    }


def _order_json(order_id="61e69015-8549-4bfd-b9c3-01e75843f47d", symbol="AAPL", side="buy",
                 order_type="market", status="filled", qty="10", filled_qty="10",
                 filled_avg_price="150.25", submitted_at="2026-08-18T14:31:00.123456Z",
                 limit_price=None, stop_price=None):
    """Matches Alpaca's real order object shape (returned by POST/GET /v2/orders)."""
    o = {
        "id": order_id, "symbol": symbol, "side": side, "type": order_type,
        "status": status, "qty": qty, "filled_qty": filled_qty,
        "filled_avg_price": filled_avg_price, "submitted_at": submitted_at,
    }
    if limit_price is not None:
        o["limit_price"] = limit_price
    if stop_price is not None:
        o["stop_price"] = stop_price
    return o


def _resp(status_code=200, json_data=None, text=""):
    r = MagicMock()
    r.ok = 200 <= status_code < 300
    r.status_code = status_code
    r.json.return_value = json_data if json_data is not None else {}
    r.text = text
    return r


# ── get_account() ────────────────────────────────────────────────────────────

def test_get_account_parses_real_balance_fields():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.side_effect = [
            _resp(200, _account_json()),
            _resp(200, [_position_json()]),
        ]
        acct = broker.get_account()
    assert acct.cash_available == 10000.00
    assert acct.equity == 15000.00
    assert acct.buying_power == 20000.00
    assert acct.day_trading_buying_power == 40000.00
    assert acct.account_id == "PA123ABC456"
    assert len(acct.open_positions) == 1
    assert acct.open_positions[0].symbol == "AAPL"


def test_get_account_broker_type_reflects_paper_vs_live():
    for paper, expected in ((True, "alpaca_paper"), (False, "alpaca")):
        broker = _make_broker(paper=paper)
        with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
            mock_get.side_effect = [_resp(200, _account_json()), _resp(200, [])]
            acct = broker.get_account()
        assert acct.broker_type == expected


def test_get_account_uses_correct_base_url_for_paper_vs_live():
    broker_paper = _make_broker(paper=True)
    broker_live = _make_broker(paper=False)
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.side_effect = [_resp(200, _account_json()), _resp(200, [])]
        broker_paper.get_account()
    assert "paper-api.alpaca.markets" in mock_get.call_args_list[0].args[0]

    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get2:
        mock_get2.side_effect = [_resp(200, _account_json()), _resp(200, [])]
        broker_live.get_account()
    assert mock_get2.call_args_list[0].args[0] == "https://api.alpaca.markets/v2/account"


def test_get_account_sends_key_id_and_secret_key_headers():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.side_effect = [_resp(200, _account_json()), _resp(200, [])]
        broker.get_account()
    headers = mock_get.call_args_list[0].kwargs["headers"]
    assert headers["APCA-API-KEY-ID"] == "test_key"
    assert headers["APCA-API-SECRET-KEY"] == "test_secret"


def test_get_account_raises_runtime_error_on_http_failure():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.return_value = _resp(401, text='{"message": "unauthorized"}')
        with pytest.raises(RuntimeError):
            broker.get_account()


def test_positions_fetch_failure_degrades_to_empty_list_not_a_crash():
    """Matches EtradeBroker._get_positions_raw()'s own established fail-soft convention — a
    failed positions fetch must not abort the whole account summary."""
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.side_effect = [_resp(200, _account_json()), _resp(500, text="error")]
        acct = broker.get_account()
    assert acct.open_positions == []


# ── place_order() ─────────────────────────────────────────────────────────────

def test_place_order_builds_correct_payload_and_parses_response():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.post") as mock_post:
        mock_post.return_value = _resp(200, _order_json())
        order = broker.place_order("AAPL", 10, OrderSide.BUY, OrderType.MARKET)
    payload = mock_post.call_args.kwargs["json"]
    assert payload["symbol"] == "AAPL"
    assert payload["qty"] == "10"
    assert payload["side"] == "buy"
    assert payload["type"] == "market"
    assert order.order_id == "61e69015-8549-4bfd-b9c3-01e75843f47d"
    assert order.status == "filled"
    assert order.filled_avg_price == 150.25


def test_place_order_sell_side_maps_correctly():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.post") as mock_post:
        mock_post.return_value = _resp(200, _order_json(side="sell"))
        broker.place_order("AAPL", 10, OrderSide.SELL, OrderType.MARKET)
    assert mock_post.call_args.kwargs["json"]["side"] == "sell"


def test_place_order_limit_includes_limit_price_in_payload():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.post") as mock_post:
        mock_post.return_value = _resp(200, _order_json(order_type="limit", limit_price="145.00"))
        broker.place_order("AAPL", 10, OrderSide.BUY, OrderType.LIMIT, limit_price=145.00)
    assert mock_post.call_args.kwargs["json"]["limit_price"] == "145.0"


def test_place_order_raises_runtime_error_on_http_failure():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.post") as mock_post:
        mock_post.return_value = _resp(422, text='{"message": "insufficient buying power"}')
        with pytest.raises(RuntimeError):
            broker.place_order("AAPL", 10, OrderSide.BUY)


# ── get_order() / list_orders() ───────────────────────────────────────────────

def test_get_order_parses_a_single_order():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.return_value = _resp(200, _order_json())
        order = broker.get_order("61e69015-8549-4bfd-b9c3-01e75843f47d")
    assert order.symbol == "AAPL"
    assert order.qty == 10.0


def test_list_orders_parses_multiple_orders():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.return_value = _resp(200, [_order_json(order_id="1"), _order_json(order_id="2", symbol="MSFT")])
        orders = broker.list_orders(status="all")
    assert len(orders) == 2
    assert orders[1].symbol == "MSFT"


def test_list_orders_status_open_maps_to_alpacas_open_query_param():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.return_value = _resp(200, [])
        broker.list_orders(status="open")
    assert mock_get.call_args.kwargs["params"]["status"] == "open"


def test_list_orders_status_filled_maps_to_alpacas_closed_query_param():
    """Alpaca's own API only has open/closed/all as query values (no separate "filled" concept
    at the query-param level) — matches this file's own _QUERY_STATUS_MAP mapping."""
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.return_value = _resp(200, [])
        broker.list_orders(status="filled")
    assert mock_get.call_args.kwargs["params"]["status"] == "closed"


def test_list_orders_status_all_sends_alpacas_own_literal_all_query_value():
    """Unlike EtradeBroker.list_orders() (whose "all" means omit the status param entirely,
    since E*Trade has no explicit "all" query value), Alpaca's /v2/orders endpoint has a real,
    documented status=all query value — so this correctly sends it rather than omitting it."""
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.return_value = _resp(200, [])
        broker.list_orders(status="all")
    assert mock_get.call_args.kwargs["params"]["status"] == "all"


def test_various_alpaca_order_statuses_map_to_this_apps_5_state_vocabulary():
    broker = _make_broker()
    cases = [
        ("new", "pending"), ("accepted", "pending"), ("partially_filled", "partially_filled"),
        ("filled", "filled"), ("done_for_day", "filled"),
        ("canceled", "cancelled"), ("expired", "cancelled"),
        ("rejected", "rejected"), ("suspended", "rejected"),
    ]
    for alpaca_status, expected in cases:
        with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
            mock_get.return_value = _resp(200, _order_json(status=alpaca_status))
            order = broker.get_order("x")
        assert order.status == expected, f"{alpaca_status} should map to {expected}, got {order.status}"


def test_order_side_buy_open_style_actions_still_parse_correctly():
    """Alpaca's own equity orders always use a plain 'buy'/'sell' side (no BUY_OPEN/BUY_CLOSE
    options-style variants the way E*Trade has) — this test documents that this adapter's
    side-mapping is a simple exact match, not the startswith() logic EtradeBroker needs."""
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.return_value = _resp(200, _order_json(side="buy"))
        order = broker.get_order("x")
    assert order.side == OrderSide.BUY


def test_get_order_raises_runtime_error_on_http_failure():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.return_value = _resp(404, text='{"message": "order not found"}')
        with pytest.raises(RuntimeError):
            broker.get_order("nonexistent")


# ── cancel_order() ────────────────────────────────────────────────────────────

def test_cancel_order_returns_true_on_204():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.delete") as mock_delete:
        r = MagicMock()
        r.status_code = 204
        mock_delete.return_value = r
        assert broker.cancel_order("some-id") is True


def test_cancel_order_returns_false_on_non_204():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.delete") as mock_delete:
        r = MagicMock()
        r.status_code = 422
        mock_delete.return_value = r
        assert broker.cancel_order("some-id") is False


# ── get_quote() ────────────────────────────────────────────────────────────────

def test_get_quote_uses_the_separate_data_api_host_not_the_trading_host():
    """Alpaca splits trading (paper-api/api.alpaca.markets) and market-data
    (data.alpaca.markets) onto different hosts — this is the one real, documented
    architecture difference from E*Trade's single-host design."""
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.return_value = _resp(200, {"quotes": {}})
        broker.get_quote(["AAPL"])
    assert "data.alpaca.markets" in mock_get.call_args.args[0]


def test_get_quote_parses_bid_ask_and_computes_midpoint_as_last_price():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.return_value = _resp(200, {"quotes": {"AAPL": {"bp": 150.00, "ap": 150.10}}})
        quotes = broker.get_quote(["AAPL"])
    assert quotes[0].bid == 150.00
    assert quotes[0].ask == 150.10
    assert quotes[0].last_price == pytest.approx(150.05)


def test_get_quote_missing_symbol_degrades_to_all_none_not_a_crash():
    """A symbol Alpaca can't quote must degrade to an all-None quote (matching
    EtradeBroker.get_quote()'s own per-item fail-soft convention) rather than raising or
    silently dropping the symbol from the results list."""
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.return_value = _resp(200, {"quotes": {}})
        quotes = broker.get_quote(["ZZZZ"])
    assert len(quotes) == 1
    assert quotes[0].symbol == "ZZZZ"
    assert quotes[0].last_price is None


def test_get_quote_one_sided_quote_does_not_fabricate_a_midpoint():
    """A quote with only a bid or only an ask must not fabricate a midpoint last_price from a
    single one-sided value."""
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.return_value = _resp(200, {"quotes": {"AAPL": {"bp": 150.00}}})
        quotes = broker.get_quote(["AAPL"])
    assert quotes[0].last_price is None


def test_get_quote_empty_symbol_list_returns_empty_without_a_call():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        quotes = broker.get_quote([])
    assert quotes == []
    mock_get.assert_not_called()


def test_get_quote_raises_runtime_error_on_http_failure():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.return_value = _resp(429, text='{"message": "rate limited"}')
        with pytest.raises(RuntimeError):
            broker.get_quote(["AAPL"])


# ── is_market_open() ──────────────────────────────────────────────────────────

def test_is_market_open_reads_alpacas_clock_endpoint():
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.return_value = _resp(200, {"is_open": True})
        assert broker.is_market_open() is True
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.return_value = _resp(200, {"is_open": False})
        assert broker.is_market_open() is False


def test_is_market_open_fails_open_to_fixed_hours_on_a_clock_endpoint_error():
    """is_market_open() has no documented error contract for callers to handle (unlike every
    other method here, which raises RuntimeError) — must degrade to the same fixed-hours
    approximation every other broker adapter uses, never raise."""
    broker = _make_broker()
    with patch("src.services.broker.alpaca_broker.requests.get") as mock_get:
        mock_get.side_effect = ConnectionError("network down")
        result = broker.is_market_open()  # must not raise
    assert isinstance(result, bool)
