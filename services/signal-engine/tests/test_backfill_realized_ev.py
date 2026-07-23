"""Tests for SELFIMPROVE-NO-RETRO-FEEDBACK-LOOP's backfill_realized_ev()/_retro_ev_for().

TuneHistory rows record what a calibration mechanism PREDICTED a promoted change would do
(validation_ev_pct) — nothing previously checked whether it ACTUALLY helped in subsequent
real trading. backfill_realized_ev() closes that loop: for promoted rows old enough that
real SignalOutcome data has accumulated since the change, it computes the same win_rate/
ev_pct formula every other calibration mechanism uses and writes it to
realized_ev_pct_after.

routes.py can't be imported directly in this environment (conftest.py stubs the `common`
package wholesale, which routes.py's own `from common.jwt_auth import get_current_username`
needs for real) — so _retro_ev_for()'s source is extracted directly from the real file and
exec()'d against real sqlalchemy + the real shared/db/models.py, matching the source-text-
extraction technique test_price_alert_price_check.py (market-data) uses for the same class
of import constraint. This tests the ACTUAL function under test, not a hand-copied
re-implementation that could silently drift from it (the exact gap that let an earlier
EMA-formula bug this session ship un-caught until a real-pandas cross-check was added).
"""
import importlib.util
import pathlib
import sys
from datetime import date, timedelta

from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test"] = _models
_spec.loader.exec_module(_models)

SignalOutcome = _models.SignalOutcome
TuneHistory = _models.TuneHistory
Stock = _models.Stock
Base = _models.Base
SignalHorizon = _models.SignalHorizon
Market = _models.Market
Exchange = _models.Exchange

_ROUTES_PATH = pathlib.Path(__file__).resolve().parents[1] / "src" / "api" / "outcomes.py"
_ROUTES_SOURCE = _ROUTES_PATH.read_text()


def _extract_retro_ev_for():
    """Pulls _retro_ev_for()'s real source out of routes.py and exec()s it against real
    sqlalchemy/models, so this test exercises the actual function under test rather than a
    hand-copied duplicate that could silently drift from it."""
    start = _ROUTES_SOURCE.index("def _retro_ev_for(")
    end = _ROUTES_SOURCE.index('@router.post("/backfill_realized_ev")', start)
    func_source = _ROUTES_SOURCE[start:end]
    namespace = {
        "select": select,
        "SignalOutcome": SignalOutcome,
        "SignalHorizon": SignalHorizon,
        "Stock": Stock,
        "Session": Session,
        "_RETRO_MIN_SAMPLES": 50,
    }
    exec(func_source, namespace)  # noqa: S102 — isolated eval of one pure function's real source
    return namespace["_retro_ev_for"]


_retro_ev_for = _extract_retro_ev_for()


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[SignalOutcome.__table__, TuneHistory.__table__, Stock.__table__])
    return Session(engine)


def _make_outcome(i, horizon, entry_date, is_correct, pct_return, stock_id=1):
    return SignalOutcome(
        id=i, signal_id=i, stock_id=stock_id, symbol="TEST", horizon=horizon,
        signal_direction="BUY", signal_date=entry_date - timedelta(days=1),
        confidence=50.0, entry_date=entry_date, is_correct=is_correct, pct_return=pct_return,
    )


# ── source-text check: the constant and endpoint really exist in routes.py ────

def test_retro_min_samples_matches_the_apps_own_established_statistical_floor():
    """min_samples=50 is calibrate_ta_weights' own documented floor elsewhere in this file —
    guards against someone loosening this without noticing it breaks consistency."""
    assert "_RETRO_MIN_SAMPLES = 50" in _ROUTES_SOURCE


def test_backfill_endpoint_is_registered():
    assert '@router.post("/backfill_realized_ev")' in _ROUTES_SOURCE
    assert "def backfill_realized_ev(" in _ROUTES_SOURCE


