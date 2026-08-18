"""Tests for T232-DL-DUALSCORER-DEBT item #23 — GET /stocks/entry-weights.

Background: _should_enter()'s own calibrated logistic-regression weights (PT-3,
/data/models/entry_weights.json, loaded via _load_entry_weights()) previously had no HTTP
exposure at all — only the fallback engine itself could read them. decision-engine's
/decide/{symbol} verdict had no access to this calibration data, so it always used the plain
additive score>=min_score comparison even for a portfolio whose real fallback gate had already
moved on to the calibrated model (>=100 closed trades). This endpoint exposes the identical
dict _should_enter() itself reads, so decision-engine's own aget_entry_weights() (aggregator.py)
can fetch it and apply the same calibrated-probability check.

routes.py can't be imported directly in this test environment (its import chain pulls in
common.config/db, neither for-real-installed here) — tested via source-text extraction, same
technique as test_min_kscore_config_wiring.py / test_min_ta_score_config_wiring.py.
"""
import pathlib

_routes_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "routes.py"
_routes_source = _routes_path.read_text()


def _entry_weights_route_body() -> str:
    start = _routes_source.index('@router.get("/entry-weights")')
    end = _routes_source.index("\n\n\n", start)
    return _routes_source[start:end]


_route_body = _entry_weights_route_body()


def test_route_is_registered_at_the_expected_path():
    assert '@router.get("/entry-weights")' in _routes_source


def test_route_delegates_to_load_entry_weights_not_a_reimplementation():
    """Must call the REAL _load_entry_weights() (the same function _should_enter() itself
    calls) — never a second, independently-reimplemented read of the calibration file, which
    could silently drift from what the fallback engine actually uses."""
    assert "_load_entry_weights" in _route_body
    assert "from ..services.paper_trading_engine import _load_entry_weights" in _route_body


def test_route_returns_the_function_result_directly_not_a_wrapped_shape():
    """The response must be the bare weights dict (or {} when uncalibrated) — decision-engine's
    aget_entry_weights() expects to read weights["intercept"]/weights["n_trades"] etc. directly
    off the top-level JSON body, not nested under a wrapper key."""
    assert "return _load_entry_weights()" in _route_body
