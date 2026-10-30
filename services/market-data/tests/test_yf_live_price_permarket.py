"""YF-LIVEPRICE-PERMARKET / YF-ALERTS-LIVECACHE (2026-10-30): cut the per-minute Yahoo load.

Every ticker in a `yf.download` is its own Yahoo request (3 tickers measured 6 on production).
The live-price refresh downloaded all ~184 symbols every minute whenever EITHER market was
open (~176k requests a trading day), and check_price_alerts called `fast_info` per symbol every
minute around the clock. See docs/audits/2026-10-30-yfinance-usage-audit.md.
"""
import json
import pathlib
from collections import namedtuple
from datetime import datetime, timezone
from unittest.mock import patch

import src.api.routes as R
import src.services.scheduler as S

Row = namedtuple("Row", "symbol currency market")
STOCKS = [Row("AAPL", "USD", "US"), Row("MSFT", "USD", "US"),
          Row("0700.HK", "HKD", "HK"), Row("0005.HK", "HKD", "HK")]


class _Redis:
    def __init__(self, data=None):
        self.data = dict(data or {})
        self.ttl = {}

    def get(self, k):
        return self.data.get(k)

    def setex(self, k, ttl, v):
        self.data[k], self.ttl[k] = v, ttl


class _Session:
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False

    def execute(self, *a, **k):
        return type("Res", (), {"all": lambda self: list(STOCKS)})()


CLOSE = datetime(2026, 10, 29, 20, 0, tzinfo=timezone.utc)
POST_CLOSE_FETCH = "2026-10-29T20:59:00+00:00"
PRE_CLOSE_FETCH = "2026-10-29T17:30:00+00:00"


def _row(sym, price, fetched_at=POST_CLOSE_FETCH):
    return {"symbol": sym, "price": price, "fetched_at": fetched_at}


def _refresh(markets, snapshot=None, returns=None):
    r = _Redis({R._LIVE_LAST_KEY: json.dumps(snapshot)} if snapshot is not None else {})
    asked = []

    def bulk(stocks):
        asked.extend(s.symbol for s in stocks)
        ret = returns if returns is not None else {s.symbol: 1.0 for s in stocks}
        return [_row(s.symbol, ret[s.symbol]) for s in stocks if s.symbol in ret]

    with patch("db.SessionLocal", _Session), patch.object(R, "_get_redis", lambda: r), \
         patch.object(R, "_fetch_live_bulk", bulk):
        n = R.refresh_live_price_cache(markets)
    live = {e["symbol"]: e["price"] for e in json.loads(r.data.get(R._LIVE_KEY, "[]"))}
    last = {e["symbol"]: e["price"] for e in json.loads(r.data.get(R._LIVE_LAST_KEY, "[]"))}
    return n, asked, live, last, r


SNAP = [_row("AAPL", 9.0), _row("MSFT", 9.0), _row("0700.HK", 5.0), _row("0005.HK", 6.0)]


# ── the refresh downloads only the open market ──────────────────────────────────────────

def test_only_the_open_markets_symbols_are_downloaded_and_the_other_is_carried():
    n, asked, live, last, r = _refresh({"US"}, snapshot=SNAP)
    assert sorted(asked) == ["AAPL", "MSFT"] and n == 2
    assert live == {"AAPL": 1.0, "MSFT": 1.0, "0700.HK": 5.0, "0005.HK": 6.0}
    assert last == live
    assert r.ttl[R._LIVE_KEY] == R._LIVE_TTL and r.ttl[R._LIVE_LAST_KEY] >= 7 * 24 * 3600


def test_a_closed_market_symbol_without_a_snapshot_row_is_fetched_once():
    _, asked, live, _, _ = _refresh({"US"}, snapshot=[_row("0700.HK", 5.0)])
    assert sorted(asked) == ["0005.HK", "AAPL", "MSFT"]
    assert live["0700.HK"] == 5.0 and live["0005.HK"] == 1.0


def test_an_open_market_symbol_yahoo_missed_is_dropped_not_carried_stale():
    _, _, live, _, _ = _refresh({"US"}, snapshot=SNAP, returns={"AAPL": 1.0})
    assert "MSFT" not in live
    assert live["AAPL"] == 1.0 and live["0700.HK"] == 5.0


