"""Regression test for T247-RANKINGENGINE-HSI-SILENT.

_etf_20d_return()'s yfinance fallback path (used exclusively for ^HSI, since it isn't
DB-seeded) previously returned None on any failure with zero log line — collapsing every HK
stock's relative-strength score to a flat neutral 50.0 (via _rs_score()'s etf_ret=None
branch) for the whole cache window, with no signal the HSI benchmark fetch actually failed.
"""
from unittest.mock import MagicMock

import pandas as pd

import src.api.routes as routes_mod
from src.api.routes import _etf_20d_return


def _clear_cache(ticker="^HSI"):
    routes_mod._ETF_CACHE.pop(ticker, None)


def test_successful_fetch_returns_a_return_no_log(monkeypatch):
    _clear_cache()
    fake_hist = pd.DataFrame({"Close": [100.0 + i for i in range(30)]})
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = fake_hist
    monkeypatch.setattr(routes_mod, "yf", MagicMock(Ticker=lambda t: fake_ticker))
    monkeypatch.setattr(routes_mod, "_HAS_YF", True)
    routes_mod.log.warning.reset_mock()

    result = _etf_20d_return("^HSI")
    assert result is not None
    routes_mod.log.warning.assert_not_called()


def test_yfinance_not_installed_logs_a_warning(monkeypatch):
    _clear_cache()
    monkeypatch.setattr(routes_mod, "_HAS_YF", False)
    routes_mod.log.warning.reset_mock()

    result = _etf_20d_return("^HSI")
    assert result is None
    routes_mod.log.warning.assert_called_once()
    args, kwargs = routes_mod.log.warning.call_args
    assert args[0] == "ranking.etf_return_fetch_failed"
    assert kwargs.get("ticker") == "^HSI"


def test_insufficient_history_logs_a_warning(monkeypatch):
    _clear_cache()
    fake_ticker = MagicMock()
    fake_ticker.history.return_value = pd.DataFrame({"Close": [100.0] * 5})  # <21 bars
    monkeypatch.setattr(routes_mod, "yf", MagicMock(Ticker=lambda t: fake_ticker))
    monkeypatch.setattr(routes_mod, "_HAS_YF", True)
    routes_mod.log.warning.reset_mock()

    result = _etf_20d_return("^HSI")
    assert result is None
    routes_mod.log.warning.assert_called_once()
    args, kwargs = routes_mod.log.warning.call_args
    assert args[0] == "ranking.etf_return_fetch_failed"


def test_yfinance_exception_logs_a_warning(monkeypatch):
    _clear_cache()
    def _raise(*a, **k):
        raise ConnectionError("rate limited")
    monkeypatch.setattr(routes_mod, "yf", MagicMock(Ticker=_raise))
    monkeypatch.setattr(routes_mod, "_HAS_YF", True)
    routes_mod.log.warning.reset_mock()

    result = _etf_20d_return("^HSI")
    assert result is None
    routes_mod.log.warning.assert_called_once()
    args, kwargs = routes_mod.log.warning.call_args
    assert args[0] == "ranking.etf_return_fetch_failed"
    assert "rate limited" in kwargs.get("error", "")
