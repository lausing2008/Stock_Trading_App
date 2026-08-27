"""Tests for T234-CONFIG-UNJUSTIFIED-THRESHOLDS Group A's new GET /backtest/scorer-sweep route
in paper_portfolio.py. Matches test_backtest_open_risk_cap_sweep_route.py's own established
precedent exactly — this file only guards the route's own wiring via source-text checks, since
paper_portfolio.py can't be imported directly in this test environment (conftest.py stubs
sqlalchemy/db wholesale). The underlying walk_forward_scorer_sweep() function itself is already
thoroughly tested in test_walk_forward_scorer_sweep.py.
"""
import pathlib

_PP_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_PP_SOURCE = _PP_PATH.read_text()


def _route_body():
    start = _PP_SOURCE.index('@router.get("/backtest/scorer-sweep")')
    end = _PP_SOURCE.index("\n\n\n# ── T233-SELFIMPROVE-PHASE3", start)
    return _PP_SOURCE[start:end]


def test_route_is_registered_with_the_expected_path():
    assert '@router.get("/backtest/scorer-sweep")' in _PP_SOURCE


def test_route_is_admin_only_matching_its_sibling_backtest_routes():
    body = _route_body()
    assert "Depends(get_admin_user)" in body


def test_route_validates_style_against_the_known_4_values():
    body = _route_body()
    assert '("SHORT", "SWING", "LONG", "GROWTH")' in body


def test_route_validates_market_against_us_or_hk():
    body = _route_body()
    assert '("US", "HK")' in body


def test_route_delegates_to_walk_forward_scorer_sweep_not_a_reimplementation():
    body = _route_body()
    assert "from ..backtest.gate_harness import walk_forward_scorer_sweep" in body
    assert "walk_forward_scorer_sweep(session, style, market, base_cfg, window_start, window_end)" in body


def test_route_builds_base_cfg_from_the_real_default_config_and_style_overrides():
    """Must reuse _DEFAULT_CONFIG/_STYLE_OVERRIDES — the SAME real live-trading defaults every
    other sweep route builds its baseline cfg from, not a hand-picked/hardcoded dict."""
    body = _route_body()
    assert "from ..services.paper_trading_engine import _DEFAULT_CONFIG, _STYLE_OVERRIDES" in body
    assert 'base_cfg = {**_DEFAULT_CONFIG, **_STYLE_OVERRIDES.get(style, {})}' in body


def test_route_never_writes_to_portfolio_config_or_any_promotion_table():
    """Matches the sibling backtest routes' own documented invariant — this is a read-only
    research tool, never a live config mutation."""
    body = _route_body()
    assert "portfolio.config" not in body
    assert "TuneHistory" not in body
    assert ".commit()" not in body


def test_route_uses_the_same_longer_default_window_as_its_sibling_sweeps():
    """A walk-forward sweep needs enough history for a real 70/30 train/validation split on top
    of the outcome-resolution lag — matching the drawdown/risk-per-trade/open-risk sweeps' own
    365-day default, not the plain /backtest/portfolio route's shorter 180-day one."""
    body = _route_body()
    assert "window_days: int = Query(365" in body


def test_route_has_no_symbols_param_unlike_its_portfolio_scoped_siblings():
    """walk_forward_scorer_sweep() operates on ALL resolved BUY signals for a style/market
    (matching replay_should_enter()'s own signature), never a symbol-scoped subset the way the
    portfolio-level sweeps (drawdown-breaker, open-risk-cap) require."""
    body = _route_body()
    assert "symbols:" not in body
    assert "symbol_list" not in body