def test_no_markets_argument_keeps_the_old_fetch_everything_behaviour():
    _, asked, _, _, _ = _refresh(None, snapshot=SNAP)
    assert sorted(asked) == sorted(s.symbol for s in STOCKS)


def test_a_failed_download_writes_nothing():
    n, _, _, _, r = _refresh({"US"}, snapshot=SNAP, returns={})
    assert n == 0 and R._LIVE_KEY not in r.data


def test_the_scheduled_job_passes_only_the_open_markets():
    src = pathlib.Path(S.__file__).read_text()
    i = src.index("def _live_price_refresh_job")
    body = src[i:src.index("_scheduler.add_job(", i)]
    assert 'refresh_live_price_cache({m for m, o in (("US", us_open), ("HK", hk_open)) if o})' in body


# ── market windows ──────────────────────────────────────────────────────────────────────

def test_market_windows():
    assert R.live_price_markets_open(datetime(2026, 10, 28, 19, 30, tzinfo=timezone.utc)) == {"US"}
    assert R.live_price_markets_open(datetime(2026, 10, 29, 2, 0, tzinfo=timezone.utc)) == {"HK"}   # 10:00 HKT
    assert R.live_price_markets_open(datetime(2026, 10, 29, 10, 0, tzinfo=timezone.utc)) == set()   # gap
    assert R.live_price_markets_open(datetime(2026, 10, 31, 15, 0, tzinfo=timezone.utc)) == set()   # Saturday
    assert R.live_price_markets_open(datetime(2026, 11, 26, 15, 0, tzinfo=timezone.utc)) == set()   # Thanksgiving (HK closed: 23:00 HKT)


# ── page views outside both windows serve the snapshot ─────────────────────────────────

def _latest(open_markets, snapshot):
    r = _Redis({R._LIVE_LAST_KEY: json.dumps(snapshot)} if snapshot is not None else {})
    downloaded = []

    def bulk(stocks):
        downloaded.extend(s.symbol for s in stocks)
        return [_row(s.symbol, 1.0) for s in stocks]

    with patch.object(R, "_get_redis", lambda: r), patch.object(R, "_fetch_live_bulk", bulk), \
         patch.object(R, "live_price_markets_open", lambda: set(open_markets)):
        out = R.latest_prices(symbols=None, session=_Session())
    return out, downloaded, r


def test_outside_both_windows_a_page_view_serves_the_snapshot_and_writes_no_live_cache():
    out, downloaded, r = _latest(set(), SNAP + [_row("GONE", 3.0)])
    assert downloaded == [] and R._LIVE_KEY not in r.data
    assert {e["symbol"] for e in out} == {s.symbol for s in STOCKS}       # inactive symbol filtered


def test_inside_a_window_or_without_a_snapshot_the_page_view_downloads_as_before():
    assert _latest({"US"}, SNAP)[1] != []
    assert _latest(set(), None)[1] != []


# ── price alerts read the cache ────────────────────────────────────────────────────────

class _YF:
    def __init__(self, calls):
        self.calls = calls

    def Tickers(self, s):
        self.calls.append(s)
        return type("T", (), {"tickers": {x: type("F", (), {"fast_info": type("I", (), {"last_price": 2.0})()})()
                                          for x in s.split()}})()


def _alert(symbols, live, last, open_markets):
    r = _Redis({R._LIVE_KEY: json.dumps(live), R._LIVE_LAST_KEY: json.dumps(last)})
    calls = []
    import sys
    with patch.object(R, "_get_redis", lambda: r), \
         patch.object(R, "live_price_markets_open", lambda *a: set(open_markets)), \
         patch.object(R, "_last_session_close", lambda m, now: CLOSE), \
         patch.dict(sys.modules, {"yfinance": _YF(calls)}):
        return S._alert_prices(symbols), calls


