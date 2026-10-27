"""F03 observation phase (2026-10-27 design §11/§12) — occurrences, snapshots, completed closes.

Behavioural, against the real shared models on SQLite (this suite's conftest stubs `db`, so the
models are loaded by path). PostgreSQL-only behaviour — the advisory lock under concurrent
refreshes — is in test_f03_occurrence_observer_pg.py.
"""
import importlib.util
import pathlib
import sys
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "src"))

from api import occurrence_observer as ob  # noqa: E402

_models_path = pathlib.Path(__file__).resolve().parents[3] / "shared" / "db" / "models.py"
_spec = importlib.util.spec_from_file_location("db_models_under_test_f03", _models_path)
_models = importlib.util.module_from_spec(_spec)
sys.modules["db_models_under_test_f03"] = _models
_spec.loader.exec_module(_models)

T0 = datetime(2026, 10, 27, 14, 0)


@pytest.fixture
def s():
    engine = create_engine("sqlite://")
    _models.Base.metadata.create_all(engine, tables=[
        _models.Stock.__table__, _models.Signal.__table__, _models.SignalOccurrence.__table__,
        _models.SignalSnapshot.__table__, _models.SignalSessionClose.__table__])
    with Session(engine) as sess:
        sess.add(_models.Stock(id=1, symbol="TEST", name="TEST", market=_models.Market.US,
                               exchange=_models.Exchange.NASDAQ))
        sess.commit()
        yield sess


def _reasons(day="2026-10-27", status="forming", conf_note=None, generated_at="x"):
    basis = {"forming": "provisional_intraday", "completed": "session_closed"}.get(status, "unknown")
    r = {"ta_score": 0.6, "input_contract": {
        "contract_version": "f02-input-v1", "market": "US", "generated_at": generated_at,
        "basis": basis, "last_bar_date": day, "last_bar_status": status}}
    if conf_note is not None:
        r["note"] = conf_note
    return r


def _gen(s, signal, minutes, conf=60.0, **kw):
    out = ob.observe_generation(
        s, stock_id=1, horizon="SWING", signal=signal, confidence=conf, bullish_probability=0.7,
        reasons=_reasons(**kw), signal_id=None, now=T0 + timedelta(minutes=minutes))
    s.commit()
    return out


def _occ(s):
    return [dict(r) for r in s.execute(text(
        "SELECT direction, session_date, seq, closed_at IS NULL AS open, close_reason, generations, "
        "content_changes FROM signal_occurrences ORDER BY id")).mappings()]


def test_sell_then_buy_is_two_occurrences_with_their_own_opened_snapshots(s):
    """THE F03 CASE: one daily row, two published directions — two occurrences, not one."""
    _gen(s, "SELL", 0)
    _gen(s, "BUY", 180)
    occ = _occ(s)
    assert [(o["direction"], o["seq"], o["open"], o["close_reason"]) for o in occ] == [
        ("SELL", 1, 0, "direction_change"), ("BUY", 2, 1, None)]
    snaps = s.execute(text("SELECT kind, signal FROM signal_snapshots ORDER BY id")).all()
    assert snaps == [("opened", "SELL"), ("opened", "BUY")]


def test_same_direction_confidence_changes_stay_in_one_occurrence_and_are_counted(s):
    _gen(s, "BUY", 0, conf=58.0)
    _gen(s, "BUY", 60, conf=58.0)            # identical decision content
    _gen(s, "BUY", 120, conf=81.0)           # strength change
    occ = _occ(s)
    assert len(occ) == 1 and occ[0]["generations"] == 3 and occ[0]["content_changes"] == 1
    snap = s.execute(text("SELECT confidence FROM signal_snapshots WHERE kind='opened'")).all()
    assert snap == [(58.0,)], "the opened snapshot is frozen; later strength is counted, not stored"


@pytest.mark.parametrize("neutral,reason", [("HOLD", "to_hold"), ("WAIT", "to_wait")])
def test_hold_or_wait_closes_and_a_later_buy_opens_a_new_run(s, neutral, reason):
    _gen(s, "BUY", 0)
    _gen(s, neutral, 60)
    _gen(s, neutral, 90)
    _gen(s, "BUY", 120)
    occ = _occ(s)
    assert [(o["seq"], o["close_reason"]) for o in occ] == [(1, reason), (2, None)]
    assert s.execute(text("SELECT COUNT(*) FROM signal_snapshots WHERE signal IN ('HOLD','WAIT')")).scalar() == 0


def test_a_new_session_closes_the_run_even_in_the_same_direction(s):
    _gen(s, "BUY", 0, day="2026-10-27")
    _gen(s, "BUY", 24 * 60, day="2026-10-28")
    occ = _occ(s)
    assert [(str(o["session_date"]), o["seq"], o["close_reason"]) for o in occ] == [
        ("2026-10-27", 1, "session_change"), ("2026-10-28", 1, None)]


def test_a_generation_reading_an_older_session_is_ignored(s):
    _gen(s, "BUY", 0, day="2026-10-28")
    got = _gen(s, "SELL", 5, day="2026-10-27")
    assert got == {"observed": False, "reason": "older_session_than_open_occurrence"}
    assert [(o["direction"], o["open"]) for o in _occ(s)] == [("BUY", 1)]


@pytest.mark.parametrize("status", ["future", "not_a_session", "unknown"])
def test_no_session_identity_records_nothing(s, status):
    got = _gen(s, "BUY", 0, status=status)
    assert got["observed"] is False
    assert _occ(s) == []


