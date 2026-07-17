"""Regression test for AUD-MD-FUNDAMENTALS-EMPTY-OVERWRITE.

get_fundamentals() previously cached AND persisted a completely empty yfinance response
(ticker.info == {} on a transient rate-limit/timeout) as if it were a normal successful
result — every field null, silently overwriting yesterday's real fundamentals data. Confirmed
happening in production 2026-07-16: AAPL/MU's fundamentals row went from real values to 100%
NULL after one bad nightly batch run, blanking the stock detail page's Company Financials
section and P/E/EV/Beta cards.

routes.py can't be imported directly in this test environment (conftest.py stubs sqlalchemy/db
as MagicMock(), and this module does real query construction at import time in several route
handlers) — this is a source-text check confirming the guard exists and is wired into both the
Redis cache write and the DB persist path, matching the source-text-regression pattern already
used elsewhere in this repo's test suite for similarly large, hard-to-isolate functions (e.g.
test_scheduler_static_names.py).
"""
import pathlib

_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
)
_SOURCE = _PATH.read_text()


def _get_fundamentals_body() -> str:
    start = _SOURCE.index("def get_fundamentals(")
    end = _SOURCE.index("\n\n\ndef ", start)
    return _SOURCE[start:end]


def test_empty_fetch_guard_exists():
    body = _get_fundamentals_body()
    assert "fetch_looks_empty" in body
    assert "data.market_cap is None and data.trailing_pe is None and data.total_revenue is None" in body


def test_guard_runs_before_the_redis_cache_write():
    body = _get_fundamentals_body()
    guard_pos = body.index("if fetch_looks_empty:")
    cache_write_pos = body.index("_get_redis().setex(cache_key, _FUND_TTL, data.model_dump_json())")
    assert guard_pos < cache_write_pos, (
        "the empty-fetch guard must run BEFORE the cache write, or a bad fetch still "
        "gets cached for 24h despite the guard existing"
    )


def test_guard_runs_before_the_db_persist():
    body = _get_fundamentals_body()
    guard_pos = body.index("if fetch_looks_empty:")
    # The DB persist block starts with this comment, immediately preceding the pg_insert call.
    persist_pos = body.index("# Persist key fields to DB for ML feature use")
    assert guard_pos < persist_pos, (
        "the empty-fetch guard must run BEFORE the DB persist, or a bad fetch still "
        "overwrites yesterday's good row despite the guard existing"
    )


def test_guard_returns_early_without_falling_through_to_writes():
    """The guard's own branch must return before reaching the write code — otherwise the
    guard is a no-op (both branches execute regardless)."""
    body = _get_fundamentals_body()
    guard_start = body.index("if fetch_looks_empty:")
    write_start = body.index("_get_redis().setex(cache_key, _FUND_TTL, data.model_dump_json())")
    guard_block = body[guard_start:write_start]
    assert "return data" in guard_block or "return json.loads(stale)" in guard_block


def test_etf_carveout_exists_and_precedes_the_guard():
    """AUD-FUNDAMENTALS-ETF-FALSEPOSITIVE regression: ETFs (GLD, SPY, sector ETFs)
    legitimately have null market_cap/trailing_pe/total_revenue on a genuinely SUCCESSFUL
    fetch — without a carve-out, the guard above trips on every real ETF fetch, permanently
    disabling caching/persistence for the entire ETF universe. quoteType=="ETF"/"MUTUALFUND"
    or a present totalAssets field (yfinance-populated only for real fund-type responses)
    must be checked and must be part of the `fetch_looks_empty` condition, not a separate
    dead branch."""
    body = _get_fundamentals_body()
    assert '_is_fund_type' in body
    assert 'quoteType' in body
    assert 'totalAssets' in body
    fund_type_pos = body.index("_is_fund_type = ")
    guard_pos = body.index("fetch_looks_empty = (")
    assert fund_type_pos < guard_pos, "_is_fund_type must be computed before fetch_looks_empty uses it"
    guard_end = body.index(")", guard_pos)
    guard_expr = body[guard_pos:guard_end]
    assert "_is_fund_type" in guard_expr, "fetch_looks_empty must actually reference _is_fund_type"
