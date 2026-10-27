"""F03 observation phase on PostgreSQL: concurrent refreshes, the open-run index, JSON round trip.

Skipped unless STOCKAI_F03_DB names a THROWAWAY PostgreSQL database — the fixture drops and
recreates the tables it uses. SQLite serialises writers, so the race cannot be shown there.
"""
import importlib.util
import os
import pathlib
import sys
import threading
import time
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from api import occurrence_observer as ob  # noqa: E402

_URL = os.environ.get("STOCKAI_F03_DB", "")
pytestmark = pytest.mark.skipif(not _URL.startswith("postgresql"),
                                reason="needs STOCKAI_F03_DB=postgresql://... (throwaway)")

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_f03_pg", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_f03_pg"] = _models
_spec.loader.exec_module(_models)

_TABLES = [_models.Stock.__table__, _models.Signal.__table__, _models.SignalOccurrence.__table__,
           _models.SignalSnapshot.__table__, _models.SignalSessionClose.__table__]
T0 = datetime(2026, 10, 27, 14, 0)


@pytest.fixture
def engine():
    eng = create_engine(_URL, pool_size=10)
    _models.Base.metadata.drop_all(eng, tables=list(reversed(_TABLES)))
    with eng.begin() as c:
        for t in ("signaltype", "signalhorizon", "market", "exchange"):
            c.execute(text(f"DROP TYPE IF EXISTS {t} CASCADE"))
    _models.Base.metadata.create_all(eng, tables=_TABLES)
    _models.Base.metadata.create_all(eng, tables=_TABLES)      # idempotent, as at every startup
    with Session(eng) as s:
        s.add(_models.Stock(id=1, symbol="TEST", name="TEST", market=_models.Market.US,
                            exchange=_models.Exchange.NASDAQ))
        s.commit()
    yield eng
    eng.dispose()


def _reasons(status="forming", day="2026-10-27"):
    basis = "session_closed" if status == "completed" else "provisional_intraday"
    return {"ta_score": 0.61, "nan_field": float("nan"), "input_contract": {
        "market": "US", "basis": basis, "last_bar_date": day, "last_bar_status": status,
        "generated_at": "x"}}


def _observe(eng, signal, minutes, barrier=None, **kw):
    with Session(eng) as s:
        if barrier is not None:
            barrier.wait()
        got = ob.observe_safely(s, stock_id=1, horizon="SWING", signal=signal, confidence=60.0,
                                bullish_probability=0.7, reasons=_reasons(**kw),
                                now=T0 + timedelta(minutes=minutes))
        s.commit()
        return got


@pytest.fixture
def slow_open(monkeypatch):
    """Hold the opening transaction open after its occurrence INSERT, so competitors are certain
    to overlap it. Without this the threads happened to run one after another and the race test
    passed with the lock removed."""
    real = ob._snapshot

    def slow(*a, **k):
        time.sleep(0.3)
        return real(*a, **k)
    monkeypatch.setattr(ob, "_snapshot", slow)


def test_concurrent_refreshes_open_exactly_one_run(engine, slow_open):
    """Eight generations race to open the same run: one occurrence, seq 1, eight generations."""
    barrier, results = threading.Barrier(8), []
    threads = [threading.Thread(target=lambda i=i: results.append(_observe(engine, "BUY", i, barrier)))
               for i in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with engine.connect() as c:
        rows = c.execute(text("SELECT seq, generations, closed_at FROM signal_occurrences")).all()
        snaps = c.execute(text("SELECT COUNT(*) FROM signal_snapshots")).scalar()
    assert rows == [(1, 8, None)]
    assert snaps == 1
    assert all(r and r["observed"] for r in results), "no generation lost to the race"


def test_concurrent_opposite_directions_never_leave_two_open_runs(engine, slow_open):
    barrier = threading.Barrier(6)
    threads = [threading.Thread(target=_observe, args=(engine, "BUY" if i % 2 else "SELL", i, barrier))
               for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with engine.connect() as c:
        open_runs = c.execute(text("SELECT COUNT(*) FROM signal_occurrences WHERE closed_at IS NULL")).scalar()
        seqs = [r[0] for r in c.execute(text("SELECT seq FROM signal_occurrences ORDER BY seq"))]
    assert open_runs == 1
    assert c_total(engine) == 6, "every generation observed — none lost to a unique violation"
    assert seqs == list(range(1, len(seqs) + 1)), "seq is gapless and unique under the lock"


def c_total(eng):
    with eng.connect() as c:
        return c.execute(text("SELECT COALESCE(SUM(generations), 0) FROM signal_occurrences")).scalar()


def test_the_index_itself_refuses_a_second_open_run(engine):
    """Backstop independent of the lock."""
    _observe(engine, "BUY", 0)
    with pytest.raises(IntegrityError):
        with engine.begin() as c:
            c.execute(text("""
                INSERT INTO signal_occurrences (stock_id, horizon, direction, market, session_date,
                    seq, opened_at, last_seen_at, generations, content_changes, last_content_hash,
                    contract_version)
                VALUES (1, 'SWING', 'SELL', 'US', '2026-10-27', 2, now(), now(), 1, 0, 'h', 'v')
            """))


def test_json_round_trips_and_the_completed_close_is_first_wins(engine, slow_open):
    _observe(engine, "BUY", 0)
    barrier = threading.Barrier(4)
    threads = [threading.Thread(target=_observe, args=(engine, "BUY", 130 + i, barrier),
                                kwargs={"status": "completed"}) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    with engine.connect() as c:
        closes = c.execute(text("SELECT COUNT(*) FROM signal_session_closes")).scalar()
        snap = c.execute(text("""
            SELECT sn.reasons, sn.input_contract FROM signal_session_closes c
            JOIN signal_snapshots sn ON sn.id = c.snapshot_id""")).first()
    assert closes == 1
    assert snap[0]["ta_score"] == 0.61 and snap[0]["nan_field"] is None
    assert snap[1]["last_bar_status"] == "completed"


def test_the_report_runs_on_postgresql_with_table_sizes(engine):
    _observe(engine, "SELL", 0)
    _observe(engine, "BUY", 60)
    with Session(engine) as s:
        rep = ob.observation_report(s)
    assert rep["close_reasons"] == {"direction_change": 1, "open": 1}
    assert set(rep["table_bytes"]) == {"signal_occurrences", "signal_snapshots", "signal_session_closes"}
