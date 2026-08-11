"""Tests for AUD264-REGIME-FAILURE-DEFAULTS-DISAGREE (market-data half).

get_last_regime()/get_last_hk_regime() previously returned a bare {} on a lazy-fetch failure
(empty cache + _fetch_market_regime()/_fetch_hk_market_regime() both raising) — every consumer
resolves a missing "state" key via .get("state", "neutral"), the MOST PERMISSIVE regime (full
size, no gate), exactly when this service has lost visibility into market conditions. Now
defaults to "choppy" (conservative), matching _fetch_market_regime()'s own internal policy for
an ambiguous/failed classification.

Both functions import directly in this test environment (conftest.py's stubbing is sufficient),
so these are real behavioral tests, not source-text extraction.
"""
import src.services.paper_trading_engine as pte


class TestGetLastRegimeFailureDefault:
    def test_returns_choppy_not_neutral_when_cache_empty_and_fetch_fails(self, monkeypatch):
        monkeypatch.setattr(pte, "_regime_cache", {})
        monkeypatch.setattr(pte, "_fetch_market_regime", lambda cfg: (_ for _ in ()).throw(RuntimeError("network down")))
        result = pte.get_last_regime()
        assert result.get("state") == "choppy"
        assert result.get("state") != "neutral"

    def test_still_returns_the_real_cache_when_populated(self, monkeypatch):
        """Regression guard: the fix must not affect the healthy, common path — a populated
        cache is returned verbatim regardless of what state it holds."""
        monkeypatch.setattr(pte, "_regime_cache", {"state": "bull", "vix": 14.0})
        result = pte.get_last_regime()
        assert result.get("state") == "bull"

    def test_still_returns_a_real_fresh_fetch_when_the_fetch_succeeds(self, monkeypatch):
        """Regression guard: the fix only changes the FAILURE path — a successful lazy fetch
        (empty cache, but the fetch itself works) must return its own real result unchanged."""
        monkeypatch.setattr(pte, "_regime_cache", {})
        monkeypatch.setattr(pte, "_fetch_market_regime", lambda cfg: {"state": "risk_off", "vix": 32.0})
        result = pte.get_last_regime()
        assert result.get("state") == "risk_off"


class TestGetLastHkRegimeFailureDefault:
    def test_returns_choppy_not_neutral_when_cache_empty_and_fetch_fails(self, monkeypatch):
        monkeypatch.setattr(pte, "_hk_regime_cache", {})
        monkeypatch.setattr(pte, "_fetch_hk_market_regime", lambda cfg: (_ for _ in ()).throw(RuntimeError("network down")))
        result = pte.get_last_hk_regime()
        assert result.get("state") == "choppy"
        assert result.get("state") != "neutral"

    def test_still_returns_the_real_cache_when_populated(self, monkeypatch):
        monkeypatch.setattr(pte, "_hk_regime_cache", {"state": "bear", "vix": 40.0})
        result = pte.get_last_hk_regime()
        assert result.get("state") == "bear"
