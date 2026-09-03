"""Regression test for AUD-DECIDE2-BATCHNOFLOOR (Decision-Making deep audit, 2026-09-03):
BatchDecisionRequest had no initial_capital field, so decide_batch() built its inner
DecisionRequest without one, silently falling to DecisionRequest's own 10_000.0 default
regardless of the real portfolio's actual starting capital — a real problem since the T201
equity-floor circuit breaker (hard_rejects.py) computes equity/initial_capital, and any real
portfolio whose initial_capital differs from 10_000 (e.g. any HK portfolio, seeded at 300_000)
got a fabricated, wrong ratio on every batch call.
"""
import pathlib

from src.api.core.models import BatchDecisionRequest, DecisionRequest

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def test_batch_decision_request_has_an_initial_capital_field():
    req = BatchDecisionRequest(symbols=["AAPL"])
    assert hasattr(req, "initial_capital")


def test_default_matches_decision_requests_own_default():
    """Both models must share the same fallback so a caller that omits the field on either
    gets identical, documented behavior."""
    assert BatchDecisionRequest(symbols=["AAPL"]).initial_capital == DecisionRequest().initial_capital


def test_a_real_portfolios_initial_capital_is_accepted_and_preserved():
    req = BatchDecisionRequest(symbols=["AAPL"], initial_capital=300_000.0)
    assert req.initial_capital == 300_000.0


def test_decide_batch_threads_initial_capital_into_the_inner_decision_request():
    """Source-text check: decide_batch() must pass req.initial_capital through to the
    DecisionRequest it constructs, not silently drop it (routes.py can't be imported directly
    in this test environment's conftest.py setup, but IS importable here since decision-engine
    has no Docker-only stubs — this is a belt-and-suspenders structural check regardless)."""
    start = _ROUTES_SOURCE.index("single_req = DecisionRequest(")
    end = _ROUTES_SOURCE.index(")", start)
    construction = _ROUTES_SOURCE[start:end]
    assert "initial_capital=req.initial_capital" in construction
