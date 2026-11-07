"""Tests for T233-ARCH-PORTFOLIO-CONSOLIDATE's moved portfolio_risk() endpoint.

Moved verbatim from services/market-data/src/api/portfolio.py (same route path /portfolio-risk/
risk, same response shape — zero frontend changes needed). The only real logic change is the
data-fetching layer: market-data queried Price/Stock directly via SQLAlchemy; portfolio-optimizer
has no DB access, so _fetch_returns()/_fetch_stock_meta() now call market-data's own
GET /stocks/{symbol}/prices and GET /stocks/{symbol} over HTTP — the same two endpoints this
service's sibling _fetch_closes() (in routes.py, for /portfolio/optimize) already relies on, so
this isn't a new integration pattern, just applying an existing one. Direct function calls with
monkeypatch on this module's own fetch helpers, matching test_optimize_endpoint.py's established
pattern — fastapi/httpx/pandas/numpy are all real, installed packages in this environment (per
conftest.py's own docstring), so no stub workaround is needed here.
"""
import numpy as np
import pandas as pd
import pytest
from fastapi import HTTPException

from src.api.risk import _beta, portfolio_risk


def _returns_df(symbols, n=90, seed=0):
    rng = np.random.default_rng(seed)
    return pd.DataFrame({s: rng.normal(0.0005, 0.01, n) for s in symbols})


def test_rejects_fewer_than_two_symbols():
    with pytest.raises(HTTPException) as exc:
        portfolio_risk(symbols="AAPL", weights=None, _user="testuser")
    assert exc.value.status_code == 400


def test_rejects_more_than_ten_symbols():
    syms = ",".join(f"SYM{i}" for i in range(11))
    with pytest.raises(HTTPException) as exc:
        portfolio_risk(symbols=syms, weights=None, _user="testuser")
    assert exc.value.status_code == 400


def test_rejects_mismatched_weights_count():
    with pytest.raises(HTTPException) as exc:
        portfolio_risk(symbols="AAPL,MSFT", weights="0.5", _user="testuser")
    assert exc.value.status_code == 400


def test_returns_insufficient_history_error_when_fetch_yields_too_few_symbols(monkeypatch):
    import src.api.risk as risk_mod
    monkeypatch.setattr(risk_mod, "_fetch_returns", lambda symbols, days=60: pd.DataFrame({"AAPL": [0.01] * 30}))
    monkeypatch.setattr(risk_mod, "_fetch_stock_meta", lambda symbols: {})
    with pytest.raises(HTTPException) as exc:
        portfolio_risk(symbols="AAPL,MSFT", weights=None, _user="testuser")
    assert exc.value.status_code == 422


def test_computes_correlation_betas_and_sector_weights_end_to_end(monkeypatch):
    import src.api.risk as risk_mod
    syms = ["AAPL", "MSFT"]
    monkeypatch.setattr(risk_mod, "_fetch_returns", lambda symbols, days=60: _returns_df(syms))
    monkeypatch.setattr(risk_mod, "_fetch_stock_meta", lambda symbols: {
        "AAPL": {"sector": "Technology", "market": "US"},
        "MSFT": {"sector": "Technology", "market": "US"},
    })
    monkeypatch.setattr(risk_mod, "yf", type("FakeYf", (), {
        "download": staticmethod(lambda *a, **kw: pd.DataFrame({"Close": np.linspace(100, 110, 90)}))
    }))

    result = portfolio_risk(symbols="AAPL,MSFT", weights=None, _user="testuser")

    assert set(result["symbols"]) == set(syms)
    assert len(result["correlation"]) == 2
    assert set(result["betas"].keys()) == set(syms)
    assert result["sector_weights"] == {"Technology": 1.0}
    assert result["benchmark"] == "SPY"  # no HK symbols -> US benchmark
    assert "var_95_pct" in result


def test_uses_hsi_benchmark_when_majority_of_symbols_are_hk(monkeypatch):
    import src.api.risk as risk_mod
    syms = ["0700.HK", "9988.HK", "AAPL"]
    monkeypatch.setattr(risk_mod, "_fetch_returns", lambda symbols, days=60: _returns_df(syms))
    monkeypatch.setattr(risk_mod, "_fetch_stock_meta", lambda symbols: {
        "0700.HK": {"sector": "Tech", "market": "HK"},
        "9988.HK": {"sector": "Tech", "market": "HK"},
        "AAPL": {"sector": "Tech", "market": "US"},
    })
    monkeypatch.setattr(risk_mod, "yf", type("FakeYf", (), {
        "download": staticmethod(lambda *a, **kw: pd.DataFrame({"Close": np.linspace(100, 110, 90)}))
    }))

    result = portfolio_risk(symbols=",".join(syms), weights=None, _user="testuser")
    assert result["benchmark"] == "^HSI"


