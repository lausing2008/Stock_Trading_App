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

from src.api.risk import portfolio_risk


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