def test_backfill_only_touches_promoted_rows_with_null_realized_ev():
    """The exact safety property that makes re-running this endpoint safe: a row must only
    be considered a candidate once (promoted=True AND realized_ev_pct_after IS NULL) — an
    already-checked row should never be silently re-touched with a different verdict."""
    start = _ROUTES_SOURCE.index("def backfill_realized_ev(")
    # T233-ARCH-INSERVICE-SPLITS: this function now lives in outcomes.py, where it's
    # immediately followed by the next route's decorator rather than the "T223" confidence-
    # calibration comment block (that block moved to signals_shared.py in the same split).
    end = _ROUTES_SOURCE.index('@router.get("/accuracy")', start)
    body = _ROUTES_SOURCE[start:end]
    assert "TuneHistory.promoted.is_(True)" in body
    assert "TuneHistory.realized_ev_pct_after.is_(None)" in body


# ── behavioral checks against the real, extracted _retro_ev_for() ─────────────

def test_returns_none_below_the_min_sample_floor():
    session = _make_session()
    for i in range(49):
        session.add(_make_outcome(i, "SWING", date(2026, 7, 1), True, 0.05))
    session.commit()
    assert _retro_ev_for(session, "SWING", "ALL", date(2026, 6, 1)) is None


def test_computes_win_rate_and_ev_once_the_floor_is_cleared():
    session = _make_session()
    for i in range(30):
        session.add(_make_outcome(i, "SWING", date(2026, 7, 1), True, 0.05))
    for i in range(30, 50):
        session.add(_make_outcome(i, "SWING", date(2026, 7, 1), False, -0.03))
    session.commit()
    result = _retro_ev_for(session, "SWING", "ALL", date(2026, 6, 1))
    assert result is not None
    assert result["n"] == 50
    assert result["win_rate"] == 0.6
    expected_ev = ((30 * 0.05 + 20 * -0.03) / 50) * 100
    assert abs(result["ev_pct"] - round(expected_ev, 2)) < 0.01


def test_excludes_outcomes_before_the_since_date():
    session = _make_session()
    for i in range(60):
        session.add(_make_outcome(i, "SWING", date(2026, 5, 1), True, 0.05))  # before since
    session.commit()
    assert _retro_ev_for(session, "SWING", "ALL", date(2026, 6, 1)) is None


def test_filters_by_stock_market_when_market_is_not_all():
    session = _make_session()
    session.add(Stock(id=1, symbol="US1", market=Market.US, exchange=Exchange.NASDAQ, name="US Co"))
    session.add(Stock(id=2, symbol="HK1", market=Market.HK, exchange=Exchange.HKEX, name="HK Co"))
    session.commit()
    for i in range(50):
        session.add(_make_outcome(i, "SWING", date(2026, 7, 1), True, 0.05, stock_id=1))
    for i in range(50, 100):
        session.add(_make_outcome(i, "SWING", date(2026, 7, 1), False, -0.10, stock_id=2))
    session.commit()

    us_result = _retro_ev_for(session, "SWING", "US", date(2026, 6, 1))
    assert us_result is not None
    assert us_result["n"] == 50
    assert us_result["win_rate"] == 1.0

    all_result = _retro_ev_for(session, "SWING", "ALL", date(2026, 6, 1))
    assert all_result["n"] == 100


def test_excludes_still_open_outcomes_with_null_is_correct_or_pct_return():
    session = _make_session()
    for i in range(50):
        session.add(_make_outcome(i, "SWING", date(2026, 7, 1), True, 0.05))
    for i in range(50, 70):
        session.add(SignalOutcome(
            id=i, signal_id=i, stock_id=1, symbol="TEST", horizon="SWING",
            signal_direction="BUY", signal_date=date(2026, 6, 30),
            confidence=50.0, entry_date=date(2026, 7, 1), is_correct=None, pct_return=None,
        ))
    session.commit()
    result = _retro_ev_for(session, "SWING", "ALL", date(2026, 6, 1))
    assert result["n"] == 50
