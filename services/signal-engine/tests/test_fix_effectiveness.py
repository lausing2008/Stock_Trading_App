"""Tests for T325-FIXEFFECTIVENESS — "did this fix actually work" tracking, direct user
request (2026-09-02) after the AI Signal deep audit: "I would like to have a dashboard to show
the performance after we applied the fix so that we can compare later."

fix_effectiveness.py can't be imported directly in this test environment (fastapi/
common.jwt_auth aren't stubbed by conftest.py) — matching test_delisted_loss_scoring.py's own
established convention: source-text extraction for route-wiring/structural checks, plus a real
in-memory-SQLite model to directly exercise _compute_ai_signal_win_rate_metrics()'s real query
logic (extracted via exec(), the same technique test_eval_outcomes_first_fire_snapshot.py and
test_fetch_ml_data_falsy_zero_auc.py already use for isolating one function's source from a
file that can't be imported as a whole).
"""
import importlib.util
import pathlib
import sys
import textwrap
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_fix_eff", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_fix_eff"] = _models
_spec.loader.exec_module(_models)

SignalOutcome = _models.SignalOutcome
SignalHorizon = _models.SignalHorizon
Stock = _models.Stock
Market = _models.Market
Exchange = _models.Exchange
Base = _models.Base

_FIX_EFF_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "fix_effectiveness.py"
_FIX_EFF_SOURCE = _FIX_EFF_PATH.read_text()


# ── Route wiring / structural checks (source-text extraction) ──────────────────────────

def test_router_has_the_expected_prefix():
    assert 'router = APIRouter(prefix="/fix-effectiveness"' in _FIX_EFF_SOURCE


def test_register_endpoint_rejects_a_duplicate_fix_id():
    assert "already registered" in _FIX_EFF_SOURCE
    assert "409" in _FIX_EFF_SOURCE


def test_snapshot_endpoint_404s_for_an_unregistered_fix_id():
    assert "No FixRecord registered for fix_id" in _FIX_EFF_SOURCE
    assert "404" in _FIX_EFF_SOURCE


def test_snapshot_endpoint_rejects_a_domain_with_no_metric_function_yet():
    """The dispatch must explicitly refuse an unimplemented domain rather than silently
    producing a meaningless/empty snapshot."""
    assert 'if record.domain != "ai_signal":' in _FIX_EFF_SOURCE
    assert "No snapshot metric function implemented yet for domain" in _FIX_EFF_SOURCE


def test_main_py_registers_the_router_before_the_catch_all_router():
    main_path = pathlib.Path(__file__).resolve().parents[1] / "src" / "main.py"
    main_source = main_path.read_text()
    fix_eff_idx = main_source.index("fix_effectiveness_router")
    routers_list_idx = main_source.index("routers=[")
    router_bare_idx = main_source.index(", router]", routers_list_idx)
    assert fix_eff_idx < router_bare_idx


# ── _compute_ai_signal_win_rate_metrics() — real query logic, real in-memory SQLite ─────

def _extract_compute_metrics_func():
    start = _FIX_EFF_SOURCE.index("def _compute_ai_signal_win_rate_metrics(")
    end = _FIX_EFF_SOURCE.index("\n@router.get", start)
    return textwrap.dedent(_FIX_EFF_SOURCE[start:end])


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[Stock.__table__, SignalOutcome.__table__])
    return Session(engine)


def _run_compute(session, since=None):
    namespace = {
        "select": __import__("sqlalchemy").select,
        "func": __import__("sqlalchemy").func,
        "SignalOutcome": SignalOutcome,
        "Session": Session,
        "date": date,
    }
    exec(_extract_compute_metrics_func(), namespace)  # noqa: S102 — isolated eval of real source
    return namespace["_compute_ai_signal_win_rate_metrics"](session, since=since)


def _add_stock(session, stock_id, market=None):
    session.add(Stock(id=stock_id, symbol=f"SYM{stock_id}", name=f"Stock {stock_id}",
                        market=market or Market.US, exchange=Exchange.NASDAQ, active=True, delisted=False))


_next_id = [0]


