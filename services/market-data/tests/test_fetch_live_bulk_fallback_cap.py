"""Tests for BUG-YFCALLVOL2 (2026-08-17) — api/routes.py's _fetch_live_bulk() unconditionally
retried EVERY symbol the bulk yf.download() call missed via an individual _fetch_live_one()
call each (up to 4 concurrent). During a real Yahoo-side rate-limit event this app hit live in
production, the bulk call itself got throttled and missed the WHOLE universe (confirmed live:
repeated live_prices.bulk_fallback count=165 events, recurring every 1-2 minutes) — the SAME
condition that caused the bulk miss guaranteed the ~150+-request individual fallback storm
ALSO got rate-limited, except now it was actively amplifying the same throttle this function
exists to avoid, every single minute, with zero backoff. This is the exact BUG-YFCALLVOL
amplification pattern already fixed once in paper_trading_engine.py's _fetch_live_prices(),
recurring here in a second, never-touched call site.

Fixed by capping the fallback: only retry individually when the miss count is small (a real,
few-symbol straggler case Yahoo's batch endpoint occasionally produces even under normal
conditions) — a large miss count skips the fallback entirely, since it's evidence of a global
throttle event that individual retries would only make worse.
"""
import pandas as pd

from src.api.routes import _fetch_live_bulk, _LIVE_BULK_FALLBACK_MAX


class _FakeStock:
    def __init__(self, symbol, currency="USD"):
        self.symbol = symbol
        self.currency = currency


def _multi_symbol_download_df(prices: dict) -> pd.DataFrame:
    """Builds a fake yf.download(group_by="ticker") multi-symbol DataFrame — 2 rows (2d
    window) per symbol, Close + Volume columns, matching _fetch_live_bulk()'s own real shape."""
    frames = {}
    for sym, (prev_close, close) in prices.items():
        frames[sym] = pd.DataFrame({"Close": [prev_close, close], "Volume": [1000, 1200]})
    return pd.concat(frames, axis=1)


def _all_missing_multi_symbol_df(symbols: list) -> pd.DataFrame:
    """A genuinely non-empty multi-symbol DataFrame where EVERY symbol's Close is NaN — the
    real shape of a rate-limited bulk download that technically returns a result but has no
    usable data for anyone (distinct from a totally empty/failed download, which _fetch_live_
    bulk() already handles via its own `raw is None or raw.empty` check earlier)."""
    frames = {}
    for sym in symbols:
        frames[sym] = pd.DataFrame({"Close": [float("nan"), float("nan")], "Volume": [None, None]})
    return pd.concat(frames, axis=1)


def test_small_miss_count_still_uses_the_individual_fallback(monkeypatch):
    """The real, intended behavior: a handful of stragglers Yahoo's bulk endpoint omitted
    (not a rate-limit event) still get filled in via the individual fallback."""
    import src.api.routes as routes_mod

    stocks = [_FakeStock("AAPL"), _FakeStock("MSFT"), _FakeStock("MISSING")]
    monkeypatch.setattr(
        routes_mod.yf, "download",
        lambda symbols, **kw: _multi_symbol_download_df({"AAPL": (100.0, 101.0), "MSFT": (300.0, 305.0)}),
    )
    fallback_calls = []
    monkeypatch.setattr(
        routes_mod, "_fetch_live_one",
        lambda sym, ccy: fallback_calls.append(sym) or {"symbol": sym, "price": 42.0, "prev_close": 41.0,
                                                          "change_pct": 2.4, "currency": ccy, "volume": None, "avg_volume": None},
    )

    results = _fetch_live_bulk(stocks)

    symbols_returned = {r["symbol"] for r in results}
    assert symbols_returned == {"AAPL", "MSFT", "MISSING"}
    assert fallback_calls == ["MISSING"]


def test_large_miss_count_skips_the_fallback_entirely(monkeypatch):
    """The actual bug fix: when the bulk call misses a large number of symbols (a rate-limit
    event, not a few stragglers — mirrors the real live incident's ~150-symbol universe going
    entirely missing), the individual fallback must NOT fire at all — firing 100+ more requests
    during an active throttle only makes it worse.

    Uses a FIXED fixture size (150, matching the real live production universe size at the time
    this bug was found) rather than deriving it from _LIVE_BULK_FALLBACK_MAX itself — a first
    version of this test built `_LIVE_BULK_FALLBACK_MAX + 5` fake stocks, which is fine at the
    real threshold (20) but hangs the test suite if the constant is ever sabotaged/misconfigured
    to something huge (confirmed while adversarially verifying this very test: raising the
    constant to 999999 made this test attempt to build and process a million-symbol fixture and
    time out, rather than failing cleanly on the real assertion). A fixed, realistic size avoids
    coupling the test's own resource usage to the value under test."""
    import src.api.routes as routes_mod

    many_missing = [_FakeStock(f"SYM{i}") for i in range(150)]
    monkeypatch.setattr(
        routes_mod.yf, "download",
        lambda symbols, **kw: _all_missing_multi_symbol_df([s.symbol for s in many_missing]),
    )
    fallback_calls = []
    monkeypatch.setattr(
        routes_mod, "_fetch_live_one",
        lambda sym, ccy: fallback_calls.append(sym) or None,
    )

    results = _fetch_live_bulk(many_missing)

    assert results == []
    assert fallback_calls == []  # the individual fallback must never have been invoked


