"""Tests for IF-01: compute_var_cvar() and run_stress_test() (risk.py) — the historical
VaR/CVaR upgrade alongside the pre-existing parametric VaR, and the 5 predefined stress
scenarios.

Both are pure functions with no DB/HTTP dependency, matching test_portfolio_risk.py's own
established convention for this module's pure helper functions (fastapi/pandas/numpy are all
real, installed packages in this environment per conftest.py's own docstring, so no stub
workaround is needed).
"""
import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from src.api.risk import (
    compute_var_cvar,
    run_stress_test,
    STRESS_SCENARIOS,
    _VAR_CONFIDENCES,
    _VAR_HORIZONS_DAYS,
    portfolio_stress_test,
    list_stress_scenarios,
)


def _returns_df(symbols, n=90, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({s: rng.normal(0.0005, 0.01, n) for s in symbols})


# ── compute_var_cvar() ───────────────────────────────────────────────────────────────────────

def test_returns_none_values_below_the_20_sample_floor():
    rets = pd.Series([0.01, -0.02, 0.005, -0.01] * 4)  # 16 samples, under the floor
    result = compute_var_cvar(rets)
    assert result["insufficient_data"] is True
    assert result["sample_size"] == 16
    for conf in _VAR_CONFIDENCES:
        for h in _VAR_HORIZONS_DAYS:
            assert result[f"var_{int(conf*100)}_{h}d_pct"] is None
            assert result[f"cvar_{int(conf*100)}_{h}d_pct"] is None


def test_computes_real_values_at_exactly_the_20_sample_floor():
    rng = np.random.default_rng(1)
    rets = pd.Series(rng.normal(0.0, 0.02, 20))
    result = compute_var_cvar(rets)
    assert result["insufficient_data"] is False
    assert result["sample_size"] == 20
    assert result["var_95_1d_pct"] is not None


def test_var_is_expressed_as_a_positive_percentage_representing_a_loss():
    """A losses-only return series must produce a positive VaR figure (representing the
    magnitude of the loss), never a negative number — matching the sign convention of the
    pre-existing parametric var_95_pct field."""
    rets = pd.Series([-0.03] * 25)  # a uniformly bad month
    result = compute_var_cvar(rets)
    assert result["var_95_1d_pct"] > 0
    assert result["cvar_95_1d_pct"] > 0


def test_cvar_is_strictly_more_severe_than_var_when_the_tail_has_real_spread():
    """CVaR averages the tail BEYOND the VaR threshold — with a genuinely fat/skewed tail (a
    handful of much-worse-than-typical losses), it must be STRICTLY more severe than VaR alone,
    not merely equal to it (equal would mean the "average of the tail" computation degenerated
    into just re-reporting the VaR point itself, defeating the whole purpose of CVaR)."""
    rng = np.random.default_rng(3)
    normal_days = rng.normal(0.0, 0.008, 90)
    fat_tail_days = np.array([-0.15, -0.12, -0.10, -0.09, -0.08])  # a few much-worse-than-typical days
    rets = pd.Series(np.concatenate([normal_days, fat_tail_days]))
    result = compute_var_cvar(rets)
    for conf in _VAR_CONFIDENCES:
        for h in _VAR_HORIZONS_DAYS:
            assert result[f"cvar_{int(conf*100)}_{h}d_pct"] > result[f"var_{int(conf*100)}_{h}d_pct"]


def test_cvar_is_never_less_severe_than_var_even_in_the_degenerate_equal_case():
    """A weaker, always-true invariant (>=, not >) that must hold in EVERY case, including a
    degenerate one where the tail beyond VaR collapses to a single point equal to VaR itself."""
    rng = np.random.default_rng(4)
    rets = pd.Series(rng.normal(0.0, 0.015, 100))
    result = compute_var_cvar(rets)
    for conf in _VAR_CONFIDENCES:
        for h in _VAR_HORIZONS_DAYS:
            assert result[f"cvar_{int(conf*100)}_{h}d_pct"] >= result[f"var_{int(conf*100)}_{h}d_pct"]


def test_99_percent_confidence_is_at_least_as_severe_as_95_percent():
    """The 99% VaR (a rarer, worse-case threshold) must show a loss magnitude >= the 95% VaR
    at the same horizon — a stricter confidence level can never look SAFER."""
    rng = np.random.default_rng(4)
    rets = pd.Series(rng.normal(0.0, 0.02, 100))
    result = compute_var_cvar(rets)
    for h in _VAR_HORIZONS_DAYS:
        assert result[f"var_99_{h}d_pct"] >= result[f"var_95_{h}d_pct"]


def test_10_day_horizon_scales_up_from_1_day_via_sqrt_time():
    """Hand-verified: the 10-day figure must equal the 1-day figure * sqrt(10), the documented
    scaling convention — not a coincidentally-similar number."""
    rng = np.random.default_rng(5)
    rets = pd.Series(rng.normal(0.0, 0.01, 60))
    result = compute_var_cvar(rets)
    expected_10d = round(result["var_95_1d_pct"] * (10 ** 0.5), 2)
    assert abs(result["var_95_10d_pct"] - expected_10d) < 0.02  # rounding tolerance


def test_dropna_excludes_nan_rows_from_the_sample_count():
    rng = np.random.default_rng(6)
    real = pd.Series(rng.normal(0.0, 0.01, 25))
    rets = pd.concat([real, pd.Series([np.nan] * 10)])
    result = compute_var_cvar(rets)
    assert result["sample_size"] == 25


# ── run_stress_test() ────────────────────────────────────────────────────────────────────────

def test_raises_on_an_unknown_scenario_key():
    with pytest.raises(ValueError):
        run_stress_test({"AAPL": 1.0}, {"AAPL": 1.0}, "not_a_real_scenario")


def test_all_5_documented_scenarios_are_present():
    assert len(STRESS_SCENARIOS) == 5
    for key in ("gfc_2008", "covid_2020", "rate_shock_2022", "flash_crash_2010", "stagflation_1973"):
        assert key in STRESS_SCENARIOS


def test_a_beta_of_exactly_1_reproduces_the_benchmark_move_exactly():
    """A position with beta=1.0 must show a per-position impact EXACTLY equal to the
    scenario's own benchmark_move_pct — the simplest possible correctness check."""
    result = run_stress_test({"SPY_PROXY": 1.0}, {"SPY_PROXY": 1.0}, "covid_2020")
    assert result["per_position_impact_pct"]["SPY_PROXY"] == STRESS_SCENARIOS["covid_2020"]["benchmark_move_pct"]
    assert result["portfolio_impact_pct"] == STRESS_SCENARIOS["covid_2020"]["benchmark_move_pct"]


def test_a_higher_beta_position_shows_a_larger_magnitude_impact_than_a_lower_beta_one():
    betas = {"HIGH_BETA": 2.0, "LOW_BETA": 0.5}
    weights = {"HIGH_BETA": 0.5, "LOW_BETA": 0.5}
    result = run_stress_test(betas, weights, "gfc_2008")
    assert abs(result["per_position_impact_pct"]["HIGH_BETA"]) > abs(result["per_position_impact_pct"]["LOW_BETA"])


def test_portfolio_impact_is_the_weighted_sum_of_per_position_impacts():
    """Hand-verified: 60% weight at beta=1.5, 40% weight at beta=0.8, scenario move -25%.
    per_position: 1.5*-25=-37.5, 0.8*-25=-20.0. weighted: 0.6*-37.5 + 0.4*-20.0 = -22.5 -8.0 = -30.5."""
    betas = {"A": 1.5, "B": 0.8}
    weights = {"A": 0.6, "B": 0.4}
    result = run_stress_test(betas, weights, "rate_shock_2022")
    assert result["portfolio_impact_pct"] == -30.5


def test_a_symbol_missing_from_betas_falls_back_to_a_neutral_beta_of_1_0():
    """A symbol with no computed beta (e.g. a benchmark fetch failure elsewhere) must fall back
    to beta=1.0 — matching portfolio_risk()'s own established fallback for the same case —
    rather than crashing with a KeyError."""
    result = run_stress_test({}, {"UNKNOWN_SYM": 1.0}, "flash_crash_2010")
    assert result["per_position_impact_pct"]["UNKNOWN_SYM"] == STRESS_SCENARIOS["flash_crash_2010"]["benchmark_move_pct"]


# ── GET /portfolio-risk/stress-test — endpoint-level tests, matching test_portfolio_risk.py's
# established monkeypatch pattern (fastapi/pandas/numpy/httpx are real, installed packages here) ─

def test_stress_test_endpoint_rejects_an_unknown_scenario():
    with pytest.raises(HTTPException) as exc:
        portfolio_stress_test(symbols="AAPL,MSFT", weights=None, scenario="not_a_real_one", _user="testuser")
    assert exc.value.status_code == 400


def test_stress_test_endpoint_rejects_fewer_than_two_symbols():
    with pytest.raises(HTTPException) as exc:
        portfolio_stress_test(symbols="AAPL", weights=None, scenario="covid_2020", _user="testuser")
    assert exc.value.status_code == 400


def _fake_benchmark_prices(n=90, seed=42):
    """A REALISTIC noisy benchmark price series, not a deterministic linear ramp — a linspace
    ramp has near-zero return variance (std ~3e-5), which sends _beta()'s cov/var computation
    into an absurdly amplified (600+) beta against real noisy stock returns. Real production
    benchmark data always has genuine variance, so the fixture needs it too to exercise
    run_stress_test()'s scaling realistically instead of an unreachable degenerate case."""
    rng = np.random.default_rng(seed)
    rets = rng.normal(0.0005, 0.01, n)
    prices = 100 * np.cumprod(1 + rets)
    return pd.DataFrame({"Close": prices})


def test_stress_test_endpoint_computes_a_real_result_end_to_end(monkeypatch):
    import src.api.risk as risk_mod
    syms = ["AAPL", "MSFT"]
    monkeypatch.setattr(risk_mod, "_fetch_returns", lambda symbols, days=60: _returns_df(syms))
    monkeypatch.setattr(risk_mod, "_fetch_stock_meta", lambda symbols: {
        "AAPL": {"sector": "Technology", "market": "US"},
        "MSFT": {"sector": "Technology", "market": "US"},
    })
    monkeypatch.setattr(risk_mod, "yf", type("FakeYf", (), {
        "download": staticmethod(lambda *a, **kw: _fake_benchmark_prices())
    }))

    result = portfolio_stress_test(symbols="AAPL,MSFT", weights=None, scenario="covid_2020", _user="testuser")

    assert result["scenario"] == "covid_2020"
    assert set(result["symbols"]) == set(syms)
    assert "portfolio_impact_pct" in result
    # With independent random noise for stock/benchmark returns, a computed beta can legitimately
    # come out small and either sign — the exact sign/scaling correctness is already covered by
    # the dedicated hand-computed run_stress_test() unit tests above. This end-to-end test only
    # confirms the wiring reaches a real, bounded (not runaway) result — matching the class of
    # bug actually caught here (a near-zero-variance benchmark fixture producing a 650% blowup).
    assert -100.0 <= result["portfolio_impact_pct"] <= 100.0
    assert set(result["per_position_impact_pct"].keys()) == set(syms)


def test_stress_test_endpoint_falls_back_to_beta_one_when_benchmark_fetch_fails(monkeypatch):
    import src.api.risk as risk_mod
    syms = ["AAPL", "MSFT"]
    monkeypatch.setattr(risk_mod, "_fetch_returns", lambda symbols, days=60: _returns_df(syms))
    monkeypatch.setattr(risk_mod, "_fetch_stock_meta", lambda symbols: {
        "AAPL": {"sector": "Tech", "market": "US"}, "MSFT": {"sector": "Tech", "market": "US"},
    })

    def _raise(*a, **kw):
        raise RuntimeError("yfinance down")
    monkeypatch.setattr(risk_mod, "yf", type("FakeYf", (), {"download": staticmethod(_raise)}))

    result = portfolio_stress_test(symbols="AAPL,MSFT", weights=None, scenario="gfc_2008", _user="testuser")
    # every beta falls back to 1.0, so the per-position impact must exactly equal the scenario's own move
    assert all(v == STRESS_SCENARIOS["gfc_2008"]["benchmark_move_pct"] for v in result["per_position_impact_pct"].values())


def test_list_scenarios_endpoint_returns_all_5_with_labels_and_moves():
    result = list_stress_scenarios(_user="testuser")
    assert len(result) == 5
    for key, v in result.items():
        assert "label" in v and "benchmark_move_pct" in v
