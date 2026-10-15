"""AUD-UW-INTRADAY-TZ: the Unusual Whales bar adapter, driven through the REAL fetch_ohlcv.

THE DEFECT (found 2026-10-14 during the copilot deployment's verification, reproduced by the
independent re-review). Every intraday request raised `TypeError: Invalid comparison between
dtype=datetime64[ns, UTC] and Timestamp`: UW's intraday candles carry UTC `start_time` values,
and the window filter compared them with naive date bounds. The failure happened AFTER the
metered request, so no failure counter saw it, and every 5m/15m/1h fetch fell back to yfinance
silently. Daily candles carry a plain market `date` and were unaffected.

Payload shapes are the ones captured from production on 2026-10-14 (GDX):
  1d:       {"date": "2026-10-14", "market_time": "r", ...}            (no times)
  intraday: {"start_time": "2026-10-14T19:00:00Z", "end_time": ..., "market_time": "r", ...}

Unlike the source-text instrumentation tests, these assert the BARS RETURNED and the COUNTERS
WRITTEN, with HTTP and Redis replaced in-process.
"""
from datetime import date, datetime

import pandas as pd
import pytest

from src.adapters import unusual_whales_adapter as U


class _Redis:
    def __init__(self):
        self.d = {}

    def incr(self, k):
        self.d[k] = self.d.get(k, 0) + 1
        return self.d[k]

    def expire(self, k, ttl):
        pass

    def get(self, k):
        return self.d.get(k)

    def setex(self, k, ttl, v):
        self.d[k] = v


class _Resp:
    def __init__(self, status, rows):
        self.status_code, self._rows = status, rows

    def json(self):
        return {"data": self._rows}

    def raise_for_status(self):
        if self.status_code >= 400:
            raise _HTTPStatusError("err")


# This suite's conftest replaces `httpx` with a MagicMock, whose attributes are not exception
# classes — `except httpx.TimeoutException` would then itself raise TypeError. The adapter gets
# a stand-in carrying REAL exception classes, so its except-clauses behave as in production.
class _TimeoutException(Exception):
    pass


class _HTTPStatusError(Exception):
    pass


@pytest.fixture
def env(monkeypatch):
    red = _Redis()
    state = {"resp": _Resp(200, [])}

    class _Client:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def get(self, *a, **k):
            return state["resp"]
    import sys
    import types
    # The module the adapter imports FROM (sys.modules), not `common.redis_client` as an
    # attribute of the mocked `common` package, which is a different object.
    monkeypatch.setattr(sys.modules["common.redis_client"], "get_redis", lambda: red,
                        raising=False)
    fake_httpx = types.SimpleNamespace(Client=_Client, TimeoutException=_TimeoutException,
                                       HTTPStatusError=_HTTPStatusError)
    monkeypatch.setattr(U, "httpx", fake_httpx)
    monkeypatch.setattr(U.UnusualWhalesAdapter, "_key", staticmethod(lambda: "k"))
    return state, red


def _fails(red, prefix="processing_"):
    return {k.split(":")[3]: v for k, v in red.d.items()
            if k.startswith(U._FAILURE_COUNTER_PREFIX) and k.split(":")[3].startswith(prefix)}


def _intraday(day, hours, mt="r"):
    return [{"start_time": f"{day}T{h:02d}:00:00Z", "end_time": f"{day}T{h + 1:02d}:00:00Z",
             "market_time": mt, "open": "10", "high": "11", "low": "9", "close": "10.5",
             "volume": 100, "date": None} for h in hours]


def test_intraday_bars_no_longer_raise_and_the_whole_end_date_is_included(env):
    state, red = env
    state["resp"] = _Resp(200, _intraday("2026-10-13", [14, 15]) + _intraday("2026-10-14", [14, 19])
                          + _intraday("2026-10-14", [21], mt="po") + _intraday("2026-10-12", [15]))
    df = U.UnusualWhalesAdapter().fetch_ohlcv("GDX", date(2026, 10, 13), date(2026, 10, 14), "1h").df
    got = [t.isoformat() for t in df["ts"]]
    # 10-12 is before the window, the post-market row is not regular session, and the 19:00Z
    # bar on the END date is kept — attaching UTC to a midnight end bound would have dropped it.
    assert got == ["2026-10-13T14:00:00", "2026-10-13T15:00:00",
                   "2026-10-14T14:00:00", "2026-10-14T19:00:00"]
    assert df["ts"].dt.tz is None, "canonical intraday ts is naive UTC with its time kept"
    assert _fails(red) == {}