def test_miss_count_exactly_at_the_threshold_still_uses_the_fallback(monkeypatch):
    """Boundary check: exactly _LIVE_BULK_FALLBACK_MAX misses should still use the fallback —
    only a count STRICTLY GREATER than the threshold skips it.

    This test's own fixture size is necessarily derived from _LIVE_BULK_FALLBACK_MAX itself
    (a real boundary test has no other way to test the exact boundary) — the sanity assert
    below converts a misconfigured/sabotaged huge constant into a fast, clear failure instead
    of the test hanging while it tries to build and process a million-item fixture (the exact
    failure mode discovered while adversarially verifying this file: sabotaging the constant to
    999999 made this test attempt a million-symbol ThreadPoolExecutor run and time out rather
    than failing on a real assertion)."""
    assert _LIVE_BULK_FALLBACK_MAX < 1000, (
        f"_LIVE_BULK_FALLBACK_MAX={_LIVE_BULK_FALLBACK_MAX} is implausibly large for a "
        "fallback-skip threshold — refusing to build a fixture this size rather than hang"
    )
    import src.api.routes as routes_mod

    exactly_at_threshold = [_FakeStock(f"SYM{i}") for i in range(_LIVE_BULK_FALLBACK_MAX)]
    monkeypatch.setattr(
        routes_mod.yf, "download",
        lambda symbols, **kw: _all_missing_multi_symbol_df([s.symbol for s in exactly_at_threshold]),
    )
    fallback_calls = []
    monkeypatch.setattr(
        routes_mod, "_fetch_live_one",
        lambda sym, ccy: fallback_calls.append(sym) or {"symbol": sym, "price": 1.0, "prev_close": 1.0,
                                                          "change_pct": 0.0, "currency": "USD", "volume": None, "avg_volume": None},
    )

    results = _fetch_live_bulk(exactly_at_threshold)

    assert len(fallback_calls) == _LIVE_BULK_FALLBACK_MAX
    assert len(results) == _LIVE_BULK_FALLBACK_MAX


def test_miss_count_one_over_the_threshold_skips_the_fallback(monkeypatch):
    """Same sanity-guard reasoning as the exactly-at-threshold test above — this is also a
    real boundary test that must derive its fixture size from the constant itself."""
    assert _LIVE_BULK_FALLBACK_MAX < 1000, (
        f"_LIVE_BULK_FALLBACK_MAX={_LIVE_BULK_FALLBACK_MAX} is implausibly large for a "
        "fallback-skip threshold — refusing to build a fixture this size rather than hang"
    )
    import src.api.routes as routes_mod

    one_over = [_FakeStock(f"SYM{i}") for i in range(_LIVE_BULK_FALLBACK_MAX + 1)]
    monkeypatch.setattr(
        routes_mod.yf, "download",
        lambda symbols, **kw: _all_missing_multi_symbol_df([s.symbol for s in one_over]),
    )
    fallback_calls = []
    monkeypatch.setattr(routes_mod, "_fetch_live_one", lambda sym, ccy: fallback_calls.append(sym) or None)

    _fetch_live_bulk(one_over)

    assert fallback_calls == []


def test_zero_misses_never_touches_the_fallback_path_at_all(monkeypatch):
    """When the bulk call succeeds for everything, the fallback branch must not run at all —
    confirms the fix's `missed` empty-list short-circuit is untouched by this change."""
    import src.api.routes as routes_mod

    stocks = [_FakeStock("AAPL"), _FakeStock("MSFT")]
    monkeypatch.setattr(
        routes_mod.yf, "download",
        lambda symbols, **kw: _multi_symbol_download_df({"AAPL": (100.0, 101.0), "MSFT": (300.0, 305.0)}),
    )
    fallback_calls = []
    monkeypatch.setattr(routes_mod, "_fetch_live_one", lambda sym, ccy: fallback_calls.append(sym) or None)

    results = _fetch_live_bulk(stocks)

    assert len(results) == 2
    assert fallback_calls == []
