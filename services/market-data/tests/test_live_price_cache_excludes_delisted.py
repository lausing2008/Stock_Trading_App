"""Regression test for the delisted-blindness gap found in the 2026-09-06 deep audit
(priority item #9), in the two functions that populate stockai:live_prices.

`latest_prices()` and `refresh_live_price_cache()` (services/market-data/src/api/routes.py)
both used `select(Stock.symbol, Stock.currency).where(Stock.active.is_(True))` — no
Stock.delisted filter. refresh_live_price_cache() runs every minute during market hours and is
the SOLE writer of the stockai:live_prices Redis blob, which 7 every-minute alert scanners
(check_volume_anomalies, check_short_squeeze_alerts, check_squeeze_ignition_alerts,
check_prebreakout_alerts, check_value_area_breakdown, check_price_alerts, the post-open digest)
read INSTEAD OF querying the DB directly — so this one missing filter defeated all 7 scanners'
own correct DB-side Stock.delisted filtering.

Correctly-filtered sibling sites elsewhere in the codebase (e.g. scheduler.py's alert-symbol
resolution) pair BOTH conditions: Stock.active.is_(True), Stock.delisted.is_(False). This fix
brings these two sites in line with that established pattern.

Note: _symbols_for() (scheduler.py) is DELIBERATELY unfiltered on Stock.delisted — it feeds
daily ingestion, which is what DETECTS delisting in the first place; filtering it would make
the delisted flag permanently unsettable. This test does not touch that function and this
comment exists so it is never "fixed" by mistake in a future sweep of this bug class.
"""
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _function_body(name: str) -> str:
    start = _ROUTES_SOURCE.index(f"def {name}(")
    next_def = _ROUTES_SOURCE.index("\ndef ", start + 10)
    return _ROUTES_SOURCE[start:next_def]


def test_latest_prices_stock_query_excludes_delisted():
    body = _function_body("latest_prices")
    assert "Stock.delisted.is_(False)" in body, (
        "latest_prices()'s symbol query must exclude delisted stocks — this cache-miss "
        "fallback feeds the same stockai:live_prices blob the every-minute alert scanners read."
    )


def test_refresh_live_price_cache_stock_query_excludes_delisted():
    body = _function_body("refresh_live_price_cache")
    assert "Stock.delisted.is_(False)" in body, (
        "refresh_live_price_cache() is the SOLE writer of stockai:live_prices, read every "
        "minute during market hours by 7 alert scanners instead of the DB directly — a "
        "delisted stock's frozen last price staying in this cache lets those scanners fire "
        "forever on an untradeable stock."
    )
