"""Tests for T234-CONFIG-UNJUSTIFIED-THRESHOLDS's new GET /backtest/drawdown-breaker-sweep
route in paper_portfolio.py. Matches test_backtest_portfolio_route.py's own established
precedent exactly (its sibling /backtest/portfolio route) — this file only guards the route's
own wiring via source-text checks, since paper_portfolio.py can't be imported directly in this
test environment (conftest.py stubs sqlalchemy/db wholesale). The underlying
sweep_max_portfolio_drawdown_pct() function itself is already thoroughly tested in
test_portfolio_backtest.py.
"""
import pathlib

_PP_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_PP_SOURCE = _PP_PATH.read_text()


def _route_body():
    start = _PP_SOURCE.index('@router.get("/backtest/drawdown-breaker-sweep")')
    end = _PP_SOURCE.index("\n\n\n# ── T233-SELFIMPROVE-PHASE3", start)
    return _PP_SOURCE[start:end]


def test_route_is_registered_with_the_expected_path():
    assert '@router.get("/backtest/drawdown-breaker-sweep")' in _PP_SOURCE


def test_route_is_admin_only_matching_its_sibling_backtest_routes():
    body = _route_body()
    assert "Depends(get_admin_user)" in body


def test_route_validates_style_against_the_known_4_values():
    body = _route_body()
    assert '("SHORT", "SWING", "LONG", "GROWTH")' in body


def test_route_validates_market_against_us_or_hk():
    body = _route_body()
    assert '("US", "HK")' in body


def test_route_rejects_an_empty_symbols_list_rather_than_running_a_meaningless_sweep():
    body = _route_body()
    assert "symbol_list" in body
    assert "at least one ticker" in body


def test_route_delegates_to_sweep_max_portfolio_drawdown_pct_not_a_reimplementation():
    body = _route_body()
    assert "from ..backtest.portfolio_backtest import sweep_max_portfolio_drawdown_pct" in body
    assert "sweep_max_portfolio_drawdown_pct(" in body


def test_route_never_writes_to_portfolio_config_or_any_promotion_table():
    """Matches the sibling backtest routes' own documented invariant — this is a read-only
    research tool, never a live config mutation."""
    body = _route_body()
    assert "portfolio.config" not in body
    assert "TuneHistory" not in body
    assert ".commit()" not in body


def test_route_uses_a_longer_default_window_than_the_plain_portfolio_backtest_route():
    """A walk-forward sweep needs enough history for a real 70/30 train/validation split on
    top of the outcome-resolution lag — the plain /backtest/portfolio route's own 180-day
    default would leave too little for a meaningful validation slice, so this route's default
    is deliberately wider (365 days, matching sweep_max_portfolio_drawdown_pct's own module
    docstring reasoning)."""
    body = _route_body()
    assert "window_days: int = Query(365" in body
