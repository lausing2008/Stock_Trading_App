"""Regression test for T247-RESEARCHENGINE-CACHEKEY.

generate_research()'s cache (_cache, keyed only by symbol) previously served a cached report
to ANY request for that symbol regardless of portfolio_size/max_risk_pct — a report generated
for one user's $100k/2% inputs was returned verbatim to a different request with $500k/1%
inputs, with the WRONG position_sizing block (dollar_risk, share_quantity, position_size,
pct_of_portfolio) baked in.

_position_sizing_matches() is the fix: gate the cache-hit on whether the cached report's own
stored portfolio_size/max_risk_pct (written by _position_size()) match the current request's.
"""
from types import SimpleNamespace

from src.api.routes import _position_sizing_matches


def _req(portfolio_size=100_000.0, max_risk_pct=2.0):
    """A ResearchRequest is a pydantic BaseModel (stubbed in conftest.py) — _position_sizing_
    matches() only reads .portfolio_size/.max_risk_pct attributes, so a plain object with
    those two attributes is a faithful stand-in without needing the real pydantic model."""
    return SimpleNamespace(portfolio_size=portfolio_size, max_risk_pct=max_risk_pct)


def _report(portfolio_size=100_000.0, max_risk_pct=2.0):
    return {"position_sizing": {"portfolio_size": portfolio_size, "max_risk_pct": max_risk_pct}}


def test_matching_portfolio_params_returns_true():
    report = _report(portfolio_size=100_000.0, max_risk_pct=2.0)
    req = _req(portfolio_size=100_000.0, max_risk_pct=2.0)
    assert _position_sizing_matches(report, req) is True


def test_different_portfolio_size_returns_false():
    """The exact bug scenario: User A generated with $100k, User B requests with $500k."""
    report = _report(portfolio_size=100_000.0, max_risk_pct=2.0)
    req = _req(portfolio_size=500_000.0, max_risk_pct=2.0)
    assert _position_sizing_matches(report, req) is False


def test_different_max_risk_pct_returns_false():
    report = _report(portfolio_size=100_000.0, max_risk_pct=2.0)
    req = _req(portfolio_size=100_000.0, max_risk_pct=1.0)
    assert _position_sizing_matches(report, req) is False


def test_both_different_returns_false():
    report = _report(portfolio_size=100_000.0, max_risk_pct=2.0)
    req = _req(portfolio_size=500_000.0, max_risk_pct=1.0)
    assert _position_sizing_matches(report, req) is False


def test_missing_position_sizing_key_returns_false():
    """A malformed/legacy cached report with no position_sizing block at all must not match
    (fail closed — regenerate rather than risk serving mismatched numbers)."""
    report = {}
    req = _req()
    assert _position_sizing_matches(report, req) is False


def test_position_sizing_present_but_none_returns_false():
    report = {"position_sizing": None}
    req = _req()
    assert _position_sizing_matches(report, req) is False
