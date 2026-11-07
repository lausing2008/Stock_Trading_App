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
            state["gets"] = state.get("gets", 0) + 1
            if state.get("hook"):
                state["hook"]()
            if state.get("seq"):
                return state["seq"].pop(0)
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
    state["slept"] = []
    monkeypatch.setattr(U, "_retry_sleep", state["slept"].append)
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


# ── AUD-UW-429-RETRY (2026-11-01) ───────────────────────────────────────────────────────

class _Resp429(_Resp):
    def __init__(self, headers=None, text='{"message":"slow down"}'):
        super().__init__(429, [])
        self.headers, self.text = headers or {}, text


def _count(red, prefix, name):
    return sum(v for k, v in red.d.items() if k.startswith(prefix) and k.split(":")[3] == name)


def test_a_single_refusal_is_retried_and_the_bars_are_returned(env):
    state, red = env
    state["seq"] = [_Resp429(), _Resp(200, _intraday("2026-10-14", [14]))]
    df = U.UnusualWhalesAdapter().fetch_ohlcv("GDX", date(2026, 10, 14), date(2026, 10, 14), "1h").df
    assert len(df) == 1 and state["gets"] == 2
    assert state["slept"] == [U._RETRY_DELAY_S]
    assert red.get(U._COOLDOWN_KEY) is None, "a recovered refusal must not pause US ingest"
    assert _count(red, U._FAILURE_COUNTER_PREFIX, "rate_limited_retried") == 1
    assert _count(red, U._FAILURE_COUNTER_PREFIX, "rate_limited") == 0
    assert _count(red, U._CALL_COUNTER_PREFIX, "/api/stock/{symbol}/ohlc/{candle}") == 2


def test_a_second_refusal_fails_the_fetch_and_arms_the_cooldown(env):
    state, red = env
    state["seq"] = [_Resp429(), _Resp429()]
    with pytest.raises(RuntimeError):
        U.UnusualWhalesAdapter().fetch_ohlcv("GDX", date(2026, 10, 14), date(2026, 10, 14), "1h")
    assert state["gets"] == 2, "exactly one retry"
    assert red.get(U._COOLDOWN_KEY) == "1"
    assert _count(red, U._FAILURE_COUNTER_PREFIX, "rate_limited") == 1
    assert _count(red, U._FAILURE_COUNTER_PREFIX, "rate_limited_retried") == 1


def test_a_success_is_not_retried(env):
    state, red = env
    state["resp"] = _Resp(200, _intraday("2026-10-14", [14]))
    U.UnusualWhalesAdapter().fetch_ohlcv("GDX", date(2026, 10, 14), date(2026, 10, 14), "1h")
    assert state["gets"] == 1 and state["slept"] == []


def test_other_errors_are_not_retried(env):
    state, red = env
    state["seq"] = [_Resp(503, []), _Resp(200, [])]
    with pytest.raises(Exception):
        U.UnusualWhalesAdapter().fetch_ohlcv("GDX", date(2026, 10, 14), date(2026, 10, 14), "1h")
    assert state["gets"] == 1 and state["slept"] == []


@pytest.mark.parametrize("hdr,expected", [
    ({"retry-after": "2"}, 2.0),
    ({"retry-after": "60"}, U._RETRY_DELAY_MAX_S),
    ({"retry-after": "0"}, U._RETRY_DELAY_S),
    ({"retry-after": "Fri, 31 Oct 2026 20:00:00 GMT"}, U._RETRY_DELAY_S),
    ({}, U._RETRY_DELAY_S),
])
def test_retry_after_is_honoured_but_capped(env, hdr, expected):
    state, red = env
    state["seq"] = [_Resp429(headers=hdr), _Resp(200, [])]
    U.UnusualWhalesAdapter().fetch_ohlcv("GDX", date(2026, 10, 14), date(2026, 10, 14), "1h")
    assert state["slept"] == [expected]


def test_the_refusal_is_logged_with_body_and_uw_headers(env, monkeypatch):
    state, red = env
    seen = []
    monkeypatch.setattr(U.log, "warning", lambda ev, **kw: seen.append((ev, kw)))
    state["seq"] = [_Resp429(headers={"x-uw-daily-req-count": "609", "content-type": "json",
                                      "retry-after": "1"}, text="x" * 500),
                    _Resp429(text="concurrency")]
    with pytest.raises(RuntimeError):
        U.UnusualWhalesAdapter().fetch_ohlcv("GDX", date(2026, 10, 14), date(2026, 10, 14), "1h")
    assert [kw["attempt"] for ev, kw in seen if ev == "unusual_whales.bar_rate_limited"] == [1, 2]
    first = seen[0][1]
    assert first["headers"] == {"x-uw-daily-req-count": "609", "retry-after": "1"}
    assert len(first["body"]) == 300 and seen[1][1]["body"] == "concurrency"


