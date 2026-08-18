"""Tests for T230-BACKTESTING-MULTISYMBOL's new GET /backtest/portfolio route in
paper_portfolio.py. Neither sibling route (/backtest/min-entry-score, /backtest/extended-gate)
has its own dedicated route test in this repo — both are admin-only manual research tools whose
underlying walk_forward_* functions are already thoroughly tested elsewhere, matching this
route's own precedent (run_portfolio_backtest() has 21 dedicated tests in
test_portfolio_backtest.py). This file only guards the route's own wiring via source-text
checks, since paper_portfolio.py can't be imported directly in this test environment
(conftest.py stubs sqlalchemy/db wholesale).
"""
import pathlib

_PP_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_PP_SOURCE = _PP_PATH.read_text()


def _route_body():
    start = _PP_SOURCE.index('@router.get("/backtest/portfolio")')
    end = _PP_SOURCE.index("\n\n\n# ── T233-SELFIMPROVE-PHASE3", start)
    return _PP_SOURCE[start:end]


def test_route_is_registered_with_the_expected_path():
    assert '@router.get("/backtest/portfolio")' in _PP_SOURCE


def test_route_is_admin_only_matching_its_two_sibling_backtest_routes():
    body = _route_body()
    assert "Depends(get_admin_user)" in body


def test_route_validates_style_against_the_known_4_values():
    body = _route_body()
    assert '("SHORT", "SWING", "LONG", "GROWTH")' in body


def test_route_validates_market_against_us_or_hk():
    body = _route_body()
    assert '("US", "HK")' in body


def test_route_rejects_an_empty_symbols_list_rather_than_running_a_meaningless_backtest():
    body = _route_body()
    assert "symbol_list" in body
    assert "at least one ticker" in body


def test_route_delegates_to_run_portfolio_backtest_not_a_reimplementation():
    body = _route_body()
    assert "from ..backtest.portfolio_backtest import run_portfolio_backtest" in body
    assert "run_portfolio_backtest(" in body


def test_route_never_writes_to_portfolio_config_or_any_promotion_table():
    """Matches the sibling backtest routes' own documented invariant — this is a read-only
    research tool, never a live config mutation."""
    body = _route_body()
    assert "portfolio.config" not in body
    assert "TuneHistory" not in body
    assert ".commit()" not in body
