"""Regression test for T247-DECISIONENGINE-FETCHSIGNAL-MISATTRIBUTION.

_fetch_signal()'s list-handling branch previously fell back to `data[0]` (the first entry of
an unrelated list) when no signal matched the requested symbol, instead of returning None.
Currently unreachable (signal-engine's /signals/{symbol}?style=... never actually returns a
bare list for this query shape), but if that upstream response shape ever changes, this would
silently score an arbitrary, unrelated symbol's signal instead of correctly falling through to
the "no signal" BLOCKED path.

No pytest-asyncio available locally — async behavior is driven directly via asyncio.run()
inside plain sync test functions, matching test_regime.py's/test_aggregator.py's pattern.
"""
import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())

import src.api.core.aggregator as aggregator  # noqa: E402

# _fetch_signal() calls _svc_token(), which real-encodes a JWT via jose.jwt.encode() using
# _settings.jwt_secret — a MagicMock attribute, not a real string, so the real jose encoder
# raises. Stub _svc_token() directly so tests exercise _fetch_signal()'s own logic, not this
# unrelated auth-token-generation path.
aggregator._svc_token = lambda: "test-token"


def _fake_client(status_code, json_body):
    client = MagicMock()
    client.get = AsyncMock(return_value=MagicMock(status_code=status_code, json=lambda: json_body))
    return client


def test_matching_symbol_in_a_list_response_is_returned():
    client = _fake_client(200, [
        {"symbol": "MSFT", "confidence": 50.0},
        {"symbol": "AAPL", "confidence": 80.0},
    ])
    result = asyncio.run(aggregator._fetch_signal(client, "AAPL", "SWING"))
    assert result == {"symbol": "AAPL", "confidence": 80.0}


def test_no_matching_symbol_in_a_list_response_returns_none_not_an_unrelated_signal():
    """The exact bug scenario: a list response with entries for OTHER symbols but not the
    requested one must return None, not silently misattribute the first unrelated entry."""
    client = _fake_client(200, [
        {"symbol": "MSFT", "confidence": 50.0},
        {"symbol": "GOOG", "confidence": 30.0},
    ])
    result = asyncio.run(aggregator._fetch_signal(client, "AAPL", "SWING"))
    assert result is None


def test_empty_list_response_returns_none():
    client = _fake_client(200, [])
    result = asyncio.run(aggregator._fetch_signal(client, "AAPL", "SWING"))
    assert result is None


def test_dict_response_is_returned_directly():
    client = _fake_client(200, {"symbol": "AAPL", "confidence": 80.0})
    result = asyncio.run(aggregator._fetch_signal(client, "AAPL", "SWING"))
    assert result == {"symbol": "AAPL", "confidence": 80.0}


def test_non_200_status_returns_none():
    client = _fake_client(401, {})
    result = asyncio.run(aggregator._fetch_signal(client, "AAPL", "SWING"))
    assert result is None