def test_a_broken_response_object_cannot_break_refusal_logging(env):
    class _Weird(_Resp):
        status_code = 429

        def __init__(self):
            super().__init__(429, [])

        @property
        def headers(self):
            raise RuntimeError("boom")
    U.UnusualWhalesAdapter._log_refusal(_Weird(), "GDX", "1h", 1)


def test_the_cooldown_is_shorter_than_before():
    assert U._COOLDOWN_SECONDS <= 30


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


# ── AUD-UW-BAR-INFLIGHT: at most _BAR_MAX_IN_FLIGHT bar requests in flight per process ────────

def _inflight_probe(state, hold_s=0.05):
    """A client.get that records how many calls overlap, holding each briefly."""
    import threading
    import time as _t
    lock = threading.Lock()
    seen = {"now": 0, "max": 0}

    def hook():
        with lock:
            seen["now"] += 1
            seen["max"] = max(seen["max"], seen["now"])
        _t.sleep(hold_s)
        with lock:
            seen["now"] -= 1
    state["hook"] = hook
    return seen


def test_six_concurrent_fetches_never_exceed_the_cap(env, monkeypatch):
    import threading
    state, red = env
    monkeypatch.setattr(U, "_bar_slots", threading.BoundedSemaphore(2))
    seen = _inflight_probe(state)
    errors = []

    def run():
        try:
            U.UnusualWhalesAdapter().fetch_ohlcv("GDX", date(2026, 10, 13), date(2026, 10, 14), "1d")
        except Exception as exc:  # pragma: no cover - surfaced below
            errors.append(exc)
    threads = [threading.Thread(target=run) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(10)
    assert errors == []
    assert state["gets"] == 6, "every fetch still runs"
    # <= 2 is the property; >= 1 only guards that the probe ran. Without the cap this is 6.
    assert 1 <= seen["max"] <= 2


def test_the_default_cap_leaves_one_uw_slot_free(monkeypatch):
    monkeypatch.delenv("UW_BAR_MAX_IN_FLIGHT", raising=False)
    assert U._bar_max_in_flight() == 2


@pytest.mark.parametrize("raw,expected", [("3", 3), ("0", 1), ("x", 2), (None, 2)])
def test_the_cap_can_be_overridden_but_not_below_one(monkeypatch, raw, expected):
    if raw is None:
        monkeypatch.delenv("UW_BAR_MAX_IN_FLIGHT", raising=False)
    else:
        monkeypatch.setenv("UW_BAR_MAX_IN_FLIGHT", raw)
    assert U._bar_max_in_flight() == expected


def test_no_slot_is_held_during_the_retry_pause(env, monkeypatch):
    import threading
    state, red = env
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(U, "_bar_slots", slots)
    free_while_sleeping = []

    def sleep(_s):
        ok = slots.acquire(blocking=False)
        free_while_sleeping.append(ok)
        if ok:
            slots.release()
    monkeypatch.setattr(U, "_retry_sleep", sleep)
    state["seq"] = [_Resp(429, []), _Resp(200, [])]
    U.UnusualWhalesAdapter().fetch_ohlcv("GDX", date(2026, 10, 13), date(2026, 10, 14), "1d")
    assert free_while_sleeping == [True]


def test_a_request_that_raises_still_releases_its_slot(env, monkeypatch):
    import threading
    state, red = env
    slots = threading.BoundedSemaphore(1)
    monkeypatch.setattr(U, "_bar_slots", slots)

    def boom():
        raise _TimeoutException("slow")
    state["hook"] = boom
    with pytest.raises(_TimeoutException):
        U.UnusualWhalesAdapter().fetch_ohlcv("GDX", date(2026, 10, 13), date(2026, 10, 14), "1d")
    assert slots.acquire(blocking=False), "slot leaked after an exception"


def test_a_slot_wait_timeout_proceeds_instead_of_failing(env, monkeypatch):
    import threading
    state, red = env
    slots = threading.BoundedSemaphore(1)
    slots.acquire()  # held elsewhere and never released
    monkeypatch.setattr(U, "_bar_slots", slots)
    monkeypatch.setattr(U, "_BAR_SLOT_WAIT_S", 0.01)
    state["resp"] = _Resp(200, [])
    U.UnusualWhalesAdapter().fetch_ohlcv("GDX", date(2026, 10, 13), date(2026, 10, 14), "1d")
    assert state["gets"] == 1
    slots.release()  # the outside holder's slot: releasing once must not over-release
    with pytest.raises(ValueError):
        slots.release()

