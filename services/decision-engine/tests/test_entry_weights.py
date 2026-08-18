"""Tests for T232-DL-DUALSCORER-DEBT item #23.

Background: paper_trading_engine.py's _should_enter() (the DE-outage fallback gate) abandons
the plain additive score>=min_entry_score comparison entirely once a portfolio has >=100 closed
trades (PT-3) — it instead fits a calibrated logistic-regression win-probability model
(intercept + w_rr*rr + w_confidence*confidence + w_score*score + w_kscore*kscore, sigmoid,
compared against a calibrated threshold). decision-engine's own /decide/{symbol} verdict had no
equivalent — it always used the plain score>=min_score comparison, even for a portfolio whose
fallback gate had already moved past that comparison entirely. A real divergence for exactly
the portfolios most worth trusting (100+ real closed trades' worth of calibration).

Fix: a new aget_entry_weights()/_get_entry_weights() pair in aggregator.py (mirroring
aget_entry_gate_params()'s exact fetch-cache-fallback shape) fetches the SAME weights dict
_should_enter() itself reads via _load_entry_weights(), from a new market-data endpoint
(GET /stocks/entry-weights). routes.py's _decide() applies the identical calibrated-probability
formula when weights.get("n_trades", 0) >= 100 — the exact same gate _should_enter() checks —
falling back to the pre-existing plain score>=min_score comparison otherwise (a young portfolio,
or a market-data fetch failure, which fails open to {}).
"""
import sys
from unittest.mock import MagicMock

sys.modules.setdefault("common", MagicMock())
sys.modules.setdefault("common.config", MagicMock())
sys.modules.setdefault("common.jwt_auth", MagicMock())

import src.api.core.aggregator as aggregator  # noqa: E402


def _reset_entry_weights_cache():
    aggregator._ENTRY_WEIGHTS_CACHE = None
    aggregator._ENTRY_WEIGHTS_TS = 0.0


class TestGetEntryWeightsFetchCacheFallback:
    def test_fetches_from_market_data_on_cache_miss(self, monkeypatch):
        _reset_entry_weights_cache()
        mock_response = MagicMock()
        mock_response.json.return_value = {"intercept": -1.0, "n_trades": 150}
        mock_response.raise_for_status = lambda: None
        mock_get = MagicMock(return_value=mock_response)
        monkeypatch.setattr(aggregator.httpx, "get", mock_get)

        result = aggregator._get_entry_weights()

        assert result == {"intercept": -1.0, "n_trades": 150}
        mock_get.assert_called_once()
        args, _ = mock_get.call_args
        assert "entry-weights" in args[0]

    def test_warm_cache_skips_the_http_call_entirely(self, monkeypatch):
        _reset_entry_weights_cache()
        mock_response = MagicMock()
        mock_response.json.return_value = {"intercept": -1.0}
        mock_response.raise_for_status = lambda: None
        mock_get = MagicMock(return_value=mock_response)
        monkeypatch.setattr(aggregator.httpx, "get", mock_get)

        aggregator._get_entry_weights()
        aggregator._get_entry_weights()

        assert mock_get.call_count == 1

    def test_fetch_failure_with_no_prior_cache_falls_back_to_empty_dict(self, monkeypatch):
        """An empty dict is the SAFE degrade here — the caller's own n_trades>=100 gate
        correctly treats {} as "no calibration data, use the plain threshold" — matching
        _load_entry_weights()'s own no-file sentinel exactly."""
        _reset_entry_weights_cache()
        monkeypatch.setattr(aggregator.httpx, "get", MagicMock(side_effect=Exception("connection refused")))

        result = aggregator._get_entry_weights()

        assert result == {}

    def test_fetch_failure_after_a_prior_success_returns_the_stale_cached_value_not_empty(self, monkeypatch):
        """Fail-open should prefer a stale-but-real cached value over discarding it entirely —
        matches _get_entry_gate_params()'s own identical precedent."""
        _reset_entry_weights_cache()
        good_response = MagicMock()
        good_response.raise_for_status = lambda: None
        good_response.json.return_value = {"intercept": -1.0, "n_trades": 150}
        monkeypatch.setattr(aggregator.httpx, "get", MagicMock(return_value=good_response))
        aggregator._get_entry_weights()

        aggregator._ENTRY_WEIGHTS_TS = 0.0  # force cache to look expired
        monkeypatch.setattr(aggregator.httpx, "get", MagicMock(side_effect=Exception("timeout")))

        result = aggregator._get_entry_weights()
        assert result == {"intercept": -1.0, "n_trades": 150}


class TestAsyncWrapperUsesTheDedicatedExecutorNotTheSharedEventLoop:
    def test_aget_entry_weights_is_a_coroutine_function(self):
        import inspect
        assert inspect.iscoroutinefunction(aggregator.aget_entry_weights)

    def test_aget_entry_weights_source_uses_run_in_executor(self):
        import inspect
        src = inspect.getsource(aggregator.aget_entry_weights)
        assert "run_in_executor" in src
        assert "_game_plan_executor" in src


# ── routes.py's _decide() wiring — source-text regression checks ──────────────────────────
#
# Matches test_entry_gate_params.py's own established precedent: _decide() is too heavy to
# exercise end-to-end here (fetch_all/aget_regime/etc. fan-out), so these guard the exact SHAPE
# of the fix instead — the fetch happens, the >=100-trade gate mirrors _should_enter()'s own,
# and the formula matches _should_enter()'s verbatim.

sys.modules.setdefault("fastapi", MagicMock())
import src.api.routes as decide_routes  # noqa: E402
import inspect as _inspect  # noqa: E402

_DECIDE_SOURCE = _inspect.getsource(decide_routes._decide)


def test_decide_fetches_entry_weights():
    assert "aget_entry_weights()" in _DECIDE_SOURCE


def test_decide_gates_on_the_same_n_trades_threshold_should_enter_uses():
    """_should_enter() gates its own calibrated-logistic bypass on n_trades >= 100 — the port
    must use the identical threshold, not a different one that would silently diverge from
    when the fallback gate itself switches to calibrated scoring."""
    assert '_entry_weights.get("n_trades", 0) >= 100' in _DECIDE_SOURCE


def test_decide_requires_intercept_present_before_using_calibrated_path():
    assert '_entry_weights.get("intercept") is not None' in _DECIDE_SOURCE


def test_decide_calibrated_formula_matches_should_enter_verbatim():
    """The logit formula (intercept + w_rr*rr + w_confidence*confidence + w_score*score +
    w_kscore*kscore) must use the identical weight keys _should_enter() reads from the same
    weights dict, or the two engines would silently score the exact same candidate
    differently even when both are using "the calibrated model." """
    for key in ("w_rr", "w_confidence", "w_score", "w_kscore"):
        assert f'_entry_weights["{key}"]' in _DECIDE_SOURCE


def test_decide_caps_rr_at_8_matching_should_enter():
    assert "min(_rr, 8.0)" in _DECIDE_SOURCE


def test_decide_defaults_threshold_to_052_matching_should_enter():
    assert '_entry_weights.get("threshold", 0.52)' in _DECIDE_SOURCE


def test_decide_defaults_kscore_to_50_when_absent_matching_should_enter():
    """_should_enter() uses `kscore if kscore is not None else 50.0` — a missing kscore in the
    calibrated formula must degrade to the same neutral 50.0, not 0 (which would look like a
    terrible K-Score and wrongly tank the calibrated probability for every candidate with no
    K-Score data at all)."""
    assert "else 50.0" in _DECIDE_SOURCE


def test_aget_entry_weights_is_imported_in_routes():
    assert hasattr(decide_routes, "aget_entry_weights")
