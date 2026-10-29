"""Intraday Stage C storage suite (design §9; review CDR-04, CDR-R2-02, CDR-R2-03).

Not collected by the market-data suite (no test_ prefix): its conftest stubs SQLAlchemy. Run by
test_intraday_store.py in a subprocess with --noconftest, against
  INTRADAY_SUITE_URL=sqlite://                 constraints SQLite can express, writer logic
  INTRADAY_SUITE_URL=postgresql+psycopg2://... the full contract, triggers included (each test in
                                               its own throwaway schema, dropped afterwards)
"""
import os
import pathlib
import sys
import threading
import uuid
from datetime import date, datetime, timedelta, timezone

ROOT = pathlib.Path(__file__).resolve().parents[3]
sys.path[:0] = [str(ROOT / "shared"), str(ROOT / "services" / "market-data")]

import pytest  # noqa: E402
from sqlalchemy import create_engine, event, func, select, text  # noqa: E402
from sqlalchemy.exc import DBAPIError, IntegrityError  # noqa: E402

from src.services.intraday import contract as c  # noqa: E402
from src.services.intraday import models as m  # noqa: E402
from src.services.intraday import schema, store  # noqa: E402

URL = os.environ.get("INTRADAY_SUITE_URL", "sqlite://")
PG = URL.startswith("postgresql")
pg_only = pytest.mark.skipif(not PG, reason="database trigger: PostgreSQL only")
UTC = timezone.utc
D = date(2026, 10, 28)
T = c.session_times("US", D)
REFUSED = (IntegrityError, DBAPIError, c.ContractViolation)


def at(h, mi):
    return datetime(2026, 10, 28, h, mi, tzinfo=UTC)


@pytest.fixture
def engine():
    if PG:
        schema_name = "t_" + uuid.uuid4().hex[:12]
        admin = create_engine(URL)
        with admin.begin() as conn:
            conn.execute(text(f"CREATE SCHEMA {schema_name}"))
        eng = create_engine(URL, connect_args={"options": f"-csearch_path={schema_name}"}, pool_size=8)
    else:
        from sqlalchemy.pool import StaticPool
        eng = create_engine(URL, poolclass=StaticPool, connect_args={"check_same_thread": False})

        @event.listens_for(eng, "connect")
        def _fk(dbapi, _):
            dbapi.execute("PRAGMA foreign_keys=ON")
    with eng.begin() as conn:
        schema.create(conn)
    yield eng
    eng.dispose()
    if PG:
        with admin.begin() as conn:
            conn.execute(text(f"DROP SCHEMA {schema_name} CASCADE"))
        admin.dispose()


IDENT_KW = dict(strategy_version="1", strategy_source_hash="s" * 64, params=c.Params())


def ident(conn, **kw):
    return store.ensure_identity(conn, **{**IDENT_KW, **kw})


def batch(conn, identity, run_key=c.OBSERVED_RUN):
    return store.start_batch(conn, run_key=run_key, identity=identity, code_version="abc123",
                             started_at=at(14, 7), data_cutoff=at(14, 7))


SETUP = dict(direction="bullish", entry_reference=100.0, stop=99.0, target=102.0, risk_per_share=1.0, reasons={})


def a_setup(conn, identity, b, *, symbol="AAA", trigger=None, run_key=c.OBSERVED_RUN, stock_id=1):
    trigger = trigger or at(14, 0)
    det = c.decide_at_detection(trigger_bar_open=trigger, detected_at=trigger + timedelta(minutes=7), direction="bullish",
                                times=T, stop_breached=False, inputs_ok=True)
    inp = c.InputRecord(role="trigger", timeframe="5m", bar_open=trigger, source="native", values={"o": 1},
                        raw_hash="r", source_hash="s", input_hash="i", derived_hash=None,
                        readable_at=trigger + timedelta(minutes=7), input_read_at=trigger + timedelta(minutes=7))
    return store.record_evaluation(conn, batch_id=b, run_key=run_key, identity=identity, stock_id=stock_id,
                                   symbol=symbol, trigger_bar_open=trigger, result="setup",
                                   detected_at=trigger + timedelta(minutes=7), data_cutoff=trigger + timedelta(minutes=7),
                                   setup=SETUP, input_records=[inp], detection=det, times=T)


def ev(decided, basis="observed"):
    return c.EventTimes(decided - timedelta(minutes=1), decided, decided, basis)