def test_flags_high_correlation_and_concentration_warnings(monkeypatch):
    import src.api.risk as risk_mod
    syms = ["A", "B"]
    # Perfectly correlated series -> corr == 1.0, triggers the >0.8 warning.
    base = np.random.default_rng(0).normal(0.0005, 0.01, 90)
    monkeypatch.setattr(risk_mod, "_fetch_returns", lambda symbols, days=60: pd.DataFrame({"A": base, "B": base}))
    monkeypatch.setattr(risk_mod, "_fetch_stock_meta", lambda symbols: {
        "A": {"sector": "Tech", "market": "US"}, "B": {"sector": "Tech", "market": "US"},
    })
    monkeypatch.setattr(risk_mod, "yf", type("FakeYf", (), {
        "download": staticmethod(lambda *a, **kw: pd.DataFrame({"Close": np.linspace(100, 110, 90)}))
    }))

    result = portfolio_risk(symbols="A,B", weights=None, _user="testuser")
    assert any("High correlation" in w for w in result["warnings"])
    assert any("100% concentration in Tech" in w for w in result["warnings"])


def test_falls_back_to_beta_one_when_benchmark_fetch_fails(monkeypatch):
    import src.api.risk as risk_mod
    syms = ["AAPL", "MSFT"]
    monkeypatch.setattr(risk_mod, "_fetch_returns", lambda symbols, days=60: _returns_df(syms))
    monkeypatch.setattr(risk_mod, "_fetch_stock_meta", lambda symbols: {
        "AAPL": {"sector": "Tech", "market": "US"}, "MSFT": {"sector": "Tech", "market": "US"},
    })

    def _raise(*a, **kw):
        raise RuntimeError("yfinance down")
    monkeypatch.setattr(risk_mod, "yf", type("FakeYf", (), {"download": staticmethod(_raise)}))

    result = portfolio_risk(symbols="AAPL,MSFT", weights=None, _user="testuser")
    assert all(b == 1.0 for b in result["betas"].values())
    assert result["portfolio_beta"] == 1.0


def test_response_includes_the_historical_var_block(monkeypatch):
    """IF-01: portfolio_risk() must now also surface historical_var alongside the pre-existing
    parametric var_95_pct, not replace it."""
    import src.api.risk as risk_mod
    syms = ["AAPL", "MSFT"]
    monkeypatch.setattr(risk_mod, "_fetch_returns", lambda symbols, days=60: _returns_df(syms, n=90))
    monkeypatch.setattr(risk_mod, "_fetch_stock_meta", lambda symbols: {
        "AAPL": {"sector": "Technology", "market": "US"},
        "MSFT": {"sector": "Technology", "market": "US"},
    })
    monkeypatch.setattr(risk_mod, "yf", type("FakeYf", (), {
        "download": staticmethod(lambda *a, **kw: pd.DataFrame({"Close": np.linspace(100, 110, 90)}))
    }))

    result = portfolio_risk(symbols="AAPL,MSFT", weights=None, _user="testuser")

    assert "var_95_pct" in result  # pre-existing parametric field, unchanged
    assert "historical_var" in result
    assert result["historical_var"]["insufficient_data"] is False
    assert result["historical_var"]["var_95_1d_pct"] is not None
    assert result["historical_var"]["cvar_99_10d_pct"] is not None


# ── AUD292-SHARPE-VAREPS's own sibling gap: _beta() used a bare `var > 0` guard ─────────────
# paper_portfolio.py's Sharpe/Sortino computation (services/market-data) already had this
# exact bug — a near-zero-but-nonzero variance from floating-point noise (not an exact 0.0)
# can pass a bare `> 0` check and explode the resulting ratio. _beta() sits in the same file
# as that fix's own module docstring header (IF-01) but was never updated with the same real
# epsilon threshold convention until now.