def test_the_window_is_the_new_york_trading_date_not_the_utc_date(env):
    """A bar at 02:00Z on the 15th is still the 14th in New York."""
    state, red = env
    state["resp"] = _Resp(200, [{"start_time": "2026-10-15T02:00:00Z", "market_time": "r",
                                 "open": "1", "high": "1", "low": "1", "close": "1",
                                 "volume": 1, "date": None}])
    df = U.UnusualWhalesAdapter().fetch_ohlcv("GDX", date(2026, 10, 14), date(2026, 10, 14), "1h").df
    assert len(df) == 1


def test_daily_bars_use_the_market_date_inclusively(env):
    state, red = env
    rows = [{"date": d, "market_time": "r", "open": "80", "high": "84", "low": "79",
             "close": "83.4", "volume": 10, "start_time": None} for d in
            ("2026-10-10", "2026-10-13", "2026-10-14")]
    rows.append({"date": "2026-10-14", "market_time": "pr", "open": "1", "high": "1", "low": "1",
                 "close": "1", "volume": 1})
    state["resp"] = _Resp(200, rows)
    df = U.UnusualWhalesAdapter().fetch_ohlcv("GDX", date(2026, 10, 13), date(2026, 10, 14), "1d").df
    assert [t.date().isoformat() for t in df["ts"]] == ["2026-10-13", "2026-10-14"]
    assert (df["ts"].dt.hour == 0).all() and df["close"].tolist() == [83.4, 83.4]


@pytest.mark.parametrize("rows,tf,kind", [
    ([{"market_time": "r", "open": "1", "high": "1", "low": "1", "close": "1", "volume": 1}],
     "1h", "processing_ValueError"),                                   # no start_time at all
    ([{"start_time": "not-a-time", "market_time": "r", "open": "1", "high": "1", "low": "1",
       "close": "1", "volume": 1}], "1h", None),                         # unparseable timestamp
    ([{"market_time": "r", "open": "1", "high": "1", "low": "1", "close": "1", "volume": 1}],
     "1d", "processing_ValueError"),                                   # daily without date
])
def test_a_malformed_response_is_counted_once_and_still_raises_for_the_fallback(env, rows, tf, kind):
    state, red = env
    state["resp"] = _Resp(200, rows)
    with pytest.raises(Exception):
        U.UnusualWhalesAdapter().fetch_ohlcv("GDX", date(2026, 10, 13), date(2026, 10, 14), tf)
    f = _fails(red)
    assert sum(f.values()) == 1, f"exactly one processing failure, got {f}"
    if kind:
        assert kind in f


def test_http_and_rate_limit_accounting_are_unchanged(env):
    state, red = env
    state["resp"] = _Resp(429, [])
    with pytest.raises(RuntimeError):
        U.UnusualWhalesAdapter().fetch_ohlcv("GDX", date(2026, 10, 13), date(2026, 10, 14), "1h")
    assert _fails(red, prefix="rate_limited") and not _fails(red)
    assert red.get(U._COOLDOWN_KEY) == "1", "the 429 cooldown still arms"


def test_datetime_bounds_are_instants(env):
    state, red = env
    state["resp"] = _Resp(200, _intraday("2026-10-14", [14, 15, 16]))
    df = U.UnusualWhalesAdapter().fetch_ohlcv(
        "GDX", datetime(2026, 10, 14, 15, 0), datetime(2026, 10, 14, 15, 0), "1h").df
    assert [t.isoformat() for t in df["ts"]] == ["2026-10-14T15:00:00"]


def test_the_old_comparison_really_raised_on_this_payload():
    """Reproduces the production error, so this file is known to test the real defect."""
    ts = pd.to_datetime(pd.Series(["2026-10-14T14:00:00Z"]))
    with pytest.raises(TypeError, match="Invalid comparison"):
        _ = ts >= pd.Timestamp(date(2026, 10, 14))
