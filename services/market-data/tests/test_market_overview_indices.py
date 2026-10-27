"""Market overview: US index futures, Russell 2000 and macro gauges (2026-10-26).

The change baseline is `regular_market_previous_close` (what Yahoo displays). fast_info's
`previous_close` for futures points one session too far back — on 2026-10-26 ES=F read +0.67%
against it vs the real +0.13% — so it may only be the fallback, for tickers like BTC-USD
whose regular-session value is NaN.
"""
from types import SimpleNamespace
from unittest.mock import patch

from src.api import routes
from src.api.routes import _INDICES, _fetch_index


def _quote(last, prev, regprev):
    return SimpleNamespace(fast_info=SimpleNamespace(
        last_price=last, previous_close=prev, regular_market_previous_close=regprev))


def test_change_uses_regular_market_previous_close():
    with patch.object(routes.yf, "Ticker", return_value=_quote(7722.25, 7671.0, 7714.5)):
        out = _fetch_index("S&P Futures", "ES=F", "US", "futures")
    assert out["change_pct"] == 0.1
    assert out["change"] == 7.75
    assert out["group"] == "futures"


def test_nan_regular_close_falls_back_to_previous_close():
    with patch.object(routes.yf, "Ticker", return_value=_quote(79510.0, 80000.0, float("nan"))):
        out = _fetch_index("Bitcoin", "BTC-USD", "US", "macro")
    assert out["change"] == -490.0
    assert out["change_pct"] == -0.61


def test_failure_returns_nulls_not_zero():
    with patch.object(routes.yf, "Ticker", side_effect=RuntimeError("yahoo down")):
        out = _fetch_index("Gold", "GC=F", "US", "macro")
    assert out["price"] is None and out["change"] is None and out["change_pct"] is None


def test_overview_carries_the_requested_tickers():
    tickers = {row[1] for row in _INDICES}
    assert {"ES=F", "YM=F", "NQ=F", "RTY=F", "^RUT", "^TNX", "^VIX", "GC=F", "BTC-USD"} <= tickers
    assert all(row[3] in {"index", "futures", "macro"} for row in _INDICES)


# ── /stocks/market_index/{ticker} detail endpoint ───────────────────────────
import pandas as pd
import pytest
from fastapi import HTTPException

from src.api.routes import market_index_detail


class _NoRedis:
    def get(self, key):
        return None

    def setex(self, *a):
        self.set_called = True


def _ticker_with_history(df):
    fi = SimpleNamespace(last_price=7722.25, previous_close=7671.0, regular_market_previous_close=7714.5,
                         open=7715.0, day_high=7730.0, day_low=7700.0, year_high=7800.0, year_low=5800.0)
    return SimpleNamespace(fast_info=fi, history=lambda **kw: df)


def _bars(stamps):
    idx = pd.DatetimeIndex(pd.to_datetime(stamps, utc=True))
    return pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5}, index=idx)


def test_detail_rejects_untracked_ticker():
    with pytest.raises(HTTPException) as e:
        market_index_detail("AAPL", range="1d")
    assert e.value.status_code == 404


def test_detail_rejects_unknown_range():
    with pytest.raises(HTTPException) as e:
        market_index_detail("ES=F", range="10y")
    assert e.value.status_code == 422


def test_one_day_keeps_only_the_last_session():
    df = _bars(["2026-10-23 14:00", "2026-10-23 14:05", "2026-10-26 14:00", "2026-10-26 14:05"])
    redis = _NoRedis()
    with patch.object(routes.yf, "Ticker", return_value=_ticker_with_history(df)), \
         patch.object(routes, "_get_redis", return_value=redis):
        out = market_index_detail("ES=F", range="1d")
    assert len(out["bars"]) == 2
    assert out["change_pct"] == 0.1 and out["day_high"] == 7730.0 and out["delay_minutes"] == 10
    assert getattr(redis, "set_called", False)


def test_empty_history_is_not_cached():
    redis = _NoRedis()
    with patch.object(routes.yf, "Ticker", return_value=_ticker_with_history(_bars([]))), \
         patch.object(routes, "_get_redis", return_value=redis):
        out = market_index_detail("GC=F", range="1y")
    assert out["bars"] == []
    assert not getattr(redis, "set_called", False)


def test_every_dashboard_ticker_has_detail_info():
    assert {row[1] for row in _INDICES} == set(routes._INDEX_INFO)


def test_futures_session_spanning_midnight_utc_stays_whole():
    from src.api.routes import _last_session_start
    prev = list(pd.date_range("2026-10-26 13:00", "2026-10-26 21:00", freq="5min", tz="UTC"))
    # one-hour maintenance break, then the next session runs across midnight UTC
    cur = list(pd.date_range("2026-10-26 22:00", "2026-10-27 03:00", freq="5min", tz="UTC"))
    assert _last_session_start(prev + cur) == len(prev)


def test_continuous_trading_is_capped_at_24h():
    from src.api.routes import _last_session_start
    stamps = list(pd.date_range("2026-10-22", "2026-10-26 12:00", freq="5min", tz="UTC"))
    start = _last_session_start(stamps)
    assert stamps[-1] - stamps[start] <= pd.Timedelta(hours=24)
    assert stamps[-1] - stamps[start] >= pd.Timedelta(hours=23)