def test_beta_all_identical_but_nonzero_benchmark_returns_falls_back_to_neutral_one():
    """A benchmark return series recomputed via a deliberately-perturbed target rate (matching
    the exact fixture construction test_sharpe_variance_epsilon.py in market-data's own test
    suite uses for this identical bug class) — genuinely nonzero but sub-epsilon variance must
    fall back to the neutral beta=1.0, not explode via a bare `var > 0` gate."""
    base_rate = 0.001
    bench = pd.Series([base_rate + i * 1e-17 for i in range(10)])
    stock = pd.Series([0.02, -0.01, 0.03, -0.02, 0.015, -0.01, 0.025, -0.015, 0.01, -0.005])
    result = _beta(stock, bench)
    assert result == 1.0, f"expected neutral fallback for float-noise variance, got {result}"


def test_beta_genuine_variance_still_produces_a_real_finite_value():
    """The fix must not break the normal case — real, meaningfully-varying benchmark returns
    must still produce a real, non-fallback beta."""
    bench = pd.Series([0.02, -0.01, 0.015, -0.02, 0.01, -0.015, 0.025, -0.01, 0.02, -0.005])
    stock = pd.Series([0.04, -0.02, 0.03, -0.04, 0.02, -0.03, 0.05, -0.02, 0.04, -0.01])
    result = _beta(stock, bench)
    assert result != 1.0
    assert 0.5 < result < 3.0  # a roughly 2x-levered relationship by construction


def test_beta_fewer_than_five_common_dates_still_returns_neutral_regardless_of_the_fix():
    """The pre-existing len(s) < 5 floor is unrelated to and unaffected by the epsilon fix —
    confirms this fix didn't accidentally change that separate guard's own behavior."""
    bench = pd.Series([0.02, -0.01, 0.03])
    stock = pd.Series([0.04, -0.02, 0.05])
    assert _beta(stock, bench) == 1.0


def test_beta_uses_a_real_epsilon_not_a_hardcoded_literal_in_source():
    import pathlib
    risk_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "risk.py"
    source = risk_path.read_text()
    assert "_BETA_VAR_EPS = 1e-9" in source
    assert "var > _BETA_VAR_EPS" in source


# ── I06 (platform audit 2026-11-06, P05): signed weights, coverage, weight-based benchmark ───

import src.api.risk as risk_mod  # noqa: E402
from src.api.risk import portfolio_stress_test  # noqa: E402


def _stub_book(monkeypatch, history_syms, markets):
    """Stub market-data and yfinance: only `history_syms` have price history."""
    monkeypatch.setattr(risk_mod, "_fetch_returns", lambda symbols, days=60: _returns_df(history_syms))
    monkeypatch.setattr(risk_mod, "_fetch_stock_meta", lambda symbols: {
        s: {"sector": "Tech", "market": markets[s]} for s in symbols
    })
    downloaded: list[str] = []

    def _download(ticker, *a, **kw):
        downloaded.append(ticker)
        return pd.DataFrame({"Close": np.linspace(100, 110, 90)})
    monkeypatch.setattr(risk_mod, "yf", type("FakeYf", (), {"download": staticmethod(_download)}))
    return downloaded


@pytest.mark.parametrize("endpoint", ["risk", "stress"])
def test_negative_weight_is_rejected_not_counted_as_long(endpoint):
    """A -30 hedge used to become +30 via abs(), turning a hedge into added long exposure."""
    with pytest.raises(HTTPException) as exc:
        if endpoint == "risk":
            portfolio_risk(symbols="AAPL,SPY", weights="100,-30", _user="t")
        else:
            portfolio_stress_test(symbols="AAPL,SPY", weights="100,-30", scenario="covid_2020", _user="t")
    assert exc.value.status_code == 400
    assert "SPY" in exc.value.detail and "long-only" in exc.value.detail


def test_non_numeric_weight_is_a_400_not_a_server_error():
    with pytest.raises(HTTPException) as exc:
        portfolio_risk(symbols="AAPL,MSFT", weights="50,abc", _user="t")
    assert exc.value.status_code == 400


@pytest.mark.parametrize("weights", ["0,0", "nan,1", "inf,1"])
def test_degenerate_weights_are_rejected(weights):
    with pytest.raises(HTTPException) as exc:
        portfolio_risk(symbols="AAPL,MSFT", weights=weights, _user="t")
    assert exc.value.status_code == 400


def test_duplicate_symbols_are_rejected_instead_of_dropping_a_weight():
    with pytest.raises(HTTPException) as exc:
        portfolio_risk(symbols="AAPL,MSFT,AAPL", weights="10,10,80", _user="t")
    assert exc.value.status_code == 400
    assert "AAPL" in exc.value.detail


