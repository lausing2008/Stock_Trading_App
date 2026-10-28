"""Signal generation vs alert-delivery metadata: no lock-order deadlock (2026-10-27 review).

The delivery writer lives in market-data (services/ai_signal_delivery_write.py), whose suite
stubs SQLAlchemy; it is loaded by path here, beside the real upsert and F03 observer it races.

- SQLite: the helper writes every decision, in canonical order, and skips a vanished row.
- PostgreSQL (skipped unless STOCKAI_F03_DB names a THROWAWAY database — tables are dropped):
  the shipped `_bulk_persist` upsert SQL plus the F03 observer, racing the real helper while
  it already holds rows. Overlap is proven with pg_blocking_pids(), not assumed from sleeps.
  The reversed order must still deadlock (negative control), or the test proves nothing.
"""
import ast
import importlib.util
import json
import os
import pathlib
import sys
import threading
import time
from datetime import datetime, timezone

import pytest
from sqlalchemy import create_engine, event, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "services/signal-engine/src"))

from api import occurrence_observer as ob  # noqa: E402


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


_models = _load("db_models_under_test_lockorder", ROOT / "shared/db/models.py")
dw = _load("ai_signal_delivery_write_under_test",
           ROOT / "services/market-data/src/services/ai_signal_delivery_write.py")
NOW = datetime(2026, 10, 27, 20, 0, tzinfo=timezone.utc)
ORDER = dw.GENERATION_HORIZON_ORDER


def _decision(state="candidate", n=0):
    return {"state": state, "recipients": n}


# ── SQLite: what the helper writes ──────────────────────────────────────────────────────

@pytest.fixture
def s():
    engine = create_engine("sqlite://")
    _models.Base.metadata.create_all(engine, tables=[_models.Stock.__table__, _models.Signal.__table__])
    with Session(engine) as sess:
        for sid in (1, 2):
            sess.add(_models.Stock(id=sid, symbol=f"T{sid}", name="T", market=_models.Market.US,
                                   exchange=_models.Exchange.NASDAQ))
        for i, (sid, h) in enumerate([(2, "GROWTH"), (1, "GROWTH"), (1, "SHORT"), (2, "SHORT")], 1):
            sess.add(_models.Signal(id=i, stock_id=sid, horizon=_models.SignalHorizon(h),
                                    signal=_models.SignalType.BUY, confidence=50.0, source="t"))
        sess.commit()
        yield sess


def test_writes_every_decision_in_canonical_order_and_skips_a_vanished_row(s):
    updated = []
    event.listen(s.get_bind(), "before_cursor_execute",
                 lambda c, cur, stmt, params, ctx, many:
                 updated.append(params[-1]) if stmt.startswith("UPDATE") else None)
    decisions = {1: _decision("sender_accepted", 2), 99: _decision(), 4: _decision(),
                 2: _decision("queued_not_sent"), 3: _decision("suppressed_by_cooldown")}
    assert dw.persist_delivery_decisions(s, _models.Signal, decisions, decided_at=NOW,
                                         version="ai-alert-1") == 4
    s.commit()
    assert updated == [3, 2, 4, 1], "stock 1 SHORT, GROWTH, then stock 2 SHORT, GROWTH"
    rows = s.execute(text("SELECT id, alert_delivery_state, alert_delivered_recipient_count, "
                          "alert_delivery_version FROM signals ORDER BY id")).all()
    assert rows == [(1, "sender_accepted", 2, "ai-alert-1"), (2, "queued_not_sent", 0, "ai-alert-1"),
                    (3, "suppressed_by_cooldown", 0, "ai-alert-1"), (4, "candidate", 0, "ai-alert-1")]


def test_no_decisions_issues_no_statements(s):
    assert dw.persist_delivery_decisions(s, _models.Signal, {}, decided_at=NOW, version="v") == 0


# ── PostgreSQL: the race itself ─────────────────────────────────────────────────────────

_URL = os.environ.get("STOCKAI_F03_DB", "")
pg = pytest.mark.skipif(not _URL.startswith("postgresql"),
                        reason="needs STOCKAI_F03_DB=postgresql://... (throwaway)")
_TABLES = [_models.Stock.__table__, _models.Signal.__table__, _models.SignalOccurrence.__table__,
           _models.SignalSnapshot.__table__, _models.SignalSessionClose.__table__]


def _shipped_upsert() -> str:
    src = (ROOT / "services/signal-engine/src/api/routes.py").read_text()
    fn = next(n for n in ast.parse(src).body
              if isinstance(n, ast.FunctionDef) and n.name == "_bulk_persist")
    return next(n.value for n in ast.walk(fn) if isinstance(n, ast.Constant) and isinstance(n.value, str)
                and "INSERT INTO signals" in n.value and "ON CONFLICT" in n.value)


UPSERT = text(_shipped_upsert())
REASONS = {"input_contract": {"market": "US", "basis": "provisional_intraday",
                              "last_bar_date": datetime.now(timezone.utc).date().isoformat(),
                              "last_bar_status": "forming", "generated_at": "x"}}


