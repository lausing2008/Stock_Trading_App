"""Tests for BUG-YFCALLVOL (2026-08-07) — paper_trading_engine.py's _fetch_live_prices()
looped `tickers.tickers[sym].fast_info` per symbol despite its own docstring claiming "batch" —
`.fast_info` is evaluated lazily per-ticker under yfinance's hood, so this was really N separate
HTTP requests. Called every 5 min during market hours across ~100+ symbols (open positions +
watchlist candidates), this was a real, avoidable amplifier of yfinance rate-limit pressure.
Rewritten to use ONE yf.download() call, matching api/routes.py's already-proven
_fetch_live_bulk() pattern.

paper_trading_engine.py imports directly under this test environment's pytest/conftest.py
setup (yfinance itself is stubbed as MagicMock — this file patches the module's own
`yfinance.download` attribute via monkeypatch, matching test_paper_trading_engine.py's
established direct-import convention for this module).
"""
import pandas as pd

from src.services.paper_trading_engine import _fetch_live_prices


def _multi_symbol_download_df(prices: dict[str, tuple[float, float]]) -> pd.DataFrame:
    """Builds a fake yf.download(group_by="ticker") multi-symbol DataFrame — 2 rows (2d
    window) per symbol, columns are a (symbol, price_type) MultiIndex."""
    frames = {}
    for sym, (prev_close, close) in prices.items():
        frames[sym] = pd.DataFrame({"Close": [prev_close, close]})
    return pd.concat(frames, axis=1)


def _single_symbol_download_df(prev_close: float, close: float) -> pd.DataFrame:
    """A single-symbol yf.download() result has FLAT columns, not a MultiIndex — matches
    _fetch_live_bulk()'s own `len(symbols) > 1` branch distinction exactly."""
    return pd.DataFrame({"Close": [prev_close, close]})


def test_fetches_multiple_symbols_in_one_call(monkeypatch):
    calls = []

    def _fake_download(symbols, **kwargs):
        calls.append(symbols)
        return _multi_symbol_download_df({"AAPL": (100.0, 102.0), "MSFT": (300.0, 305.0)})

    import yfinance as yf
    monkeypatch.setattr(yf, "download", _fake_download)

    result = _fetch_live_prices(["AAPL", "MSFT"])

    assert result == {"AAPL": 102.0, "MSFT": 305.0}
    # The whole point of this fix: ONE call for the whole symbol list, not one per symbol.
    assert len(calls) == 1
    assert set(calls[0]) == {"AAPL", "MSFT"}


def test_single_symbol_uses_the_flat_column_branch(monkeypatch):
    import yfinance as yf
    monkeypatch.setattr(yf, "download", lambda symbols, **kw: _single_symbol_download_df(50.0, 51.5))

    result = _fetch_live_prices(["TSLA"])

    assert result == {"TSLA": 51.5}


def test_rejects_prices_below_the_fifty_cent_floor(monkeypatch):
    import yfinance as yf
    monkeypatch.setattr(
        yf, "download",
        lambda symbols, **kw: _multi_symbol_download_df({"REAL": (10.0, 11.0), "JUNK": (0.02, 0.01)}),
    )

    result = _fetch_live_prices(["REAL", "JUNK"])

    assert result == {"REAL": 11.0}
    assert "JUNK" not in result


def test_empty_symbol_list_returns_empty_dict_without_calling_yfinance(monkeypatch):
    import yfinance as yf
    called = []
    monkeypatch.setattr(yf, "download", lambda *a, **kw: called.append(1))

    result = _fetch_live_prices([])

    assert result == {}
    assert called == []


def test_download_failure_degrades_to_empty_dict_not_a_crash(monkeypatch):
    import yfinance as yf

    def _boom(symbols, **kw):
        raise ConnectionError("yfinance unreachable")

    monkeypatch.setattr(yf, "download", _boom)

    result = _fetch_live_prices(["AAPL"])

    assert result == {}


def test_empty_download_result_returns_empty_dict(monkeypatch):
    import yfinance as yf
    monkeypatch.setattr(yf, "download", lambda symbols, **kw: pd.DataFrame())

    result = _fetch_live_prices(["AAPL"])

    assert result == {}


def test_a_symbol_missing_from_the_download_result_is_silently_skipped(monkeypatch):
    import yfinance as yf
    # Only AAPL comes back — MSFT genuinely absent from the result (e.g. delisted mid-cycle).
    monkeypatch.setattr(
        yf, "download",
        lambda symbols, **kw: _multi_symbol_download_df({"AAPL": (100.0, 101.0)}),
    )

    result = _fetch_live_prices(["AAPL", "MSFT"])

    assert result == {"AAPL": 101.0}
    assert "MSFT" not in result


def test_does_not_call_ticker_dot_fast_info_at_all():
    """Regression guard for the exact bug this fix closes — the old implementation called
    yf.Tickers(...).tickers[sym].fast_info per symbol. Confirm the executable BODY (not the
    docstring, which legitimately names the old pattern while explaining why it changed) no
    longer does."""
    import inspect

    source = inspect.getsource(_fetch_live_prices)
    body = source[source.index('"""', source.index('"""') + 3) + 3:]  # past the closing """

    assert "fast_info" not in body
    assert "yf.Tickers" not in body
    assert "yf.download(" in body
