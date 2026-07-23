"""Tests for T234-CONFIG-DECIDE-DEFAULT-MISMATCH.

Background: decision-engine's own _DEFAULT_CFG (routes.py) hardcoded min_confidence=62.0 — a
value disconnected from what a real portfolio would actually use (paper_trading_engine.py's
real style/market matrix: SWING=50, LONG=40, GROWTH=45/SHORT=45 in the US, all raised to 65 in
HK). A caller going through the real trading path (_call_decision_engine()) always sends the
real value explicitly via config_overrides, so this never mattered there — but decide.tsx's
standalone GET /decide/{symbol}/explain sends no config_overrides at all, silently using the
disconnected 62.0 literal instead.

Fix: a new aget_entry_gate_params()/_get_entry_gate_params() pair in aggregator.py (mirroring
_get_style_params()'s exact fetch-cache-fallback shape) fetches the REAL resolved values from
a new market-data endpoint, and routes.py's _decide() fills them into cfg as defaults —
never overriding an explicit config_overrides value.
"""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())
sys.modules.setdefault("common.jwt_auth", MagicMock())

import src.api.core.aggregator as aggregator  # noqa: E402


def _reset_entry_gate_cache():
    aggregator._ENTRY_GATE_CACHE.clear()
    aggregator._ENTRY_GATE_TS.clear()


class TestGetEntryGateParamsFetchCacheFallback:
    def test_fetches_from_market_data_on_cache_miss(self, monkeypatch):
        _reset_entry_gate_cache()
        mock_response = MagicMock()
        mock_response.json.return_value = {"min_confidence": 50.0, "min_kscore": 52.0}
        mock_response.raise_for_status = lambda: None
        mock_get = MagicMock(return_value=mock_response)
        monkeypatch.setattr(aggregator.httpx, "get", mock_get)

        result = aggregator._get_entry_gate_params("SWING", "US")

        assert result == {"min_confidence": 50.0, "min_kscore": 52.0}
        mock_get.assert_called_once()
        _, kwargs = mock_get.call_args
        assert kwargs["params"] == {"style": "SWING", "market": "US"}

    def test_warm_cache_skips_the_http_call_entirely(self, monkeypatch):
        _reset_entry_gate_cache()
        mock_response = MagicMock()
        mock_response.json.return_value = {"min_confidence": 40.0}
        mock_response.raise_for_status = lambda: None
        mock_get = MagicMock(return_value=mock_response)
        monkeypatch.setattr(aggregator.httpx, "get", mock_get)

        aggregator._get_entry_gate_params("LONG", "US")
        aggregator._get_entry_gate_params("LONG", "US")

        assert mock_get.call_count == 1

    def test_different_style_market_pairs_are_cached_independently(self, monkeypatch):
        """A cache keyed only on style (or only on market) would silently return SWING/US's
        values for SWING/HK — the two genuinely differ (min_confidence 50 vs 65)."""
        _reset_entry_gate_cache()

        def _fake_get(url, params, timeout):
            resp = MagicMock()
            resp.raise_for_status = lambda: None
            resp.json.return_value = {"min_confidence": 65.0 if params["market"] == "HK" else 50.0}
            return resp

        monkeypatch.setattr(aggregator.httpx, "get", _fake_get)

        us_result = aggregator._get_entry_gate_params("SWING", "US")
        hk_result = aggregator._get_entry_gate_params("SWING", "HK")

        assert us_result["min_confidence"] == 50.0
        assert hk_result["min_confidence"] == 65.0

    def test_fetch_failure_falls_back_to_the_hardcoded_fallback_dict(self, monkeypatch):
        _reset_entry_gate_cache()
        monkeypatch.setattr(aggregator.httpx, "get", MagicMock(side_effect=Exception("connection refused")))

        result = aggregator._get_entry_gate_params("SWING", "US")

        assert result == aggregator._ENTRY_GATE_FALLBACK

    def test_fetch_failure_after_a_prior_success_returns_the_stale_cached_value_not_the_fallback(self, monkeypatch):
        """Fail-open should prefer a stale-but-real cached value over the generic fallback dict
        when one exists — matches _get_style_params()'s own identical precedent."""
        _reset_entry_gate_cache()
        good_response = MagicMock()
        good_response.raise_for_status = lambda: None
        good_response.json.return_value = {"min_confidence": 50.0}
        monkeypatch.setattr(aggregator.httpx, "get", MagicMock(return_value=good_response))
        aggregator._get_entry_gate_params("SWING", "US")

        # Force cache to look expired, then fail the next fetch
        aggregator._ENTRY_GATE_TS[("SWING", "US")] = 0.0
        monkeypatch.setattr(aggregator.httpx, "get", MagicMock(side_effect=Exception("timeout")))

        result = aggregator._get_entry_gate_params("SWING", "US")
        assert result == {"min_confidence": 50.0}  # stale cache, not _ENTRY_GATE_FALLBACK


