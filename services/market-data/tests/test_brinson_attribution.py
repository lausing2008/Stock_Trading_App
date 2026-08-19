"""Tests for IF-10: brinson_attribution.py — real Brinson sector allocation-vs-selection
decomposition over a paper portfolio's closed trades.

compute_brinson_attribution() and normalize_sector() are both pure (no DB/network
dependency) — the actual yfinance fetch lives in the separate fetch_benchmark_sector_returns()
wrapper, kept thin so the decomposition math stays directly testable against hand-verified
arithmetic, matching fama_french.py's own established split between fetch and compute.
"""
from datetime import date

from src.services.brinson_attribution import (
    compute_brinson_attribution,
    normalize_sector,
    SECTOR_ETF_TICKERS,
)


def _trade(sector, entry_date, exit_date, entry_price, shares, pct_return):
    return {
        "sector": sector, "entry_date": entry_date, "exit_date": exit_date,
        "entry_price": entry_price, "shares": shares, "pct_return": pct_return,
    }


# ── normalize_sector() ────────────────────────────────────────────────────────

def test_normalize_sector_maps_known_alias_to_canonical_name():
    assert normalize_sector("Financial") == "Financial Services"
    assert normalize_sector("Health Care") == "Healthcare"
    assert normalize_sector("Telecommunications") == "Communication Services"


def test_normalize_sector_passes_through_an_already_canonical_name():
    assert normalize_sector("Technology") == "Technology"


def test_normalize_sector_returns_none_for_missing_or_unrecognized():
    assert normalize_sector(None) is None
    assert normalize_sector("") is None
    assert normalize_sector("Not A Real Sector") is None


# ── compute_brinson_attribution() — sample floor ──────────────────────────────

def test_below_min_trades_returns_insufficient_data_not_a_fabricated_result():
    trades = [_trade("Technology", date(2026, 1, 1), date(2026, 1, 10), 100.0, 10, 5.0)]
    result = compute_brinson_attribution(trades, {"Technology": 3.0})
    assert result["insufficient_data"] is True
    assert result["sectors"] == []
    assert result["total_allocation_effect_pct"] is None


def test_rows_with_none_pct_return_are_excluded_from_the_sample_count():
    trades = [_trade("Technology", date(2026, 1, 1), date(2026, 1, 10), 100.0, 10, None)] * 10
    result = compute_brinson_attribution(trades, {"Technology": 3.0})
    assert result["insufficient_data"] is True
    assert result["n_trades"] == 0


# ── compute_brinson_attribution() — hand-verified arithmetic ──────────────────

def test_a_hand_computed_single_sector_case_matches_exact_brinson_formula():
    """5 identical-weight Technology trades, portfolio return 8%, benchmark return 3%.
    With only ONE real sector present, portfolio weight in Technology = 1.0 (100% of capital-
    days), benchmark weight = 1/11 (equal-weight proxy). Hand-verified:
      w_p=1.0, w_b=1/11≈0.0909, r_p=8.0, r_b=3.0
      allocation  = (1.0 - 0.0909) * 3.0  = 2.7273
      selection   = 0.0909 * (8.0 - 3.0)  = 0.4545
      interaction = (1.0 - 0.0909) * 5.0  = 4.5455
    """
    trades = [
        _trade("Technology", date(2026, 1, 1), date(2026, 1, 11), 100.0, 10, 8.0)
        for _ in range(6)
    ]
    result = compute_brinson_attribution(trades, {"Technology": 3.0})
    assert result["insufficient_data"] is False
    tech = next(s for s in result["sectors"] if s["sector"] == "Technology")
    assert tech["portfolio_weight_pct"] == 100.0
    assert abs(tech["benchmark_weight_pct"] - (1 / 11 * 100)) < 0.01
    assert abs(tech["allocation_effect_pct"] - 2.7273) < 0.01
    assert abs(tech["selection_effect_pct"] - 0.4545) < 0.01
    assert abs(tech["interaction_effect_pct"] - 4.5455) < 0.01


def test_portfolio_return_within_sector_is_capital_days_weighted_not_a_naive_mean():
    """A tiny, short-held losing trade and a large, long-held winning trade in the SAME
    sector — the capital-days-weighted average must be pulled toward the larger/longer trade,
    not a plain unweighted average of the two returns."""
    trades = [
        _trade("Technology", date(2026, 1, 1), date(2026, 1, 2), 10.0, 1, -50.0),   # tiny, 1 day
        _trade("Technology", date(2026, 1, 1), date(2026, 3, 1), 1000.0, 100, 10.0),  # huge, ~60 days
        _trade("Technology", date(2026, 1, 1), date(2026, 3, 1), 1000.0, 100, 10.0),
        _trade("Technology", date(2026, 1, 1), date(2026, 3, 1), 1000.0, 100, 10.0),
        _trade("Technology", date(2026, 1, 1), date(2026, 3, 1), 1000.0, 100, 10.0),
        _trade("Technology", date(2026, 1, 1), date(2026, 3, 1), 1000.0, 100, 10.0),
    ]
    result = compute_brinson_attribution(trades, {"Technology": 3.0})
    tech = next(s for s in result["sectors"] if s["sector"] == "Technology")
    # The naive unweighted mean would be (-50 + 5*10)/6 = 0.0 — the real capital-days-weighted
    # value must be MUCH closer to +10 (the huge, long-held trades dominate).
    assert tech["portfolio_return_pct"] > 8.0