def states(conn, sid):
    return [r.to_state for r in conn.execute(select(m.transitions.c.to_state).where(
        m.transitions.c.setup_id == sid).order_by(m.transitions.c.seq))]


# ── Schema and sentinel ───────────────────────────────────────────────────────────────────

def test_schema_is_idempotent_and_holds_only_the_observed_sentinel(engine):
    with engine.begin() as conn:
        schema.create(conn)
        assert conn.execute(select(m.runs.c.run_key, m.runs.c.record_mode)).all() == [("observed", "observed")]
        for t in m.TABLES[1:]:
            assert conn.execute(select(func.count()).select_from(t)).scalar() == 0


def test_observed_run_key_is_reserved_and_replay_needs_its_identity(engine):
    with engine.begin() as conn:
        with pytest.raises(c.ContractViolation):
            store.ensure_replay_run(conn, "observed", input_snapshot_hash="h", as_of=at(12, 0), now=at(12, 0))
    with pytest.raises(REFUSED):
        with engine.begin() as conn:
            conn.execute(m.runs.insert().values(run_key="r1", record_mode="replay", created_at=at(12, 0)))


# ── Evaluation identity and idempotency (CDR-R2-02) ───────────────────────────────────────

def test_a_genuine_duplicate_is_idempotent(engine):
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
        first = a_setup(conn, i, b)
        again = a_setup(conn, i, batch(conn, i))
        assert first["created"] and not again["created"]
        assert (again["evaluation_id"], again["setup_id"]) == (first["evaluation_id"], first["setup_id"])
        assert conn.execute(select(func.count()).select_from(m.setups)).scalar() == 1


def test_a_different_algorithm_identity_is_a_separate_evaluation(engine):
    with engine.begin() as conn:
        i1, i2 = ident(conn), ident(conn, strategy_source_hash="t" * 64)
        assert i1 != i2
        a_setup(conn, i1, batch(conn, i1))
        a_setup(conn, i2, batch(conn, i2))
        assert conn.execute(select(func.count()).select_from(m.evaluations)).scalar() == 2


def test_cross_run_and_orphan_evaluation_links_are_rejected(engine):
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
        store.ensure_replay_run(conn, "r1", input_snapshot_hash="h", as_of=at(12, 0), now=at(12, 0))
    with pytest.raises(REFUSED):                      # batch is observed, evaluation claims replay run
        with engine.begin() as conn:
            conn.execute(m.evaluations.insert().values(
                evaluation_key="k" * 64, batch_id=b, run_key="r1", identity_hash=i, stock_id=1, symbol="AAA",
                session_date=D, trigger_bar_open=at(14, 0), result="no_setup", detected_at=at(14, 7),
                data_cutoff=at(14, 7)))
    with pytest.raises(REFUSED):                      # setup pointing at a non-existent evaluation
        with engine.begin() as conn:
            conn.execute(m.setups.insert().values(
                occurrence_key="o" * 64, evaluation_id=999, run_key="observed", identity_hash=i, stock_id=1,
                symbol="AAA", session_date=D, trigger_bar_open=at(14, 0), direction="bullish", entry_reference=1,
                stop=0.5, target=2, risk_per_share=0.5, reasons={}, detected_at=at(14, 7),
                entry_expires_at=at(14, 20), last_entry_at=at(19, 30), liquidation_at=at(19, 50),
                session_close=at(20, 0)))


@pg_only
def test_a_setup_without_a_setup_evaluation_or_first_transition_is_refused_at_commit(engine):
    with pytest.raises(DBAPIError, match="intraday_setup"):
        with engine.begin() as conn:
            i = ident(conn)
            b = batch(conn, i)
            eid = conn.execute(m.evaluations.insert().values(
                evaluation_key="k" * 64, batch_id=b, run_key="observed", identity_hash=i, stock_id=1, symbol="AAA",
                session_date=D, trigger_bar_open=at(14, 0), result="no_setup", detected_at=at(14, 7),
                data_cutoff=at(14, 7)).returning(m.evaluations.c.id)).scalar()
            conn.execute(m.setups.insert().values(
                occurrence_key="o" * 64, evaluation_id=eid, run_key="observed", identity_hash=i, stock_id=1,
                symbol="AAA", session_date=D, trigger_bar_open=at(14, 0), direction="bullish", entry_reference=1,
                stop=0.5, target=2, risk_per_share=0.5, reasons={}, detected_at=at(14, 7),
                entry_expires_at=at(14, 20), last_entry_at=at(19, 30), liquidation_at=at(19, 50),
                session_close=at(20, 0)))
    with pytest.raises(DBAPIError, match="intraday_evaluation"):
        with engine.begin() as conn:
            i = ident(conn)
            conn.execute(m.evaluations.insert().values(
                evaluation_key="j" * 64, batch_id=batch(conn, i), run_key="observed", identity_hash=i, stock_id=1,
                symbol="AAA", session_date=D, trigger_bar_open=at(14, 5), result="setup", detected_at=at(14, 12),
                data_cutoff=at(14, 12)))


