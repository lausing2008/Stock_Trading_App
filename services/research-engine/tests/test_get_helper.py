"""Regression test for T247-RESEARCHENGINE-GET-SILENT.

_get() previously only logged on network/exception failures, not on non-200 HTTP responses
(e.g. the "python-jose missing from container" 401 pattern already documented multiple times
in this repo's CLAUDE.md) — a non-200 fell through to `return None` with zero log line,
unlike the exception branch. Every research report silently lost that upstream's data with
nothing in the logs to grep for.
"""
import asyncio
from unittest.mock import AsyncMock, MagicMock

import src.api.routes as routes_mod
from src.api.routes import _get


def test_200_response_returns_json_no_log():
    client = MagicMock()
    client.get = AsyncMock(return_value=MagicMock(status_code=200, json=lambda: {"a": 1}))
    routes_mod.log.warning.reset_mock()

    result = asyncio.run(_get(client, "http://signal-engine:8005/signals/AAPL"))
    assert result == {"a": 1}
    routes_mod.log.warning.assert_not_called()


def test_401_response_logs_a_warning():
    """The exact bug scenario: a 401 (e.g. jose-missing-from-container) previously returned
    None with zero log trace."""
    client = MagicMock()
    client.get = AsyncMock(return_value=MagicMock(status_code=401))
    routes_mod.log.warning.reset_mock()

    result = asyncio.run(_get(client, "http://signal-engine:8005/signals/AAPL"))
    assert result is None
    routes_mod.log.warning.assert_called_once()
    args, kwargs = routes_mod.log.warning.call_args
    assert args[0] == "upstream.get.non_200"
    assert kwargs.get("status") == 401


def test_500_response_logs_a_warning():
    client = MagicMock()
    client.get = AsyncMock(return_value=MagicMock(status_code=500))
    routes_mod.log.warning.reset_mock()

    result = asyncio.run(_get(client, "http://ranking-engine:8004/rankings/AAPL"))
    assert result is None
    routes_mod.log.warning.assert_called_once()
    args, kwargs = routes_mod.log.warning.call_args
    assert args[0] == "upstream.get.non_200"
    assert kwargs.get("status") == 500


def test_exception_still_logs_a_warning_unaffected_by_this_fix():
    """The pre-existing exception-path logging must be unaffected by this fix."""
    client = MagicMock()
    client.get = AsyncMock(side_effect=ConnectionError("connection refused"))
    routes_mod.log.warning.reset_mock()

    result = asyncio.run(_get(client, "http://event-intelligence:8010/events/AAPL"))
    assert result is None
    routes_mod.log.warning.assert_called_once()
    args, kwargs = routes_mod.log.warning.call_args
    assert args[0] == "upstream.get.failed"
