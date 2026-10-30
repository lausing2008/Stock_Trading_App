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


def _row(sym, price):
    return {"symbol": sym, "price": price}


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
    with patch.object(S, "_get_redis", lambda: r), \
         patch.object(R, "live_price_markets_open", lambda: set(open_markets)), \
         patch.dict(sys.modules, {"yfinance": _YF(calls)}):
        return S._alert_prices(symbols), calls


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