def test_capture_clock_is_not_decision_content_but_everything_else_is():
    a = ob.decision_content("BUY", "SWING", 60.0, 0.7, _reasons(generated_at="10:00"))
    b = ob.decision_content("BUY", "SWING", 60.0, 0.7, _reasons(generated_at="11:00"))
    c = ob.decision_content("BUY", "SWING", 60.0, 0.7, _reasons(generated_at="11:00", conf_note=1))
    d = ob.decision_content("BUY", "SWING", 60.0, 0.7, _reasons(status="completed"))
    assert ob.content_hash(a) == ob.content_hash(b)
    assert ob.content_hash(a) != ob.content_hash(c), "a changed reason is different evidence"
    assert ob.content_hash(a) != ob.content_hash(d), "input identity stays in the content"


def test_completed_close_is_frozen_once_for_its_own_session_and_may_be_hold(s):
    _gen(s, "BUY", 0)                                         # forming: no close record
    assert s.execute(text("SELECT COUNT(*) FROM signal_session_closes")).scalar() == 0
    _gen(s, "HOLD", 130, status="completed")                  # first post-close generation
    _gen(s, "BUY", 140, status="completed", conf=90.0)        # later one does not replace it
    rows = s.execute(text(
        "SELECT c.session_date, sn.signal, sn.kind, sn.occurrence_id, c.close_ingest_verified "
        "FROM signal_session_closes c JOIN signal_snapshots sn ON sn.id = c.snapshot_id")).all()
    assert [(str(r[0]), r[1], r[2], r[3], r[4]) for r in rows] == [
        ("2026-10-27", "HOLD", "completed_close", None, None)]


def test_yesterdays_completed_bar_never_freezes_todays_close(s):
    """§11: during today's session with today's bar missing, the newest bar is yesterday's —
    it may freeze YESTERDAY's record, and today's stays absent."""
    _gen(s, "BUY", 0, day="2026-10-26", status="completed")
    sessions = [str(r[0]) for r in s.execute(text("SELECT session_date FROM signal_session_closes"))]
    assert sessions == ["2026-10-26"]


def test_completed_close_of_an_open_run_links_to_its_occurrence(s):
    _gen(s, "BUY", 0)
    _gen(s, "BUY", 130, status="completed")
    occ_id = s.execute(text("SELECT id FROM signal_occurrences")).scalar()
    linked = s.execute(text(
        "SELECT sn.occurrence_id FROM signal_session_closes c "
        "JOIN signal_snapshots sn ON sn.id = c.snapshot_id")).scalar()
    assert linked == occ_id
    assert _occ(s)[0]["generations"] == 2


def test_a_failure_rolls_back_only_the_observation(s, monkeypatch):
    s.execute(text("INSERT INTO signals (id, stock_id, signal, horizon, confidence, source) "
                   "VALUES (7, 1, 'BUY', 'SWING', 60, 'signal-engine')"))

    def boom(*a, **k):
        raise RuntimeError("observer down")
    monkeypatch.setattr(ob, "observe_generation", boom)
    assert ob.observe_safely(s, stock_id=1, horizon="SWING", signal="BUY", confidence=60.0,
                             bullish_probability=0.7, reasons=_reasons()) is None
    s.commit()
    assert s.execute(text("SELECT COUNT(*) FROM signals")).scalar() == 1, "the upsert survives"


def test_a_partial_write_inside_the_savepoint_is_undone(s, monkeypatch):
    real = ob._snapshot

    def fail_after_occurrence(*a, **k):
        raise RuntimeError("snapshot insert failed")
    monkeypatch.setattr(ob, "_snapshot", fail_after_occurrence)
    ob.observe_safely(s, stock_id=1, horizon="SWING", signal="BUY", confidence=60.0,
                      bullish_probability=0.7, reasons=_reasons(), now=T0)
    s.commit()
    assert _occ(s) == [], "an occurrence without its opened snapshot must not survive"
    monkeypatch.setattr(ob, "_snapshot", real)


def test_the_kill_switch_disables_observation(s, monkeypatch):
    monkeypatch.setenv("F03_OBSERVATION", "0")
    assert ob.observe_safely(s, stock_id=1, horizon="SWING", signal="BUY", confidence=60.0,
                             bullish_probability=0.7, reasons=_reasons(), now=T0) is None
    assert _occ(s) == []


def test_the_report_measures_churn_per_session(s):
    _gen(s, "SELL", 0)
    _gen(s, "BUY", 60, conf=58.0)
    _gen(s, "BUY", 90, conf=81.0)
    _gen(s, "HOLD", 130, status="completed")
    rep = ob.observation_report(s)
    row = rep["per_session"][0]
    assert (row["occurrences"], row["later_runs"], row["generations"], row["content_changes"]) == (2, 1, 3, 1)
    assert rep["close_reasons"] == {"direction_change": 1, "to_hold": 1}
    assert [(r["signal"], r["n"]) for r in rep["completed_close_by_signal"]] == [("HOLD", 1)]


def test_both_upsert_sites_observe_after_the_upsert():
    """Wiring: _bulk_persist() and signal_for() both observe, with the row id from RETURNING."""
    import ast
    src = (pathlib.Path(__file__).resolve().parents[1] / "src/api/routes.py").read_text()
    tree = ast.parse(src)
    for name in ("_bulk_persist", "signal_for"):
        fn = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == name)
        body = ast.unparse(fn)
        assert "RETURNING id" in body, name
        assert body.index("INSERT INTO signals") < body.index("_observe_occurrence(") , name