def _upsert(conn, stock_id, horizon, conf):
    return conn.execute(UPSERT, dict(sid=stock_id, sig="BUY" if conf == 99 else "HOLD", hor=horizon,
                                     conf=conf, bp=0.8, rsns=json.dumps(REASONS), src="test")).scalar()


@pytest.fixture
def engine():
    eng = create_engine(_URL, pool_size=6)
    _models.Base.metadata.drop_all(eng, tables=list(reversed(_TABLES)))
    with eng.begin() as c:
        for t in ("signaltype", "signalhorizon", "market", "exchange"):
            c.execute(text(f"DROP TYPE IF EXISTS {t} CASCADE"))
    _models.Base.metadata.create_all(eng, tables=_TABLES)
    with eng.begin() as c:
        c.execute(text("CREATE UNIQUE INDEX uq_signals_stock_horizon_day "
                       "ON signals (stock_id, horizon, date_trunc('day', ts))"))
    with Session(eng) as sess:
        for sid in (1, 2):
            sess.add(_models.Stock(id=sid, symbol=f"T{sid}", name="T", market=_models.Market.US,
                                   exchange=_models.Exchange.NASDAQ))
        sess.commit()
        for sid in (1, 2):
            for h in ORDER:
                _upsert(sess, sid, h, 40)
        sess.commit()
    yield eng
    eng.dispose()


def _race(eng):
    """Generator refreshes stock 2 (the real per-symbol batch: upsert + F03 observe per horizon)
    while delivery writes metadata for all 8 rows. Delivery is let go only after it is proven
    blocked BY the generator; by then it already holds whatever it took before stock 2 SHORT."""
    with eng.connect() as c:
        ids = {(r[1], r[2]): r[0] for r in c.execute(text("SELECT id, stock_id, horizon::text FROM signals"))}
    # Insertion order is the worst case the old loop had: GROWTH first.
    decisions = {ids[(sid, h)]: _decision() for sid in (2, 1) for h in reversed(ORDER)}
    holds_short, go = threading.Event(), threading.Event()
    pids, errors, observed = {}, {}, []

    def generator():
        try:
            with Session(eng) as sess:
                pids["gen"] = sess.execute(text("SELECT pg_backend_pid()")).scalar()
                for i, h in enumerate(ORDER):
                    row_id = _upsert(sess, 2, h, 99)
                    observed.append(ob.observe_safely(
                        sess, stock_id=2, horizon=h, signal="BUY", confidence=99.0,
                        bullish_probability=0.8, reasons=REASONS, signal_id=row_id))
                    if i == 0:
                        holds_short.set()
                        assert go.wait(10)
                sess.commit()
        except OperationalError as e:
            errors["gen"] = e

    def delivery():
        try:
            with Session(eng) as sess:
                pids["del"] = sess.execute(text("SELECT pg_backend_pid()")).scalar()
                dw.persist_delivery_decisions(sess, _models.Signal, decisions,
                                              decided_at=NOW, version="ai-alert-1")
                sess.commit()
        except OperationalError as e:
            errors["del"] = e

    tg = threading.Thread(target=generator)
    tg.start()
    assert holds_short.wait(10)
    td = threading.Thread(target=delivery)
    td.start()
    blocked = False
    with eng.connect() as mon:
        for _ in range(100):
            if pids.get("del") and pids["gen"] in mon.execute(
                    text("SELECT pg_blocking_pids(:p)"), {"p": pids["del"]}).scalar():
                blocked = True
                break
            time.sleep(0.05)
    go.set()
    tg.join(30)
    td.join(30)
    assert blocked, "delivery was never observed waiting on the generator; the race did not happen"
    return errors, observed


@pg
def test_canonical_order_commits_both_writers_with_the_f03_hooks_live(engine):
    errors, observed = _race(engine)
    assert errors == {}
    with engine.connect() as c:
        assert c.execute(text("SELECT count(*) FROM signals WHERE stock_id = 2 AND confidence = 99")).scalar() == 4
        assert c.execute(text("SELECT count(*) FROM signals WHERE alert_delivery_version = 'ai-alert-1'")).scalar() == 8
        assert c.execute(text("SELECT count(*) FROM signal_occurrences WHERE stock_id = 2")).scalar() == 4
    assert all(o and o["observed"] for o in observed), "observation succeeded inside the race"


@pg
def test_negative_control_reversed_order_still_deadlocks(engine, monkeypatch):
    """Without this the positive test could pass for a reason other than the order."""
    real = dw.canonical_write_order
    monkeypatch.setattr(dw, "canonical_write_order", lambda targets: list(reversed(real(targets))))
    errors, _ = _race(engine)
    assert len(errors) == 1 and next(iter(errors.values())).orig.pgcode == "40P01"
    with engine.connect() as c:
        if "gen" in errors:   # whichever PostgreSQL chose, the victim's batch is all-or-nothing
            assert c.execute(text("SELECT count(*) FROM signals WHERE stock_id = 2 AND confidence = 99")).scalar() == 0
            assert c.execute(text("SELECT count(*) FROM signal_occurrences")).scalar() == 0
        else:
            assert c.execute(text("SELECT count(*) FROM signals WHERE alert_delivery_version IS NOT NULL")).scalar() == 0
