"""Tests for hk_connect.py's Eastmoney-based southbound flow fetch (MD-HKCONNECT2).

Covers the symbol-code conversion (pure) and the response-parsing logic in
_fetch_southbound_stock() (mocking httpx.get, since httpx itself is stubbed as a MagicMock by
conftest.py for the whole test suite — real network calls are never made in these tests).
"""
from unittest.mock import MagicMock, patch

from src.services.hk_connect import _symbol_to_eastmoney_code, _fetch_southbound_stock


# ── _symbol_to_eastmoney_code ──────────────────────────────────────────────────

def test_four_digit_hk_code_gets_zero_padded_to_five():
    assert _symbol_to_eastmoney_code("0700.HK") == "00700.HK"


def test_already_five_digit_code_unchanged():
    assert _symbol_to_eastmoney_code("9988.HK") == "09988.HK"


def test_non_hk_symbol_returns_none():
    assert _symbol_to_eastmoney_code("AAPL") is None
    assert _symbol_to_eastmoney_code("AAPL.US") is None


def test_lowercase_hk_suffix_is_handled():
    assert _symbol_to_eastmoney_code("0700.hk") == "00700.HK"


# ── _fetch_southbound_stock ────────────────────────────────────────────────────

def _mock_response(json_body, status_code=200):
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = json_body
    return resp


def test_successful_response_returns_net_buy_and_trade_date():
    body = {
        "result": {
            "data": [{
                "TRADE_DATE": "2026-07-13 00:00:00",
                "ADD_MARKET_CAP": -1410185421.5367,
            }]
        }
    }
    with patch("src.services.hk_connect.httpx.get", return_value=_mock_response(body)):
        result = _fetch_southbound_stock("00700.HK")
    assert result is not None
    assert result["net_buy_hkd"] == -1410185421.5367
    assert result["trade_date"].isoformat() == "2026-07-13"
    assert result["buy_hkd"] is None  # this report can't split gross buy/sell
    assert result["sell_hkd"] is None


def test_non_200_status_returns_none():
    with patch("src.services.hk_connect.httpx.get", return_value=_mock_response({}, status_code=500)):
        assert _fetch_southbound_stock("00700.HK") is None


def test_empty_result_returns_none():
    """Confirmed live behavior for a symbol not in the Stock Connect scheme: Eastmoney
    returns {"result": None, "success": False, ...} rather than an empty data list."""
    body = {"result": None, "success": False, "message": "返回数据为空", "code": 9201}
    with patch("src.services.hk_connect.httpx.get", return_value=_mock_response(body)):
        assert _fetch_southbound_stock("09999999.HK") is None


def test_empty_data_list_returns_none():
    body = {"result": {"data": []}}
    with patch("src.services.hk_connect.httpx.get", return_value=_mock_response(body)):
        assert _fetch_southbound_stock("00700.HK") is None


def test_missing_add_market_cap_field_returns_none():
    body = {"result": {"data": [{"TRADE_DATE": "2026-07-13 00:00:00"}]}}
    with patch("src.services.hk_connect.httpx.get", return_value=_mock_response(body)):
        assert _fetch_southbound_stock("00700.HK") is None


def test_missing_trade_date_field_returns_none():
    body = {"result": {"data": [{"ADD_MARKET_CAP": 123.0}]}}
    with patch("src.services.hk_connect.httpx.get", return_value=_mock_response(body)):
        assert _fetch_southbound_stock("00700.HK") is None


def test_zero_net_buy_is_not_treated_as_missing():
    """A genuine zero net-flow day must be preserved, not coerced to 'no data' — matches this
    session's other None-vs-falsy regression guards (T237-EI2 class)."""
    body = {"result": {"data": [{"TRADE_DATE": "2026-07-13 00:00:00", "ADD_MARKET_CAP": 0.0}]}}
    with patch("src.services.hk_connect.httpx.get", return_value=_mock_response(body)):
        result = _fetch_southbound_stock("00700.HK")
    assert result is not None
    assert result["net_buy_hkd"] == 0.0


def test_request_exception_returns_none_not_raises():
    with patch("src.services.hk_connect.httpx.get", side_effect=Exception("connection refused")):
        assert _fetch_southbound_stock("00700.HK") is None


def test_json_parse_failure_returns_none_not_raises():
    resp = MagicMock()
    resp.status_code = 200
    resp.json.side_effect = ValueError("not json")
    with patch("src.services.hk_connect.httpx.get", return_value=resp):
        assert _fetch_southbound_stock("00700.HK") is None