# ── compute_brinson_attribution() — unclassified + missing benchmark handling ─

def test_unclassified_sector_is_reported_but_excluded_from_effect_sums():
    trades = [
        _trade(None, date(2026, 1, 1), date(2026, 1, 11), 100.0, 10, 4.0)
        for _ in range(6)
    ]
    result = compute_brinson_attribution(trades, {})
    unclassified = next(s for s in result["sectors"] if s["sector"] == "unclassified")
    assert unclassified["allocation_effect_pct"] is None
    assert unclassified["benchmark_weight_pct"] is None
    assert result["total_allocation_effect_pct"] == 0.0


def test_missing_benchmark_return_for_a_real_sector_excludes_it_from_effects_not_zero():
    """A real, resolvable sector with NO fetched benchmark return (an ETF fetch failure) must
    be excluded from the total effect sums — never silently treated as a 0% benchmark return,
    which would fabricate a positive allocation/selection effect out of missing data."""
    trades = [
        _trade("Energy", date(2026, 1, 1), date(2026, 1, 11), 100.0, 10, 4.0)
        for _ in range(6)
    ]
    result = compute_brinson_attribution(trades, {})  # no Energy return supplied
    energy = next(s for s in result["sectors"] if s["sector"] == "Energy")
    assert energy["allocation_effect_pct"] is None
    assert energy["benchmark_return_pct"] is None
    # A fabricated 0%-benchmark-return version would have produced a nonzero allocation
    # effect here (since w_p != w_b) — confirm the total correctly stays exactly 0.0.
    assert result["total_allocation_effect_pct"] == 0.0


# ── compute_brinson_attribution() — multi-sector real-shape case ──────────────

def test_multi_sector_case_sums_effects_across_all_scoreable_sectors():
    trades = (
        [_trade("Technology", date(2026, 1, 1), date(2026, 1, 11), 100.0, 10, 8.0) for _ in range(3)]
        + [_trade("Healthcare", date(2026, 1, 1), date(2026, 1, 11), 100.0, 10, -2.0) for _ in range(3)]
    )
    result = compute_brinson_attribution(trades, {"Technology": 3.0, "Healthcare": 1.0})
    assert result["insufficient_data"] is False
    assert len(result["sectors"]) == 2
    tech = next(s for s in result["sectors"] if s["sector"] == "Technology")
    health = next(s for s in result["sectors"] if s["sector"] == "Healthcare")
    # Total effects must equal the sum of the two sectors' own effects.
    assert abs(
        result["total_allocation_effect_pct"] - (tech["allocation_effect_pct"] + health["allocation_effect_pct"])
    ) < 0.01


def test_response_always_states_the_equal_weight_benchmark_method_explicitly():
    trades = [_trade("Technology", date(2026, 1, 1), date(2026, 1, 11), 100.0, 10, 8.0) for _ in range(6)]
    result = compute_brinson_attribution(trades, {"Technology": 3.0})
    assert result["benchmark_weight_method"] == "equal_weight_11_spdr_sectors"


def test_all_11_spdr_sectors_are_present_in_the_ticker_map():
    assert len(SECTOR_ETF_TICKERS) == 11
    assert SECTOR_ETF_TICKERS["Technology"] == "XLK"


# ── GET /paper-portfolio/brinson-attribution — source-text regression checks ──
# paper_portfolio.py can't be imported directly in this test environment (its import chain
# needs the real conftest.py stub setup only pytest's own collection provides for db/
# db.models), matching this repo's established pattern for this exact file.

import pathlib as _pathlib

_PAPER_PORTFOLIO_PATH = _pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_PAPER_PORTFOLIO_SOURCE = _PAPER_PORTFOLIO_PATH.read_text()


def _route_body(func_name: str) -> str:
    start = _PAPER_PORTFOLIO_SOURCE.index(f"def {func_name}(")
    end = _PAPER_PORTFOLIO_SOURCE.index("\ndef ", start + 1)
    return _PAPER_PORTFOLIO_SOURCE[start:end]


def test_the_route_is_registered_at_the_documented_path():
    assert '@router.get("/brinson-attribution")' in _PAPER_PORTFOLIO_SOURCE


def test_the_route_reuses_compute_brinson_attribution_not_a_second_derivation():
    body = _route_body("get_brinson_attribution")
    assert "from ..services.brinson_attribution import compute_brinson_attribution" in body
    assert "compute_brinson_attribution(trades_in, benchmark_returns)" in body


def test_the_response_states_the_benchmark_honesty_caveat_directly():
    body = _route_body("get_brinson_attribution")
    assert '"note":' in body
    assert "equal-weight" in body.lower()


def test_the_benchmark_cache_fails_open_to_a_live_fetch_on_any_redis_error():
    body = _route_body("_get_cached_benchmark_sector_returns")
    assert "except Exception:" in body
    assert "fetch_benchmark_sector_returns(start, end)" in body


def test_no_closed_trades_returns_an_explicit_insufficient_data_shape():
    body = _route_body("get_brinson_attribution")
    assert "if not closed:" in body
    assert '"insufficient_data": True' in body