# ── Transitions (CDR-04, CDR-R2-03) ───────────────────────────────────────────────────────

def test_enter_unresolved_reconcile_succeeds_exactly_once(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
        store.append_transition(conn, setup_id=sid, to_state="entered", event=ev(at(14, 12)), idempotency_key="e1")
        store.append_transition(conn, setup_id=sid, to_state="unresolved_exit_at_close", event=ev(at(20, 1)),
                                idempotency_key="u1")
        store.append_transition(conn, setup_id=sid, to_state="reconciled", event=ev(at(20, 30)), idempotency_key="r1")
        assert states(conn, sid) == ["detected", "actionable", "entered", "unresolved_exit_at_close", "reconciled"]
        assert store.append_transition(conn, setup_id=sid, to_state="reconciled", event=ev(at(20, 30)),
                                       idempotency_key="r1")["created"] is False
        with pytest.raises(c.ContractViolation):
            store.append_transition(conn, setup_id=sid, to_state="reconciled", event=ev(at(20, 40)),
                                    idempotency_key="r2")


def test_competing_entered_and_cancelled_cannot_both_append(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
    if PG:
        # two writers race; the row lock + database validation leave exactly one winner
        barrier, results = threading.Barrier(2), []

        def write(state, key):
            try:
                with engine.begin() as conn:
                    barrier.wait()
                    store.append_transition(conn, setup_id=sid, to_state=state, event=ev(at(14, 12)), idempotency_key=key)
                results.append(state)
            except REFUSED:
                results.append("refused")
        ts = [threading.Thread(target=write, args=a) for a in (("entered", "x1"), ("cancelled", "x2"))]
        [t.start() for t in ts]
        [t.join() for t in ts]
        assert sorted(results).count("refused") == 1
    else:
        with engine.begin() as conn:
            store.append_transition(conn, setup_id=sid, to_state="entered", event=ev(at(14, 12)), idempotency_key="x1")
            with pytest.raises(c.ContractViolation):
                store.append_transition(conn, setup_id=sid, to_state="cancelled", event=ev(at(14, 12)),
                                        idempotency_key="x2")
    with engine.begin() as conn:
        assert len(states(conn, sid)) == 3


@pg_only
def test_the_database_refuses_an_append_that_skips_the_writer(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
    raw = dict(setup_id=sid, run_key="observed", decided_at=at(14, 12), clock_basis="observed")
    for bad in (dict(seq=3, from_state="detected", to_state="expired_before_detection", idempotency_key="b1"),  # stale from
                dict(seq=5, from_state="actionable", to_state="entered", idempotency_key="b2"),               # seq gap
                dict(seq=3, from_state="actionable", to_state="exited", idempotency_key="b3")):               # illegal pair
        with pytest.raises(DBAPIError):
            with engine.begin() as conn:
                conn.execute(m.transitions.insert().values(**raw, **bad))


def test_observed_event_times_must_be_ordered(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
        with pytest.raises(c.ContractViolation):
            store.append_transition(conn, setup_id=sid, to_state="cancelled", reason="input_revised",
                                    event=c.EventTimes(at(14, 9), at(14, 8), at(14, 10), "observed"), idempotency_key="z")


# ── Outcomes (CDR-R2-02) ──────────────────────────────────────────────────────────────────

def test_outcome_revisions_under_one_evaluator_version_and_duplicates(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
        kw = dict(setup_id=sid, kind="forecast", window="60", evaluator_version="v1", status="resolved",
                  values={"ret": 0.01}, now=at(16, 0))
        o1 = store.record_outcome(conn, evidence_hash="e1", **kw)
        o2 = store.record_outcome(conn, evidence_hash="e2", **kw)        # revised evidence, same code
        dup = store.record_outcome(conn, evidence_hash="e2", **kw)
        assert o1["created"] and o2["created"] and not dup["created"]
        chain = conn.execute(select(m.outcomes.c.id, m.outcomes.c.supersedes_id).order_by(m.outcomes.c.id)).all()
        assert chain == [(o1["outcome_id"], None), (o2["outcome_id"], o1["outcome_id"])]


def test_duplicate_trade_roots_and_cross_chain_predecessors_are_rejected(engine):
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
        s1 = a_setup(conn, i, b)["setup_id"]
        s2 = a_setup(conn, i, b, symbol="BBB", stock_id=2)["setup_id"]
        o1 = store.record_outcome(conn, setup_id=s1, kind="trade", window="trade", evaluator_version="v1",
                                  evidence_hash="e1", status="realized", values={}, now=at(16, 0))["outcome_id"]
    base = dict(run_key="observed", evaluator_version="v1", status="x", outcome_values={}, recorded_at=at(16, 0))
    cases = [dict(outcome_key="a" * 64, setup_id=s1, kind="trade", window="trade", evidence_hash="e9"),     # 2nd root
             dict(outcome_key="b" * 64, setup_id=s2, kind="trade", window="trade", evidence_hash="e9",
                  supersedes_id=o1),                                                                      # cross-setup
             dict(outcome_key="c" * 64, setup_id=s1, kind="forecast", window="60", evidence_hash="e9",
                  supersedes_id=o1),                                                                      # cross-window
             dict(outcome_key="d" * 64, setup_id=s1, kind="trade", window="60", evidence_hash="e9"),         # bad window
             dict(outcome_key="f" * 64, setup_id=s1, kind="trade", window="trade", evidence_hash="e9",
                  supersedes_id=424242)]                                                                  # orphan
    for bad in cases:
        with pytest.raises(REFUSED):
            with engine.begin() as conn:
                conn.execute(m.outcomes.insert().values(**base, **bad))


# ── Mutation denial (CDR-04) ──────────────────────────────────────────────────────────────

@pg_only
def test_direct_update_and_unguarded_delete_are_refused_by_the_database(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
    for stmt in ("UPDATE intraday_setups SET stop = 1 WHERE id = :s",
                 "UPDATE intraday_setup_transitions SET reason = 'x' WHERE setup_id = :s",
                 "DELETE FROM intraday_setup_inputs WHERE setup_id = :s",
                 "DELETE FROM intraday_runs WHERE run_key = 'observed'"):
        with pytest.raises(DBAPIError, match="intraday_(insert_only|delete_refused)"):
            with engine.begin() as conn:
                conn.execute(text(stmt), {"s": sid})


# ── Seals, retention, deletion (CDR-R2-03) ────────────────────────────────────────────────

def _no_setup(conn, i, b, minute, sid=1):
    return store.record_evaluation(conn, batch_id=b, run_key="observed", identity=i, stock_id=sid, symbol="AAA",
                                   trigger_bar_open=at(15, minute), result="no_setup", detected_at=at(15, minute + 7),
                                   data_cutoff=at(15, minute + 7))


@pg_only
def test_a_late_evaluation_into_a_sealed_session_is_refused(engine):
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
        _no_setup(conn, i, b, 0)
        store.seal_session(conn, run_key="observed", identity=i, session_date=D, now=at(22, 0))
    with pytest.raises(DBAPIError, match="intraday_sealed"):
        with engine.begin() as conn:
            _no_setup(conn, i, b, 5)


def test_archive_is_atomic_and_retry_safe(engine):
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
        for mi in (0, 5, 10):
            _no_setup(conn, i, b, mi)
        a_setup(conn, i, b)
        with pytest.raises(c.ContractViolation):
            store.archive_session(conn, run_key="observed", identity=i, session_date=D, now=at(22, 0))
        store.seal_session(conn, run_key="observed", identity=i, session_date=D, now=at(22, 0))
    # a failing archive commits nothing
    with pytest.raises(RuntimeError):
        with engine.begin() as conn:
            store.archive_session(conn, run_key="observed", identity=i, session_date=D, now=at(22, 0))
            raise RuntimeError("crash after archive, before commit")
    with engine.begin() as conn:
        assert conn.execute(select(func.count()).select_from(m.evaluation_counts)).scalar() == 0
        first = store.archive_session(conn, run_key="observed", identity=i, session_date=D, now=at(22, 0))
    with engine.begin() as conn:
        again = store.archive_session(conn, run_key="observed", identity=i, session_date=D, now=at(23, 0))
        counts = dict(conn.execute(select(m.evaluation_counts.c.result, m.evaluation_counts.c.count)).all())
        remaining = conn.execute(select(m.evaluations.c.result)).scalars().all()
    assert first["deleted"] == 3 and again["deleted"] == 0
    assert counts == {"no_setup": 3, "setup": 1} and remaining == ["setup"]
    assert first["counts"] == again["counts"] == {"no_setup": 3, "setup": 1}    # a retry reports the archive


def test_whole_setup_deletion_leaves_no_orphans_and_never_takes_unresolved_exposure(engine):
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
        s_final = a_setup(conn, i, b)["setup_id"]
        s_open = a_setup(conn, i, b, symbol="BBB", stock_id=2)["setup_id"]
        store.append_transition(conn, setup_id=s_final, to_state="expired", event=ev(at(14, 20)), idempotency_key="f1")
        for k in ("e1", "e2"):
            store.record_outcome(conn, setup_id=s_final, kind="forecast", window="60", evaluator_version="v1",
                                 evidence_hash=k, status="resolved", values={}, now=at(16, 0))
        store.append_transition(conn, setup_id=s_open, to_state="entered", event=ev(at(14, 12)), idempotency_key="o1")
        store.append_transition(conn, setup_id=s_open, to_state="unresolved_exit_at_close", event=ev(at(20, 1)),
                                idempotency_key="o2")
    with engine.begin() as conn:
        store.delete_setup(conn, setup_id=s_final)
        with pytest.raises(c.ContractViolation):
            store.delete_setup(conn, setup_id=s_open)
        with pytest.raises(c.ContractViolation):
            store.purge_inputs(conn, setup_id=s_open)
    with engine.begin() as conn:
        for t in (m.outcomes, m.transitions, m.inputs):
            assert conn.execute(select(func.count()).select_from(t).where(t.c.setup_id == s_final)).scalar() == 0
        assert conn.execute(select(m.setups.c.id)).scalars().all() == [s_open]


# ── Read side (CDR-06, CDR-R2-04) ─────────────────────────────────────────────────────────

from src.services.intraday import read as R  # noqa: E402


def test_mode_and_run_rules_and_hk_refusal(engine):
    with engine.begin() as conn:
        for mode, rk in (("observed", "r1"), ("replay", None), ("replay", "observed"), (None, None), ("both", None)):
            with pytest.raises(R.BadRequest):
                R.list_identities(conn, market="US", mode=mode, run_key=rk, session_date=D)
        from common.horizons import UnsupportedMarket
        with pytest.raises(UnsupportedMarket):
            R.list_identities(conn, market="HK", mode="observed", run_key=None, session_date=D)
        out = R.list_identities(conn, market="US", mode="observed", run_key=None, session_date=D)
        assert out["production_generation"] == "disabled" and out["identities"] == []


def test_two_replay_runs_and_two_identities_are_never_pooled(engine):
    with engine.begin() as conn:
        i1, i2 = ident(conn), ident(conn, strategy_source_hash="t" * 64)
        for rk in ("r1", "r2"):
            store.ensure_replay_run(conn, rk, input_snapshot_hash="h" + rk, as_of=at(21, 0), now=at(21, 0))
        a_setup(conn, i1, batch(conn, i1, "r1"), run_key="r1")
        a_setup(conn, i1, batch(conn, i1, "r2"), run_key="r2", symbol="BBB", stock_id=2)
        a_setup(conn, i2, batch(conn, i2, "r1"), run_key="r1", symbol="CCC", stock_id=3)
        r1 = R.list_setups(conn, market="US", mode="replay", run_key="r1", identity=i1, session_date=D)
        assert [x["symbol"] for x in r1["items"]] == ["AAA"] and r1["identity"]["identity_hash"] == i1
        with pytest.raises(R.BadRequest):
            R.list_setups(conn, market="US", mode="replay", run_key="r1", identity=None, session_date=D)
        ids = R.list_identities(conn, market="US", mode="replay", run_key="r1", session_date=D)["identities"]
        assert sorted(x["identity_hash"] for x in ids) == sorted([i1, i2])


def test_no_setup_evaluations_page_once_each_on_a_sealed_session(engine):
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
        for sid, sym in ((1, "AAA"), (2, "BBB"), (3, "CCC")):
            for mi in (0, 5):
                store.record_evaluation(conn, batch_id=b, run_key="observed", identity=i, stock_id=sid, symbol=sym,
                                        trigger_bar_open=at(15, mi), result="no_setup", detected_at=at(15, mi + 7),
                                        data_cutoff=at(15, mi + 7))
        first = R.list_evaluations(conn, market="US", mode="observed", run_key=None, identity=i, session_date=D, limit=4)
        assert first["membership"] == "changing_view"
        store.seal_session(conn, run_key="observed", identity=i, session_date=D, now=at(22, 0))
        seen, cur = [], None
        while True:
            page = R.list_evaluations(conn, market="US", mode="observed", run_key=None, identity=i, session_date=D,
                                      cursor=cur, limit=4)
            seen += [x["evaluation_key"] for x in page["items"]]
            cur = page["next_cursor"]
            if not cur:
                break
        assert page["membership"] == "sealed" and len(seen) == 6 == len(set(seen))
        assert page["coverage"]["by_result"] == {"no_setup": 6}
        with pytest.raises(R.BadRequest):
            R.list_evaluations(conn, market="US", mode="observed", run_key=None, identity=i, session_date=D,
                               cursor="garbage", limit=4)


def test_late_insert_behind_the_cursor_follows_the_changing_view_rule(engine):
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
        for mi in (0, 10):
            store.record_evaluation(conn, batch_id=b, run_key="observed", identity=i, stock_id=1, symbol="AAA",
                                    trigger_bar_open=at(15, mi), result="no_setup", detected_at=at(15, mi + 7),
                                    data_cutoff=at(15, mi + 7))
        p1 = R.list_evaluations(conn, market="US", mode="observed", run_key=None, identity=i, session_date=D, limit=1)
        # a late row with an EARLIER key than the cursor arrives between pages
        store.record_evaluation(conn, batch_id=b, run_key="observed", identity=i, stock_id=1, symbol="AAA",
                                trigger_bar_open=at(14, 55), result="wait", detected_at=at(15, 30),
                                data_cutoff=at(15, 30))
        p2 = R.list_evaluations(conn, market="US", mode="observed", run_key=None, identity=i, session_date=D,
                                cursor=p1["next_cursor"], limit=10)
        assert p1["membership"] == "changing_view"
        assert len(p1["items"]) + len(p2["items"]) == 2                    # missed, as the view states
        assert R.list_evaluations(conn, market="US", mode="observed", run_key=None, identity=i,
                                  session_date=D, limit=10)["coverage"]["by_result"] == {"no_setup": 2, "wait": 1}


def test_unresolved_exposure_stays_visible_after_its_session(engine):
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
        sid = a_setup(conn, i, b)["setup_id"]
        a_setup(conn, i, b, symbol="BBB", stock_id=2)
        store.append_transition(conn, setup_id=sid, to_state="entered", event=ev(at(14, 12)), idempotency_key="e")
        store.append_transition(conn, setup_id=sid, to_state="unresolved_exit_at_close", event=ev(at(20, 1)),
                                idempotency_key="u")
        out = R.list_unresolved(conn, market="US", mode="observed", run_key=None)
        assert [x["symbol"] for x in out["items"]] == ["AAA"] and out["items"][0]["execution_grade"] is False
        store.append_transition(conn, setup_id=sid, to_state="reconciled", event=ev(at(20, 30)), idempotency_key="r")
        assert R.list_unresolved(conn, market="US", mode="observed", run_key=None)["items"] == []


@pg_only
def test_partial_indexes_refuse_a_second_terminal_or_reconciliation_even_without_the_trigger(engine):
    """The one-session-terminal and one-reconciled indexes are backstops behind the append
    trigger and the pair CHECK. Witnessed with the trigger disabled inside the test transaction
    (rolled back), appending a stale-but-legal pair a second time."""
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
        store.append_transition(conn, setup_id=sid, to_state="entered", event=ev(at(14, 12)), idempotency_key="e")
        store.append_transition(conn, setup_id=sid, to_state="unresolved_exit_at_close", event=ev(at(20, 1)),
                                idempotency_key="u")
        store.append_transition(conn, setup_id=sid, to_state="reconciled", event=ev(at(20, 30)), idempotency_key="r")
    raw = dict(setup_id=sid, run_key="observed", decided_at=at(21, 0), clock_basis="observed")
    for extra in (dict(seq=6, from_state="unresolved_exit_at_close", to_state="reconciled", idempotency_key="r2"),
                  dict(seq=6, from_state="entered", to_state="unresolved_exit_at_close", idempotency_key="u2")):
        with pytest.raises(IntegrityError, match="uq_intraday_one_"):
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE intraday_setup_transitions DISABLE TRIGGER intraday_transition_append"))
                conn.execute(m.transitions.insert().values(**raw, **extra))