def test_a_closed_markets_row_fetched_before_its_close_is_not_used_as_the_close():
    """Review finding: if Yahoo blocked the host before the close, the carried row is an
    intraday price; paper snapshots and options settlement must not read it as the close."""
    p, calls = _alert(["AAPL", "MSFT"], [_row("AAPL", 1.0, PRE_CLOSE_FETCH)],
                      [_row("AAPL", 1.0, PRE_CLOSE_FETCH), _row("MSFT", 9.0)], set())
    assert calls == ["AAPL"]
    assert p == {"AAPL": 2.0, "MSFT": 9.0}


def test_a_row_without_provenance_is_never_taken_as_a_close():
    p, calls = _alert(["AAPL"], [], [{"symbol": "AAPL", "price": 1.0}], set())
    assert calls == ["AAPL"]


def test_last_session_close_handles_weekends_and_early_closes():
    assert R._last_session_close("US", datetime(2026, 10, 31, 15, tzinfo=timezone.utc)) == \
        datetime(2026, 10, 30, 20, tzinfo=timezone.utc)                       # Sat -> Fri 16:00 ET
    assert R._last_session_close("US", datetime(2026, 11, 28, 15, tzinfo=timezone.utc)) == \
        datetime(2026, 11, 27, 18, tzinfo=timezone.utc)                       # Fri 11-27 early close 13:00 ET
    assert R._last_session_close("HK", datetime(2026, 10, 30, 7, 30, tzinfo=timezone.utc)) == \
        datetime(2026, 10, 29, 8, tzinfo=timezone.utc)                        # 15:30 HKT -> prior 16:00 HKT


def test_alert_prices_come_from_the_live_cache_without_yahoo():
    p, calls = _alert(["AAPL", "0700.HK"], [_row("AAPL", 1.5), _row("0700.HK", 5.0)], [], {"US"})
    assert p == {"AAPL": 1.5, "0700.HK": 5.0} and calls == []


def test_a_closed_market_uses_its_snapshot_and_outside_windows_yahoo_is_never_called():
    p, calls = _alert(["AAPL", "0700.HK"], [], SNAP, set())
    assert p == {"AAPL": 9.0, "0700.HK": 5.0} and calls == []


def test_only_open_market_symbols_missing_from_the_cache_go_to_yahoo():
    p, calls = _alert(["AAPL", "NVDA", "9988.HK"], [_row("AAPL", 1.5)], [_row("9988.HK", 4.0)], {"US"})
    assert calls == ["NVDA"]
    assert p == {"AAPL": 1.5, "NVDA": 2.0, "9988.HK": 4.0}


def test_a_symbol_the_snapshot_never_held_is_still_asked_at_any_hour():
    """Review finding: an alert on BTC-USD or ^HSI is not in the universe, so it is never in the
    snapshot; treating it as a closed US symbol would silence it outside US hours."""
    p, calls = _alert(["BTC-USD", "AAPL"], [], SNAP, set())
    assert calls == ["BTC-USD"]
    assert p == {"BTC-USD": 2.0, "AAPL": 9.0}


def test_an_open_markets_snapshot_is_never_used_for_a_live_price():
    p, calls = _alert(["MSFT"], [], SNAP, {"US"})
    assert calls == ["MSFT"] and p == {"MSFT": 2.0}


def test_a_zero_price_is_returned_for_the_drawdown_alert_to_judge():
    p, _ = _alert(["AAPL"], [_row("AAPL", 0)], [], {"US"})
    assert p == {"AAPL": 0.0}


def test_check_price_alerts_no_longer_calls_yahoo_directly():
    src = pathlib.Path(S.__file__).read_text()
    i = src.index("def check_price_alerts(")
    body = src[i:src.index("\ndef ", i + 10)]
    assert "Tickers(" not in body and "fast_info" not in body and "import yfinance" not in body
    assert body.count("_alert_prices(") == 2


# ── paper trading reads the cache ──────────────────────────────────────────────────────

import src.services.paper_trading_engine as P  # noqa: E402


class _Frame:
    """Just enough of yf.download's group_by='ticker' frame for _fetch_live_prices."""
    def __init__(self, syms, price):
        import pandas as pd
        self.syms, self.empty = syms, False
        self._col = pd.Series([price - 1, price])

    def __getitem__(self, k):
        return {"Close": self._col} if k in self.syms else self._col if k == "Close" else None


