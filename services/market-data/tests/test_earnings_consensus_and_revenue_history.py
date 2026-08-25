"""Tests for AUD-EARNINGSCONSENSUS.

User ask: "I would like to get the company and market estimate earning data not only the
target stock price" (a follow-up after T249-earnings-calendar-market-estimates only added the
analyst price-target consensus). This adds two genuinely new data sources to get_fundamentals():
  - earnings_consensus: forward-looking market estimates for the NEXT report (yfinance's
    earnings_estimate/revenue_estimate/eps_trend/eps_revisions, one row per period key).
  - revenue_history: past-quarter ACTUAL revenue (yfinance's quarterly_financials, "Total
    Revenue" row) — a separate source from the pre-existing eps_history (actual-vs-estimate).

_consensus_num() is a small, pure, dependency-free function (no DB/yfinance access at all) —
tested directly via source-text exec(), matching test_fundamentals_cache_miss_logging.py's
established _extract_log_helper() technique. get_fundamentals() itself can't be imported
directly in this test environment (conftest.py stubs sqlalchemy/db, and this module does real
query construction at import time) — its wiring is covered via source-text regression checks,
matching test_fundamentals_empty_fetch_guard.py's established pattern for this exact
import-constraint class.
"""
import math
import pathlib

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _get_fundamentals_body() -> str:
    start = _ROUTES_SOURCE.index("def get_fundamentals(")
    end = _ROUTES_SOURCE.index("\n\n\ndef ", start)
    return _ROUTES_SOURCE[start:end]


def _extract_consensus_num():
    body = _get_fundamentals_body()
    start = body.index("def _consensus_num(v):")
    end = body.index("\n\n        _periods", start)
    func_source = body[start:end]
    namespace: dict = {}
    exec(func_source, namespace)  # noqa: S102 — isolated eval of one pure function's real source
    return namespace["_consensus_num"]


# ── _consensus_num() — pure NaN/type-safety helper, tested directly ────────────────────────

def test_real_number_passes_through_unchanged():
    fn = _extract_consensus_num()
    assert fn(2.09161) == 2.09161
    assert fn(41) == 41.0


def test_none_stays_none():
    fn = _extract_consensus_num()
    assert fn(None) is None


def test_real_nan_degrades_to_none_not_a_json_breaking_value():
    """yfinance's own DataFrames can carry real NaN (confirmed live: growth_estimates' own LTG
    row, and earnings_estimate's yearAgoEps for a company with no comparable prior period) —
    json.dumps(float('nan')) is non-standard and rejected by a strict JSON.parse, the same
    class of bug already documented and fixed once in this repo for updown_vol_ratio's
    float('inf') (AUD262/263-era). Must degrade to None, never pass a real NaN through."""
    fn = _extract_consensus_num()
    result = fn(float("nan"))
    assert result is None


def test_non_numeric_string_degrades_to_none_not_a_crash():
    fn = _extract_consensus_num()
    assert fn("N/A") is None


def test_zero_is_preserved_not_treated_as_missing():
    """A real 0 revisions count must stay 0, not be silently coerced to None the way a
    falsy-zero bug elsewhere in this codebase has been caught doing before."""
    fn = _extract_consensus_num()
    assert fn(0) == 0.0
    assert fn(0.0) == 0.0


def test_a_json_round_trip_of_a_nan_result_never_produces_the_literal_infinity_or_nan_token():
    import json
    fn = _extract_consensus_num()
    value = fn(float("nan"))
    serialized = json.dumps({"x": value})
    assert "NaN" not in serialized
    assert "Infinity" not in serialized
    parsed = json.loads(serialized)
    assert parsed["x"] is None


# ── get_fundamentals() wiring: earnings_consensus ──────────────────────────────────────────

def test_only_the_four_priceable_periods_are_kept():
    """"LTG" (long-term growth) has no matching row in earnings_estimate/revenue_estimate at
    all — a period this app can't actually price a concrete EPS/revenue estimate for must be
    dropped, not emitted half-populated."""
    body = _get_fundamentals_body()
    assert '_periods = ("0q", "+1q", "0y", "+1y")' in body


def test_consensus_reads_from_all_four_yfinance_sources():
    body = _get_fundamentals_body()
    assert "ticker.earnings_estimate" in body
    assert "ticker.revenue_estimate" in body
    assert "ticker.eps_trend" in body
    assert "ticker.eps_revisions" in body


def test_earnings_consensus_field_defaults_to_none_not_an_empty_dict():
    """An absent consensus is a genuinely different state from '{}' — a thinly-covered stock
    with zero yfinance consensus data must report None, matching this repo's established
    'never fabricate presence of data that doesn't exist' convention (see
    _prebreakout_calibration_for_band's own docstring for the same principle applied
    elsewhere)."""
    body = _get_fundamentals_body()
    assert "if consensus:" in body
    assert "data.earnings_consensus = consensus" in body


def test_consensus_fetch_is_wrapped_in_its_own_try_except_isolated_from_eps_history():
    """A failure fetching earnings_estimate/revenue_estimate/eps_trend/eps_revisions must
    never prevent the separate, pre-existing eps_history fetch immediately below it from
    running."""
    body = _get_fundamentals_body()
    consensus_start = body.index("ticker.earnings_estimate")
    eps_history_start = body.index("ticker.earnings_history")
    assert consensus_start < eps_history_start
    between = body[consensus_start:eps_history_start]
    # The consensus block's own try/except must close (via `except Exception as exc:` +
    # log.warning) before eps_history's own `try:` begins.
    assert "except Exception as exc:" in between
    assert 'log.warning("fundamentals.earnings_consensus_fetch_failed"' in between


# ── get_fundamentals() wiring: revenue_history ─────────────────────────────────────────────

def test_revenue_history_reads_total_revenue_row_from_quarterly_financials():
    body = _get_fundamentals_body()
    assert "ticker.quarterly_financials" in body
    assert '"Total Revenue" in qf.index' in body
    assert 'qf.loc["Total Revenue"]' in body


def test_revenue_history_is_sorted_oldest_first():
    body = _get_fundamentals_body()
    start = body.index("ticker.quarterly_financials")
    end = body.index("from datetime import datetime as _dt", start)
    block = body[start:end]
    assert ".sort_index()" in block


def test_revenue_history_drops_nan_rows_rather_than_emitting_them():
    body = _get_fundamentals_body()
    start = body.index("ticker.quarterly_financials")
    end = body.index("from datetime import datetime as _dt", start)
    block = body[start:end]
    assert ".dropna()" in block


def test_revenue_history_fetch_is_isolated_in_its_own_try_except():
    body = _get_fundamentals_body()
    start = body.index("ticker.quarterly_financials")
    end = body.index("from datetime import datetime as _dt", start)
    block = body[start:end]
    assert "except Exception as exc:" in block
    assert 'log.warning("fundamentals.revenue_history_fetch_failed"' in block
