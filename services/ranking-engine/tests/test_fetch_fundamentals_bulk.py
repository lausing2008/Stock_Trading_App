"""Regression test for T247-RANKINGENGINE-FUNDAMENTALS-SILENT.

_fetch_fundamentals_bulk() previously swallowed every failure with a bare
`except Exception: pass` (and silently fell through to `return {}` on a non-200 status
without even reaching the except) — zero log line anywhere. A market-data outage or timeout
during a scheduled rankings refresh silently excluded every stock's value/growth K-Score
components for the whole outage window, indistinguishable in logs from normal operation.
"""
from unittest.mock import MagicMock

import src.api.routes as routes_mod
from src.api.routes import _fetch_fundamentals_bulk


def test_successful_fetch_returns_data_no_log(monkeypatch):
    fake_client = MagicMock()
    fake_client.__enter__.return_value.get.return_value = MagicMock(
        status_code=200, json=lambda: {"AAPL": {"trailing_pe": 25.0}},
    )
    monkeypatch.setattr(routes_mod.httpx, "Client", lambda **kw: fake_client)
    routes_mod.log.warning.reset_mock()

    result = _fetch_fundamentals_bulk()
    assert result == {"AAPL": {"trailing_pe": 25.0}}
    routes_mod.log.warning.assert_not_called()


def test_non_200_status_logs_a_warning(monkeypatch):
    """The exact silent gap: a non-200 response fell through to `return {}` without even
    reaching the except block, so nothing was ever logged for this path."""
    fake_client = MagicMock()
    fake_client.__enter__.return_value.get.return_value = MagicMock(status_code=503)
    monkeypatch.setattr(routes_mod.httpx, "Client", lambda **kw: fake_client)
    routes_mod.log.warning.reset_mock()

    result = _fetch_fundamentals_bulk()
    assert result == {}
    routes_mod.log.warning.assert_called_once()
    args, kwargs = routes_mod.log.warning.call_args
    assert args[0] == "ranking.fundamentals_bulk_fetch_failed"
    assert kwargs.get("status") == 503


def test_connection_exception_logs_a_warning(monkeypatch):
    def _raise_client(**kw):
        raise ConnectionError("market-data unreachable")
    monkeypatch.setattr(routes_mod.httpx, "Client", _raise_client)
    routes_mod.log.warning.reset_mock()

    result = _fetch_fundamentals_bulk()
    assert result == {}
    routes_mod.log.warning.assert_called_once()
    args, kwargs = routes_mod.log.warning.call_args
    assert args[0] == "ranking.fundamentals_bulk_fetch_failed"
    assert "market-data unreachable" in kwargs.get("error", "")
