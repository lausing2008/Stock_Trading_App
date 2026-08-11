"""Regression test for AUD265-SQUEEZE-SCREENER-NO-DELISTED-FILTER (BUG-DELISTED-GENERATION-
BLIND, 11th instance).

Stock.delisted (aud14-survivorship) never flips Stock.active — a confirmed-delisted stock
stays "active" forever. The short-squeeze screener (GET /stocks/short-squeeze, `short_squeeze()`
in routes.py) never excluded it, and since results sort by short_percent_of_float descending, a
delisted heavily-shorted name would stay pinned at the TOP of the screener indefinitely.

short_squeeze() can't be exercised end-to-end in this test environment (it needs a real DB
session for the Stock/Ranking queries, matching the constraint already documented for
_active_us_symbols() and every other source-text-extraction test for this bug class) — matching
test_delisted_excluded_from_scheduler_jobs.py's established technique exactly.
"""
import pathlib

_routes_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_SOURCE = _routes_path.read_text()


def test_short_squeeze_universe_query_excludes_delisted_stocks():
    start = _SOURCE.index("def short_squeeze(")
    end = _SOURCE.index("stock_map = {s.symbol: s for s in stocks}", start)
    body = _SOURCE[start:end]
    assert "Stock.delisted.is_(False)" in body
    assert "Stock.active.is_(True)" in body
