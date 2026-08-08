"""Tests for T230-DATA-BROKERQUOTE — EtradeBroker.get_quote() calls E*Trade's real
/v1/market/quote/{symbols}.json endpoint on the SAME already-authenticated OAuth session used
for orders/accounts, parsing QuoteResponse.QuoteData[].All into BrokerQuote instances.

IMPORTANT CAVEAT (unlike test_broker_order_history.py's own fixture, which was corrected
against a REAL captured E*Trade sandbox response after shipping with wrong field names): this
file's fixture is built from E*Trade's PUBLISHED API documentation, not a live sandbox call —
no live E*Trade sandbox access was available while writing this. Per this repo's own standing
lesson (test_broker_order_history.py's BUG-ETRADEORDERFIELDS comment), a hand-authored fixture
that happens to match the implementation being tested proves internal consistency, NOT
correctness against the real API. Treat get_quote() as unverified against a real E*Trade
response until it's actually exercised against a live sandbox call.

EtradeBroker itself is dependency-light (only requests/requests_oauthlib, both real packages,
not part of this repo's conftest.py stub list) so it imports and runs normally under pytest.
"""
from unittest.mock import MagicMock, patch

import pytest

from src.services.broker.etrade_broker import EtradeBroker


def _make_broker(sandbox=True):
    return EtradeBroker(
        config={
            "consumer_key": "test_key", "consumer_secret": "test_secret",
            "oauth_token": "test_token", "oauth_token_secret": "test_token_secret",
            "account_id_key": "test_account_key",
        },
        sandbox=sandbox,
    )


def _quote_json(symbol="AAPL", last=150.25, bid=150.20, ask=150.30, prev_close=148.00, volume=1234567):
    """Shape per E*Trade's PUBLISHED docs (QuoteResponse.QuoteData[].{Product, All}) — see
    this file's own module docstring for why this is NOT confirmed against a live response."""
    return {
        "Product": {"symbol": symbol},
        "All": {
            "lastTrade": last, "bid": bid, "ask": ask,
            "previousClose": prev_close, "totalVolume": volume,
        },
    }


def test_get_quote_parses_a_single_symbol():
    broker = _make_broker()
    mock_resp = MagicMock(ok=True)
    mock_resp.json.return_value = {"QuoteResponse": {"QuoteData": [_quote_json(symbol="AAPL")]}}
    with patch("src.services.broker.etrade_broker.requests.get", return_value=mock_resp) as mock_get:
        quotes = broker.get_quote(["AAPL"])
    assert len(quotes) == 1
    assert quotes[0].symbol == "AAPL"
    assert quotes[0].last_price == 150.25
    assert quotes[0].bid == 150.20
    assert quotes[0].ask == 150.30
    assert quotes[0].prev_close == 148.00
    assert quotes[0].volume == 1234567
    mock_get.assert_called_once()


def test_get_quote_parses_multiple_symbols_in_one_call():
    broker = _make_broker()
    mock_resp = MagicMock(ok=True)
    mock_resp.json.return_value = {"QuoteResponse": {"QuoteData": [
        _quote_json(symbol="AAPL", last=150.25),
        _quote_json(symbol="MSFT", last=305.10),
    ]}}
    with patch("src.services.broker.etrade_broker.requests.get", return_value=mock_resp) as mock_get:
        quotes = broker.get_quote(["AAPL", "MSFT"])
    assert {q.symbol for q in quotes} == {"AAPL", "MSFT"}
    assert mock_get.call_count == 1  # one call for both symbols, not one per symbol


def test_get_quote_batches_calls_past_the_25_symbol_limit():
    """E*Trade caps /v1/market/quote at 25 symbols per call — a caller passing 30 symbols
    must transparently get 2 batched calls, not a single oversized request or a silent
    truncation to only the first 25."""
    broker = _make_broker()
    symbols = [f"SYM{i}" for i in range(30)]
    mock_resp = MagicMock(ok=True)
    mock_resp.json.return_value = {"QuoteResponse": {"QuoteData": [_quote_json(symbol="SYM0")]}}
    with patch("src.services.broker.etrade_broker.requests.get", return_value=mock_resp) as mock_get:
        broker.get_quote(symbols)
    assert mock_get.call_count == 2
    first_url = mock_get.call_args_list[0].args[0]
    second_url = mock_get.call_args_list[1].args[0]
    assert first_url.count(",") == 24   # 25 symbols joined by comma = 24 commas
    assert second_url.count(",") == 4   # remaining 5 symbols = 4 commas


def test_get_quote_a_symbol_with_no_all_block_degrades_to_none_fields_not_a_crash():
    """A bad ticker or delisted symbol returns a Messages block instead of All — must degrade
    to an all-None quote for that symbol, not raise or silently drop it from the result."""
    broker = _make_broker()
    mock_resp = MagicMock(ok=True)
    mock_resp.json.return_value = {"QuoteResponse": {"QuoteData": [
        {"Product": {"symbol": "BADTICKER"}, "Messages": {"Message": [{"description": "No quote available"}]}},
    ]}}
    with patch("src.services.broker.etrade_broker.requests.get", return_value=mock_resp):
        quotes = broker.get_quote(["BADTICKER"])
    assert len(quotes) == 1
    assert quotes[0].symbol == "BADTICKER"
    assert quotes[0].last_price is None


def test_get_quote_missing_optional_fields_degrade_to_none():
    broker = _make_broker()
    mock_resp = MagicMock(ok=True)
    mock_resp.json.return_value = {"QuoteResponse": {"QuoteData": [
        {"Product": {"symbol": "AAPL"}, "All": {"lastTrade": 150.25}},
    ]}}
    with patch("src.services.broker.etrade_broker.requests.get", return_value=mock_resp):
        quotes = broker.get_quote(["AAPL"])
    assert quotes[0].last_price == 150.25
    assert quotes[0].bid is None
    assert quotes[0].ask is None
    assert quotes[0].prev_close is None
    assert quotes[0].volume is None


def test_get_quote_raises_runtimeerror_on_http_failure():
    broker = _make_broker()
    mock_resp = MagicMock(ok=False, status_code=500, text="Internal Server Error")
    with patch("src.services.broker.etrade_broker.requests.get", return_value=mock_resp):
        with pytest.raises(RuntimeError):
            broker.get_quote(["AAPL"])


def test_get_quote_empty_response_returns_empty_list_not_none():
    broker = _make_broker()
    mock_resp = MagicMock(ok=True)
    mock_resp.json.return_value = {"QuoteResponse": {}}
    with patch("src.services.broker.etrade_broker.requests.get", return_value=mock_resp):
        quotes = broker.get_quote(["AAPL"])
    assert quotes == []


def test_manual_broker_does_not_override_get_quote_and_raises_not_implemented():
    """ManualBroker (fidelity_manual) has no real API at all — must inherit the base
    interface's NotImplementedError, matching list_orders()'s established convention exactly."""
    from src.services.broker.manual_broker import ManualBroker
    broker = ManualBroker(config={})
    with pytest.raises(NotImplementedError):
        broker.get_quote(["AAPL"])