def test_missing_history_is_disclosed_with_its_weight(monkeypatch):
    _stub_book(monkeypatch, ["AAPL", "MSFT"], {"AAPL": "US", "MSFT": "US", "NEWCO": "US"})
    result = portfolio_risk(symbols="AAPL,MSFT,NEWCO", weights="25,25,50", _user="t")

    assert result["coverage"] == {
        "requested_symbols": ["AAPL", "MSFT", "NEWCO"],
        "missing_symbols": ["NEWCO"],
        "covered_weight_pct": 50.0,
    }
    assert result["weights"] == pytest.approx([0.5, 0.5])  # covered symbols, rescaled
    assert any("NEWCO" in w and "50%" in w for w in result["warnings"])


def test_full_coverage_reports_100_pct_and_no_coverage_warning(monkeypatch):
    _stub_book(monkeypatch, ["AAPL", "MSFT"], {"AAPL": "US", "MSFT": "US"})
    result = portfolio_risk(symbols="AAPL,MSFT", weights=None, _user="t")
    assert result["coverage"]["covered_weight_pct"] == 100.0
    assert result["coverage"]["missing_symbols"] == []
    assert not any("price history" in w for w in result["warnings"])


def test_all_requested_weight_on_symbols_without_history_is_a_422(monkeypatch):
    _stub_book(monkeypatch, ["AAPL", "MSFT"], {"AAPL": "US", "MSFT": "US", "NEWCO": "US"})
    with pytest.raises(HTTPException) as exc:
        portfolio_risk(symbols="AAPL,MSFT,NEWCO", weights="0,0,100", _user="t")
    assert exc.value.status_code == 422


def test_benchmark_follows_hk_weight_not_symbol_count(monkeypatch):
    """The audit's example: 90% in one HK name, 10% across two US names. Count rule -> SPY."""
    markets = {"0700.HK": "HK", "AAPL": "US", "MSFT": "US"}
    downloaded = _stub_book(monkeypatch, list(markets), markets)
    result = portfolio_risk(symbols="0700.HK,AAPL,MSFT", weights="90,5,5", _user="t")
    assert result["benchmark"] == "^HSI"
    assert downloaded == ["^HSI"]  # betas were measured against the chosen benchmark
    assert result["benchmark_basis"]["hk_weight_pct"] == 90.0


def test_mostly_us_weight_uses_spy_even_with_a_majority_of_hk_symbols(monkeypatch):
    markets = {"0700.HK": "HK", "9988.HK": "HK", "AAPL": "US"}
    _stub_book(monkeypatch, list(markets), markets)
    result = portfolio_risk(symbols="0700.HK,9988.HK,AAPL", weights="10,10,80", _user="t")
    assert result["benchmark"] == "SPY"


@pytest.mark.parametrize("n_hk,n_us,expected", [(2, 2, "SPY"), (3, 3, "SPY"), (2, 1, "^HSI"), (3, 2, "^HSI")])
def test_equal_weights_keep_the_previous_count_majority_result(monkeypatch, n_hk, n_us, expected):
    """Unweighted calls must pick the same benchmark as before; ties stay SPY, including 3-of-6,
    where float sums of 1/6 can land a hair above 0.5."""
    markets = {**{f"{i}.HK": "HK" for i in range(n_hk)}, **{f"US{i}": "US" for i in range(n_us)}}
    _stub_book(monkeypatch, list(markets), markets)
    result = portfolio_risk(symbols=",".join(markets), weights=None, _user="t")
    assert result["benchmark"] == expected


def test_stress_test_carries_the_same_coverage_and_benchmark(monkeypatch):
    markets = {"0700.HK": "HK", "AAPL": "US", "MSFT": "US", "NEWCO": "US"}
    _stub_book(monkeypatch, ["0700.HK", "AAPL", "MSFT"], markets)
    result = portfolio_stress_test(symbols="0700.HK,AAPL,MSFT,NEWCO", weights="80,5,5,10",
                                   scenario="covid_2020", _user="t")
    assert result["benchmark"] == "^HSI"
    assert result["coverage"]["missing_symbols"] == ["NEWCO"]
    assert result["coverage"]["covered_weight_pct"] == 90.0
    assert "NEWCO" in result["coverage_warning"]
