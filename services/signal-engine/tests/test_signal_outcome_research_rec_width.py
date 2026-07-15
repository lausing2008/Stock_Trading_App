"""Regression test for T247-SIGNALENGINE-RESEARCHREC-TOOSHORT.

SignalOutcome.research_rec was String(16), but research-engine's real recommendation
vocabulary includes "INSUFFICIENT DATA" (17 chars) — every occurrence raised an unhandled
psycopg2.errors.StringDataRightTruncation, silently failing the ENTIRE batch insert of up to
25 signal_outcomes rows in evaluate_signal_outcomes(), confirmed happening repeatedly in
production on 2026-07-14 (5+ distinct failed batches in a single day).

Loads shared/db/models.py directly via importlib against a real in-memory SQLite table, since
db/__init__.py triggers a real psycopg2 engine connection — same pattern as
test_rank_symbol_market_scoping.py (ranking-engine) and test_strategy_backtest_cascade.py
(strategy-engine).
"""
import importlib.util
import pathlib
import sys
from datetime import date

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test"] = _models
_spec.loader.exec_module(_models)

SignalOutcome = _models.SignalOutcome
Base = _models.Base


def _make_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine, tables=[SignalOutcome.__table__])
    return Session(engine)


def test_research_rec_column_accepts_the_full_insufficient_data_string():
    """The exact bug scenario: research-engine's real 'INSUFFICIENT DATA' (17 chars) must fit
    without raising — this was the value that crashed the whole batch insert in production.

    id is set explicitly since BigInteger primary keys don't get SQLite's implicit
    INTEGER PRIMARY KEY autoincrement (a real Postgres sequence handles this in production;
    this is a test-harness-only workaround, not part of the fix under test)."""
    session = _make_session()
    outcome = SignalOutcome(
        id=1, signal_id=1, stock_id=1, symbol="TEST", horizon="SWING",
        signal_direction="BUY", signal_date=date(2026, 7, 1), confidence=50.0,
        research_rec="INSUFFICIENT DATA",
    )
    session.add(outcome)
    session.commit()  # must not raise StringDataRightTruncation
    assert session.query(SignalOutcome).first().research_rec == "INSUFFICIENT DATA"


def test_research_rec_column_still_accepts_shorter_real_values():
    session = _make_session()
    for i, rec in enumerate(["STRONG BUY", "BUY", "WATCH", "AVOID", "SELL"]):
        session.add(SignalOutcome(
            id=i + 1, signal_id=i, stock_id=1, symbol="TEST", horizon="SWING",
            signal_direction="BUY", signal_date=date(2026, 7, 1), confidence=50.0,
            research_rec=rec,
        ))
    session.commit()  # must not raise
    values = {o.research_rec for o in session.query(SignalOutcome).all()}
    assert values == {"STRONG BUY", "BUY", "WATCH", "AVOID", "SELL"}


def test_research_rec_column_width_is_at_least_32():
    """Directly assert the column definition itself, so a future accidental narrowing is
    caught even before any data is inserted."""
    col = SignalOutcome.__table__.c.research_rec
    assert col.type.length >= 32
