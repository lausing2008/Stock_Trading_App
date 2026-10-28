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
    # earlier session is returned as indicator warm-up, marked out of the visible range
    assert len(out["bars"]) == 4 and out["visible_from"] == 2
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
    # one-hour maintenance break, then the next session (opens 18:00 ET) runs across midnight UTC
    cur = list(pd.date_range("2026-10-26 22:00", "2026-10-27 03:00", freq="5min", tz="UTC"))
    assert _last_session_start(prev + cur, "ES=F") == len(prev)


def test_futures_data_hole_mid_session_does_not_cut_the_session():
    # Real 2026-10-27 shape: Yahoo had no ES=F bars from 02:25 to 04:05 UTC.
    from src.api.routes import _last_session_start
    prev = list(pd.date_range("2026-10-26 13:00", "2026-10-26 20:55", freq="5min", tz="UTC"))
    a = list(pd.date_range("2026-10-26 22:00", "2026-10-27 02:25", freq="5min", tz="UTC"))
    b = list(pd.date_range("2026-10-27 04:05", "2026-10-27 05:10", freq="5min", tz="UTC"))
    assert _last_session_start(prev + a + b, "ES=F") == len(prev)


def test_cash_index_keeps_the_last_exchange_date():
    from src.api.routes import _last_session_start
    fri = list(pd.date_range("2026-10-23 09:30", "2026-10-23 16:00", freq="5min", tz="America/New_York"))
    mon = list(pd.date_range("2026-10-26 09:30", "2026-10-26 16:00", freq="5min", tz="America/New_York"))
    assert _last_session_start(fri + mon, "^GSPC") == len(fri)


def test_bitcoin_is_the_last_24h():
    from src.api.routes import _last_session_start
    stamps = list(pd.date_range("2026-10-22", "2026-10-26 12:00", freq="5min", tz="UTC"))
    start = _last_session_start(stamps, "BTC-USD")
    assert stamps[-1] - stamps[start] < pd.Timedelta(hours=24)
    assert stamps[-1] - stamps[start] >= pd.Timedelta(hours=23, minutes=55)


def test_warmup_is_capped_and_visible_range_is_calendar_based():
    from src.api.routes import _INDEX_WARMUP_BARS
    stamps = pd.date_range("2016-10-26", "2026-10-26", freq="B", tz="UTC")
    df = pd.DataFrame({"Open": 1.0, "High": 2.0, "Low": 0.5, "Close": 1.5}, index=stamps)
    with patch.object(routes.yf, "Ticker", return_value=_ticker_with_history(df)), \
         patch.object(routes, "_get_redis", return_value=_NoRedis()):
        out = market_index_detail("^GSPC", range="1y")
    assert out["visible_from"] == _INDEX_WARMUP_BARS
    first_visible = pd.Timestamp(out["bars"][out["visible_from"]]["t"], unit="s", tz="UTC")
    assert pd.Timestamp("2025-10-26", tz="UTC") < first_visible <= pd.Timestamp("2025-10-28", tz="UTC")


def test_five_day_counts_futures_trade_dates():
    from src.api.routes import _index_visible_start
    # 7 futures sessions, each 22:00 UTC -> 21:00 UTC next day, 1h break between
    stamps = []
    for d in pd.date_range("2026-10-13", periods=7, freq="D", tz="UTC"):
        stamps += list(pd.date_range(d + pd.Timedelta(hours=22), d + pd.Timedelta(hours=45), freq="1h"))
    start = _index_visible_start(stamps, "ES=F", "5d")
    assert stamps[start] == pd.Timestamp("2026-10-15 22:00", tz="UTC")


def test_futures_change_uses_the_front_contract_across_a_roll():
    # Real 2026-10-27: BZ=F rolled Dec -> Jan. The continuous previous close (115.81) is the Dec
    # contract's; the Jan contract closed 110.74, so the day's real move was +0.56%, not -3.85%.
    def ticker(symbol):
        if symbol == "BZ=F":
            return SimpleNamespace(info={"underlyingSymbol": "BZF27.NYM"},
                                   fast_info=SimpleNamespace(last_price=111.36, previous_close=109.71,
                                                             regular_market_previous_close=115.81))
        assert symbol == "BZF27.NYM"
        return SimpleNamespace(fast_info=SimpleNamespace(last_price=111.36, previous_close=109.41,
                                                         regular_market_previous_close=110.74))
    with patch.object(routes.yf, "Ticker", side_effect=ticker), \
         patch.object(routes, "_get_redis", return_value=_NoRedis()):
        out = _fetch_index("Brent Crude", "BZ=F", "US", "macro")
    assert out["change_pct"] == 0.56
    assert out["price"] == 111.36


def test_front_contract_falls_back_to_continuous_symbol():
    from src.api.routes import _front_contract
    with patch.object(routes.yf, "Ticker", return_value=SimpleNamespace(info={})), \
         patch.object(routes, "_get_redis", return_value=_NoRedis()):
        assert _front_contract("CL=F") == "CL=F"
    assert _front_contract("^GSPC") == "^GSPC"  # not a future: no lookup at all


def test_oil_is_on_the_dashboard():
    tickers = {row[1]: row[3] for row in _INDICES}
    assert tickers["CL=F"] == "macro" and tickers["BZ=F"] == "macro"