def _add_outcome(session, *, stock_id, horizon, direction, signal_date, is_correct_5d, return_5d, is_correct=None, pct_return=None):
    # SQLite doesn't auto-increment a BigInteger PK the same way it does a plain Integer PK —
    # assign both id and signal_id explicitly rather than relying on autoincrement.
    _next_id[0] += 1
    session.add(SignalOutcome(
        id=_next_id[0], signal_id=1000 + _next_id[0],
        stock_id=stock_id, symbol=f"SYM{stock_id}", horizon=horizon, signal_direction=direction,
        signal_date=signal_date, confidence=50.0,
        is_correct_5d=is_correct_5d, return_5d=return_5d,
        is_correct=is_correct, pct_return=pct_return,
    ))


def test_computes_win_rate_and_avg_return_per_horizon_and_direction():
    session = _make_session()
    _add_stock(session, 1)
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SWING, direction="BUY",
                  signal_date=date(2026, 8, 1), is_correct_5d=True, return_5d=0.02)
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SWING, direction="BUY",
                  signal_date=date(2026, 8, 2), is_correct_5d=False, return_5d=-0.03)
    session.commit()

    result = _run_compute(session)
    bucket = result["by_bucket"]["SWING|BUY"]
    assert bucket["resolved_5d"] == 2
    assert bucket["win_rate_5d"] == 0.5
    assert bucket["avg_return_5d_pct"] == -0.5  # avg(0.02, -0.03) * 100 = -0.5


def test_separates_buckets_by_both_horizon_and_direction():
    session = _make_session()
    _add_stock(session, 1)
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SWING, direction="BUY",
                  signal_date=date(2026, 8, 1), is_correct_5d=True, return_5d=0.05)
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SHORT, direction="SELL",
                  signal_date=date(2026, 8, 1), is_correct_5d=True, return_5d=-0.01)
    session.commit()

    result = _run_compute(session)
    assert "SWING|BUY" in result["by_bucket"]
    assert "SHORT|SELL" in result["by_bucket"]
    assert result["by_bucket"]["SWING|BUY"]["resolved_5d"] == 1
    assert result["by_bucket"]["SHORT|SELL"]["resolved_5d"] == 1


def test_unresolved_rows_are_excluded_from_win_rate_but_counted_in_total():
    session = _make_session()
    _add_stock(session, 1)
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SWING, direction="BUY",
                  signal_date=date(2026, 8, 1), is_correct_5d=None, return_5d=None)
    session.commit()

    result = _run_compute(session)
    bucket = result["by_bucket"]["SWING|BUY"]
    assert bucket["total"] == 1
    assert bucket["resolved_5d"] == 0
    assert bucket["win_rate_5d"] is None


def test_since_filter_excludes_rows_before_the_given_date():
    """The critical property for a real before/after comparison: a snapshot computed with
    `since=<fix date>` must never silently blend pre-fix and post-fix rows into one figure,
    which would understate any real improvement by diluting it with the exact biased
    population the fix corrected."""
    session = _make_session()
    _add_stock(session, 1)
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SWING, direction="BUY",
                  signal_date=date(2026, 8, 1), is_correct_5d=False, return_5d=-0.05)  # pre-fix, bad
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SWING, direction="BUY",
                  signal_date=date(2026, 9, 5), is_correct_5d=True, return_5d=0.03)  # post-fix, good
    session.commit()

    result_all = _run_compute(session)
    result_since = _run_compute(session, since=date(2026, 9, 1))
    assert result_all["by_bucket"]["SWING|BUY"]["resolved_5d"] == 2
    assert result_since["by_bucket"]["SWING|BUY"]["resolved_5d"] == 1
    assert result_since["by_bucket"]["SWING|BUY"]["win_rate_5d"] == 1.0


def test_base_is_correct_window_computed_alongside_the_5d_window():
    session = _make_session()
    _add_stock(session, 1)
    _add_outcome(session, stock_id=1, horizon=SignalHorizon.SWING, direction="BUY",
                  signal_date=date(2026, 8, 1), is_correct_5d=True, return_5d=0.02,
                  is_correct=False, pct_return=-0.04)
    session.commit()

    result = _run_compute(session)
    bucket = result["by_bucket"]["SWING|BUY"]
    assert bucket["resolved_base"] == 1
    assert bucket["win_rate_base"] == 0.0
    assert bucket["avg_pct_return_base"] == -4.0
