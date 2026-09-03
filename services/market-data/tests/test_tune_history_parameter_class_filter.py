"""Test for T255-STRATEGY-TUNER-DASHBOARD's parameter_class filter added to GET /tune-history
in paper_portfolio.py. User asked for a dashboard/report on the weekly self-tuning run's
threshold changes and reasons, per horizon — old_value/new_value/gate_failures were already
being fetched by the frontend but never rendered, and the endpoint had no way to isolate
tune_strategy's own rows (parameter_class='joint_strategy') from every other calibration
mechanism's rows. Matches test_backtest_drawdown_sweep_route.py's own established
source-text-extraction convention — paper_portfolio.py can't be imported directly in this test
environment (conftest.py stubs sqlalchemy/db wholesale).
"""
import pathlib

_PP_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "paper_portfolio.py"
_PP_SOURCE = _PP_PATH.read_text()


def _route_body():
    start = _PP_SOURCE.index('@router.get("/tune-history")')
    end = _PP_SOURCE.index("\n\n\n", start)
    return _PP_SOURCE[start:end]


def test_route_accepts_a_parameter_class_query_param():
    body = _route_body()
    assert "parameter_class: str | None = Query(" in body


def test_route_filters_on_parameter_class_when_provided():
    body = _route_body()
    assert "TuneHistory.parameter_class == parameter_class" in body


def test_parameter_class_filter_is_exact_match_not_upper_cased():
    """Regression guard: style/market are upper()-cased before filtering (they're a fixed
    4/2-value vocabulary), but parameter_class is a free-form string written verbatim by each
    mechanism (e.g. "joint_strategy") — must NOT be upper()-cased or it would never match."""
    body = _route_body()
    assert "parameter_class.upper()" not in body


def test_existing_style_and_market_filters_still_present():
    """Regression guard: adding the new filter must not remove the 2 pre-existing ones."""
    body = _route_body()
    assert "TuneHistory.style == style.upper()" in body
    assert "TuneHistory.market == market.upper()" in body