class TestAsyncWrapperUsesTheDedicatedExecutorNotTheSharedEventLoop:
    """Matches the established T247-DECISIONENGINE-STYLEPARAMS-BLOCKING discipline: a blocking
    httpx.get() on a cache miss must never run directly on the shared event loop."""

    def test_aget_entry_gate_params_is_a_coroutine_function(self):
        import inspect
        assert inspect.iscoroutinefunction(aggregator.aget_entry_gate_params)

    def test_aget_entry_gate_params_source_uses_run_in_executor(self):
        import inspect
        src = inspect.getsource(aggregator.aget_entry_gate_params)
        assert "run_in_executor" in src
        assert "_game_plan_executor" in src


class TestFallbackDictHasAllFiveGateKeys:
    def test_fallback_has_every_key_the_real_resolver_returns(self):
        expected_keys = {"min_confidence", "min_kscore", "min_entry_score", "min_ta_score", "min_rr_ratio"}
        assert set(aggregator._ENTRY_GATE_FALLBACK.keys()) == expected_keys


# ── routes.py's _decide() wiring — source-text regression checks ──────────────────────────
#
# _decide() is a large async function with a heavy fan-out (fetch_all/aget_regime/etc.) that
# would need extensive mocking to exercise behaviorally end-to-end — no existing test in this
# file does that (matches this codebase's established practice elsewhere of source-text
# regression checks for functions too heavy to fully exercise, e.g. market-data's
# test_min_kscore_config_wiring.py). These guard the exact SHAPE of the fix: the fetch happens,
# it's applied only when the caller didn't already override the key, and it happens before
# check_hard_rejects() consumes cfg.

sys.modules.setdefault("fastapi", MagicMock())
import src.api.routes as decide_routes  # noqa: E402
import inspect as _inspect  # noqa: E402

_DECIDE_SOURCE = _inspect.getsource(decide_routes._decide)


def test_decide_fetches_real_entry_gate_defaults():
    assert "aget_entry_gate_params(style, market)" in _DECIDE_SOURCE


def test_decide_only_fills_in_keys_the_caller_did_not_already_override():
    """config_overrides must always win — the fetched defaults may only fill gaps."""
    assert 'if _k not in req.config_overrides and _v is not None:' in _DECIDE_SOURCE


def test_decide_resolves_entry_gate_defaults_before_calling_check_hard_rejects():
    fetch_idx = _DECIDE_SOURCE.index("aget_entry_gate_params(style, market)")
    hard_reject_idx = _DECIDE_SOURCE.index("check_hard_rejects(")
    assert fetch_idx < hard_reject_idx


def test_decide_resolves_entry_gate_defaults_after_market_is_finalized():
    """market can be auto-upgraded from US to HK based on the symbol's .HK suffix — the gate
    fetch must use the FINAL resolved market, not the raw req.market, or a .HK symbol would
    silently get US-style gate defaults instead of the correct HK-adjusted ones."""
    market_resolve_idx = _DECIDE_SOURCE.index('market = "HK"')
    fetch_idx = _DECIDE_SOURCE.index("aget_entry_gate_params(style, market)")
    assert market_resolve_idx < fetch_idx


def test_aget_entry_gate_params_is_imported_in_routes():
    assert "aget_entry_gate_params" in decide_routes.__dict__ or hasattr(decide_routes, "aget_entry_gate_params")