def _paper(symbols, live, last, open_markets):
    r = _Redis({R._LIVE_KEY: json.dumps(live), R._LIVE_LAST_KEY: json.dumps(last)})
    asked = []

    class YF:
        @staticmethod
        def download(syms, **k):
            asked.append(list(syms))
            return _Frame(syms, 7.0)

    import sys
    with patch.object(R, "_get_redis", lambda: r), \
         patch.object(R, "live_price_markets_open", lambda *a: set(open_markets)), \
         patch.object(R, "_last_session_close", lambda m, now: CLOSE), \
         patch.dict(sys.modules, {"yfinance": YF}):
        return P._fetch_live_prices(symbols), asked


def test_paper_prices_come_from_the_cache_and_only_the_rest_is_downloaded():
    prices, asked = _paper(["AAPL", "NVDA", "0700.HK"], [_row("AAPL", 3.0)], SNAP, {"US"})
    assert asked == [["NVDA"]]
    assert prices == {"AAPL": 3.0, "NVDA": 7.0, "0700.HK": 5.0}


def test_paper_makes_no_download_when_the_cache_answers_everything():
    prices, asked = _paper(["AAPL", "0700.HK"], [_row("AAPL", 3.0), _row("0700.HK", 5.0)], [], {"US"})
    assert asked == [] and prices == {"AAPL": 3.0, "0700.HK": 5.0}


def test_paper_still_rejects_sub_50_cent_prices_from_the_cache():
    prices, _ = _paper(["AAPL"], [_row("AAPL", 0.01)], [], {"US"})
    assert prices == {}


# ── fundamentals: remember failures, serve the last good copy ──────────────────────────

class _Ticker:
    calls = 0

    def __init__(self, sym):
        pass

    @property
    def info(self):
        type(self).calls += 1
        raise RuntimeError("Too Many Requests. Rate limited.")

    def __getattr__(self, name):          # every other Yahoo sub-request on the same ticker
        type(self).calls += 1
        raise RuntimeError("Too Many Requests. Rate limited.")


def _fund(redis_data, refresh=False):
    r = _Redis(redis_data)
    _Ticker.calls = 0
    with patch.object(R, "_get_redis", lambda: r), patch.object(R.yf, "Ticker", _Ticker):
        out = R.get_fundamentals("MU", refresh=refresh, db=None)
    return out, _Ticker.calls, r


def test_a_failed_info_stops_the_fetch_marks_failure_and_serves_the_last_good_copy():
    last = {"market_cap": 123, "trailing_pe": 9.5}
    out, calls, r = _fund({R._FUND_LAST_KEY.format("MU"): json.dumps(last)})
    assert calls == 1
    assert out["market_cap"] == 123
    assert r.ttl[R._FUND_FAIL_KEY.format("MU")] == R._FUND_FAIL_TTL


def test_inside_the_failure_window_yahoo_is_not_asked_again():
    out, calls, _ = _fund({R._FUND_FAIL_KEY.format("MU"): "1"})
    assert calls == 0
    assert getattr(out, "market_cap", None) is None


def test_a_partial_answer_is_not_recorded_as_a_failure():
    """Review finding: Yahoo answering without market cap/PE/revenue (a thin ticker) is data,
    so the empty-fetch guard must not set the 20-minute failure marker."""
    src = pathlib.Path(R.__file__).read_text()
    i = src.index("if fetch_looks_empty:")
    assert "_fundamentals_fallback(symbol, mark_failed=False)" in src[i:i + 900]
    assert "mark_failed=True" not in src[i:i + 900]


def test_an_explicit_refresh_still_asks_yahoo():
    _, calls, _ = _fund({R._FUND_FAIL_KEY.format("MU"): "1"}, refresh=True)
    assert calls == 1


def test_a_successful_fetch_keeps_a_14_day_last_good_copy():
    src = pathlib.Path(R.__file__).read_text()
    assert "_get_redis().setex(_FUND_LAST_KEY.format(symbol.upper()), _FUND_LAST_TTL, data.model_dump_json())" in src
    assert R._FUND_LAST_TTL >= 14 * 24 * 3600 and R._FUND_FAIL_TTL == 20 * 60
