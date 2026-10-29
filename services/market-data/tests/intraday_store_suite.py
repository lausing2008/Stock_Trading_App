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


def an_input(trigger, run_key=c.OBSERVED_RUN, stock_id=1, symbol="AAA"):
    """A native 5m input whose hashes are computed from its evidence, as the reader does."""
    ev = c.bar_evidence(dict(stock_id=stock_id, symbol=symbol, timeframe="5m", bar_open=trigger, open=100.0,
                             high=100.5, low=99.5, close=100.2, adj_close=None, volume=1000.0, session="REGULAR"),
                        "unusual_whales", "provenance_matches",
                        dict(timeframe="5m", bar_open=trigger, open=100.0, high=100.5, low=99.5, close=100.2,
                             volume=1000.0, price_basis="raw", price_basis_reason="no adjusted close"))
    h = c.hashes_from_evidence(ev)
    return c.InputRecord(role="trigger", timeframe="5m", bar_open=trigger, source="native", values=c.consumed_values(ev),
                         raw_hash=h["raw_hash"], source_hash=h["source_hash"], input_hash=h["input_hash"],
                         derived_hash=None, readable_at=trigger + timedelta(minutes=7),
                         input_read_at=trigger + timedelta(minutes=7),
                         read_clock_basis="observed" if run_key == c.OBSERVED_RUN else "replay_logical", evidence=ev)


def a_setup(conn, identity, b, *, symbol="AAA", trigger=None, run_key=c.OBSERVED_RUN, stock_id=1):
    trigger = trigger or at(14, 0)
    det = c.decide_at_detection(trigger_bar_open=trigger, detected_at=trigger + timedelta(minutes=7), direction="bullish",
                                times=T, stop_breached=False, inputs_ok=True)
    inp = an_input(trigger, run_key, stock_id=stock_id, symbol=symbol)
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
    raw = dict(setup_id=sid, run_key="observed", decided_at=at(14, 12), effective_at=at(14, 12), clock_basis="observed")
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
    raw = dict(setup_id=sid, run_key="observed", decided_at=at(21, 0), effective_at=at(21, 0), clock_basis="observed")
    for extra in (dict(seq=6, from_state="unresolved_exit_at_close", to_state="reconciled", idempotency_key="r2"),
                  dict(seq=6, from_state="entered", to_state="unresolved_exit_at_close", idempotency_key="u2")):
        with pytest.raises(IntegrityError, match="uq_intraday_one_"):
            with engine.begin() as conn:
                conn.execute(text("ALTER TABLE intraday_setup_transitions DISABLE TRIGGER intraday_transition_append"))
                conn.execute(m.transitions.insert().values(**raw, **extra))



# ── Review IC-01: a committed seal freezes membership ─────────────────────────────────────

def _blocked_on_advisory_lock(engine):
    with engine.connect() as conn:
        return conn.execute(text("SELECT count(*) FROM pg_locks WHERE locktype = 'advisory' AND NOT granted")).scalar() > 0


@pg_only
def test_a_seal_waits_for_an_in_flight_insert_and_then_includes_it(engine):
    import time
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
    writer = engine.connect()
    tx = writer.begin()
    _no_setup(writer, i, b, 0)                                   # in flight, holds the session lock
    sealed = {}

    def seal():
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL lock_timeout = '20s'"))     # a failing run cannot hang
            store.seal_session(conn, run_key="observed", identity=i, session_date=D, now=at(22, 0))
            sealed["count_at_seal"] = conn.execute(select(func.count()).select_from(m.evaluations)).scalar()
    t = threading.Thread(target=seal, daemon=True)
    t.start()
    try:
        deadline = time.time() + 10
        while not _blocked_on_advisory_lock(engine) and time.time() < deadline:
            time.sleep(0.05)
        assert t.is_alive() and _blocked_on_advisory_lock(engine), "the sealer must wait for the in-flight insert"
    finally:
        tx.commit()                                                    # always release the held lock
        writer.close()
        t.join(30)
    assert not t.is_alive(), "both transactions must finish"
    assert sealed["count_at_seal"] == 1                         # the committed row is inside the seal
    with pytest.raises(DBAPIError, match="intraday_sealed"):    # and nothing can join afterwards
        with engine.begin() as conn:
            _no_setup(conn, i, b, 5)
    with engine.begin() as conn:                                # final state: membership == the seal's view
        assert conn.execute(select(func.count()).select_from(m.evaluations)).scalar() == sealed["count_at_seal"]
        assert conn.execute(select(func.count()).select_from(m.seals)).scalar() == 1
        assert store.archive_session(conn, run_key="observed", identity=i, session_date=D, now=at(23, 0))["counts"] == {"no_setup": 1}


@pg_only
def test_an_insert_behind_an_open_seal_waits_and_is_then_refused(engine):
    import time
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
    sealer = engine.connect()
    tx = sealer.begin()
    store.seal_session(sealer, run_key="observed", identity=i, session_date=D, now=at(22, 0))
    outcome = {}

    def insert():
        try:
            with engine.begin() as conn:
                conn.execute(text("SET LOCAL lock_timeout = '20s'"))
                _no_setup(conn, i, b, 0)
            outcome["r"] = "inserted"
        except DBAPIError as exc:
            outcome["r"] = "refused" if "intraday_sealed" in str(exc) else repr(exc)
    t = threading.Thread(target=insert, daemon=True)
    t.start()
    try:
        deadline = time.time() + 10
        while not _blocked_on_advisory_lock(engine) and time.time() < deadline:
            time.sleep(0.05)
        assert t.is_alive() and _blocked_on_advisory_lock(engine), "the insert must wait behind the open seal"
    finally:
        tx.commit()
        sealer.close()
        t.join(30)
    assert not t.is_alive(), "both transactions must finish"
    assert outcome["r"] == "refused"
    with engine.begin() as conn:                                # final state: sealed, and nothing joined
        assert conn.execute(select(func.count()).select_from(m.seals)).scalar() == 1
        assert conn.execute(select(func.count()).select_from(m.evaluations)).scalar() == 0


@pg_only
def test_an_evaluation_insert_outside_read_committed_is_refused(engine):
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
    with pytest.raises(DBAPIError, match="requires READ COMMITTED"):
        with engine.connect().execution_options(isolation_level="REPEATABLE READ") as conn:
            with conn.begin():
                _no_setup(conn, i, b, 0)


# ── Review IC-02: clocks belong to the run; chronology never goes backwards ────────────────

def test_cross_mode_inputs_and_events_are_refused_by_the_writer(engine):
    with engine.begin() as conn:
        i = ident(conn)
        store.ensure_replay_run(conn, "r1", input_snapshot_hash="h", as_of=at(21, 0), now=at(21, 0))
        rb = batch(conn, i, "r1")
        det = c.decide_at_detection(trigger_bar_open=at(14, 0), detected_at=at(14, 7), direction="bullish", times=T,
                                    stop_breached=False, inputs_ok=True)
        with pytest.raises(c.ContractViolation, match="read clock"):
            store.record_evaluation(conn, batch_id=rb, run_key="r1", identity=i, stock_id=1, symbol="AAA",
                                    trigger_bar_open=at(14, 0), result="setup", detected_at=at(14, 7),
                                    data_cutoff=at(14, 7), setup=SETUP, input_records=[an_input(at(14, 0))],
                                    detection=det, times=T)
    with engine.begin() as conn:
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
        with pytest.raises(c.ContractViolation, match="clock on a observed record"):
            store.append_transition(conn, setup_id=sid, to_state="entered", idempotency_key="x",
                                    event=c.EventTimes(at(14, 9), at(14, 10), at(14, 10), "replay_logical"))


def test_an_observed_entry_before_detection_is_refused_by_the_writer(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
        with pytest.raises(c.ContractViolation):
            store.append_transition(conn, setup_id=sid, to_state="entered", idempotency_key="b",
                                    event=c.EventTimes(at(13, 30), at(13, 30), at(13, 30), "observed"))


@pg_only
def test_direct_sql_cannot_write_cross_mode_or_backwards_records(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
    base = dict(setup_id=sid, run_key="observed", seq=3, from_state="actionable", to_state="entered")
    for bad, why in ((dict(decided_at=at(14, 12), effective_at=at(14, 10), clock_basis="replay_logical",
                           idempotency_key="d1"), "clock_mode"),
                     (dict(decided_at=at(13, 30), effective_at=at(13, 30), clock_basis="observed",
                           idempotency_key="d2"), "chronology|entry fill"),
                     (dict(decided_at=at(14, 9), effective_at=at(14, 10), clock_basis="observed",
                           idempotency_key="d3"), "effective_before_decided"),
                     (dict(decided_at=at(14, 30), effective_at=at(14, 25), clock_basis="observed",
                           idempotency_key="d4"), "entry fill")):
        with pytest.raises(DBAPIError, match=why):
            with engine.begin() as conn:
                conn.execute(m.transitions.insert().values(**base, **bad))
    with pytest.raises(DBAPIError, match="clock_mode"):
        with engine.begin() as conn:
            conn.execute(m.inputs.insert().values(
                setup_id=sid, run_key="observed", role="context", timeframe="15m", bar_open=at(13, 45),
                source="derived_from_5m", bar_values={}, evidence={"version": 1}, input_hash="x",
                readable_at=at(14, 2), input_read_at=at(14, 3), read_clock_basis="replay_logical",
                availability_basis="unknown_exact_version"))


def test_an_entry_after_its_expiry_is_refused_by_the_writer(engine):
    """Witness of the writer's entry-window check alone: forward in time (chronology fine), but
    the fill is after the setup's expiry."""
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
        with pytest.raises(c.ContractViolation, match="entry fill outside"):
            store.append_transition(conn, setup_id=sid, to_state="entered", effective_at=at(14, 25),
                                    idempotency_key="late", event=ev(at(14, 26)))


@pg_only
def test_direct_sql_cannot_record_an_exit_before_its_entry(engine):
    """Witness of the trigger's chronology check alone: entered -> exited is a legal pair and
    not an entry, but its effective time precedes the entry's."""
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
        store.append_transition(conn, setup_id=sid, to_state="entered", effective_at=at(14, 10),
                                idempotency_key="e", event=ev(at(14, 12)))
    with pytest.raises(DBAPIError, match="chronology goes backwards"):
        with engine.begin() as conn:
            conn.execute(m.transitions.insert().values(
                setup_id=sid, run_key="observed", seq=4, from_state="entered", to_state="exited",
                effective_at=at(14, 5), decided_at=at(14, 30), clock_basis="observed", idempotency_key="x"))


def test_a_scheduled_exit_recorded_later_keeps_its_fill_time(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
        store.append_transition(conn, setup_id=sid, to_state="entered", effective_at=at(14, 10), idempotency_key="e",
                                event=c.EventTimes(at(14, 12), at(14, 12), at(14, 12), "observed"))
        store.append_transition(conn, setup_id=sid, to_state="exited", reason="max_hold", effective_at=at(16, 10),
                                idempotency_key="x", event=c.EventTimes(at(16, 17), at(16, 17), at(16, 18), "observed"))
        rows = conn.execute(select(m.transitions.c.to_state, m.transitions.c.effective_at, m.transitions.c.decided_at)
                            .where(m.transitions.c.setup_id == sid).order_by(m.transitions.c.seq)).all()
    eff = {r.to_state: (c.utc(r.effective_at), c.utc(r.decided_at)) for r in rows}
    assert eff["entered"] == (at(14, 10), at(14, 12)) and eff["exited"] == (at(16, 10), at(16, 18))


@pg_only
def test_a_concurrent_identical_retry_returns_the_stored_row(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
    barrier, out = threading.Barrier(2), []

    def go():
        with engine.begin() as conn:
            barrier.wait()
            out.append(store.append_transition(conn, setup_id=sid, to_state="entered", idempotency_key="same",
                                               event=ev(at(14, 12))))
    ts = [threading.Thread(target=go) for _ in range(2)]
    [t.start() for t in ts]
    [t.join(10) for t in ts]
    assert sorted(o["created"] for o in out) == [False, True] and {o["seq"] for o in out} == {3}


def test_tampered_evidence_is_refused_before_any_write(engine):
    """IC-03: an input whose stated hashes do not recompute from its evidence is refused, and
    nothing is written."""
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
        bad = an_input(at(14, 0))
        bad.evidence = dict(bad.evidence, transformed=dict(bad.evidence["transformed"], close=999.0))
        det = c.decide_at_detection(trigger_bar_open=at(14, 0), detected_at=at(14, 7), direction="bullish", times=T,
                                    stop_breached=False, inputs_ok=True)
        with pytest.raises(c.ContractViolation, match="recompute"):
            store.record_evaluation(conn, batch_id=b, run_key="observed", identity=i, stock_id=1, symbol="AAA",
                                    trigger_bar_open=at(14, 0), result="setup", detected_at=at(14, 7),
                                    data_cutoff=at(14, 7), setup=SETUP, input_records=[bad], detection=det, times=T)
        assert conn.execute(select(func.count()).select_from(m.evaluations)).scalar() == 0


def test_path_indeterminate_is_listed_as_open_exposure(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
        store.append_transition(conn, setup_id=sid, to_state="entered", effective_at=at(14, 10), idempotency_key="e",
                                event=ev(at(14, 12)))
        store.append_transition(conn, setup_id=sid, to_state="path_indeterminate", reason="unobserved_interval",
                                effective_at=at(14, 15), idempotency_key="p", event=ev(at(14, 22)))
        items = R.list_unresolved(conn, market="US", mode="observed", run_key=None)["items"]
        assert [(x["symbol"], x["state"]) for x in items] == [("AAA", "path_indeterminate")]
        with pytest.raises(c.ContractViolation):
            store.delete_setup(conn, setup_id=sid)                     # open exposure is never deleted


def test_an_archived_identity_stays_discoverable(engine):
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
        for mi in (0, 5):
            _no_setup(conn, i, b, mi)
        store.seal_session(conn, run_key="observed", identity=i, session_date=D, now=at(22, 0))
        store.archive_session(conn, run_key="observed", identity=i, session_date=D, now=at(22, 30))
        assert conn.execute(select(func.count()).select_from(m.evaluations)).scalar() == 0
        found = R.list_identities(conn, market="US", mode="observed", run_key=None, session_date=D)["identities"]
        assert [(x["identity_hash"], x["evaluations"]) for x in found] == [(i, 2)]


# ── Candidate review ICR-01: transition timing by event type ──────────────────────────────
# Fixture setup: trigger 14:00, detected/actionable 14:07, candidate entry open 14:10, expiry 14:20.

def _cancel(conn, sid, event, key="c", reason="input_revised", **kw):
    return store.append_transition(conn, setup_id=sid, to_state="cancelled", reason=reason, event=event,
                                   idempotency_key=key, **kw)


def _cancel_row(sid, **kw):
    row = dict(setup_id=sid, run_key="observed", seq=3, from_state="actionable", to_state="cancelled",
               reason="input_revised", clock_basis="observed", idempotency_key="raw")
    return {**row, **kw}


def test_an_observed_cancellation_cannot_be_backdated(engine):
    """The review's witness: a revision observed at 14:17, dated 14:08, would beat a 14:10 entry."""
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
        late = c.EventTimes(at(14, 8), at(14, 17), at(14, 17), "observed")
        with pytest.raises(c.ContractViolation, match="cannot be dated"):
            _cancel(conn, sid, late, effective_at=at(14, 8))
        with pytest.raises(c.ContractViolation, match="the entry stands"):
            _cancel(conn, sid, late)                               # at its real time it is after the entry
        assert states(conn, sid) == ["detected", "actionable"]


@pg_only
def test_direct_sql_cannot_backdate_or_unground_a_cancellation(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
    t = dict(evidence_eligible_at=at(14, 8), evidence_observed_at=at(14, 9), decided_at=at(14, 9))
    for bad, why in ((dict(evidence_eligible_at=at(14, 8), evidence_observed_at=at(14, 17), decided_at=at(14, 17),
                           effective_at=at(14, 8)), "takes effect when decided"),             # the review's witness
                     (dict(evidence_eligible_at=at(14, 8), evidence_observed_at=at(14, 17), decided_at=at(14, 17),
                           effective_at=at(14, 17)), "not before the candidate entry"),
                     (dict(t, effective_at=at(14, 9), evidence_observed_at=None), "with its observation"),
                     (dict(t, effective_at=at(14, 9), reason="session_end"), "not data-dependent"),
                     (dict(t, effective_at=at(14, 10), decided_at=at(14, 10)), "not before the candidate entry")):
        with pytest.raises(DBAPIError, match=why):
            with engine.begin() as conn:
                conn.execute(m.transitions.insert().values(**_cancel_row(sid, **bad)))
    with engine.begin() as conn:                                 # the same row, honestly timed, is accepted
        conn.execute(m.transitions.insert().values(**_cancel_row(sid, **t, effective_at=at(14, 9))))
        assert states(conn, sid) == ["detected", "actionable", "cancelled"]


def test_a_cancellation_decided_before_the_candidate_entry_defeats_it(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
        _cancel(conn, sid, c.EventTimes(at(14, 8), at(14, 9), at(14, 9), "observed"))
        with pytest.raises(c.ContractViolation):
            store.append_transition(conn, setup_id=sid, to_state="entered", effective_at=at(14, 10),
                                    idempotency_key="e", event=ev(at(14, 12)))
        row = conn.execute(select(m.transitions.c.effective_at).where(
            m.transitions.c.setup_id == sid, m.transitions.c.to_state == "cancelled")).scalar()
        assert c.utc(row) == at(14, 9) and states(conn, sid) == ["detected", "actionable", "cancelled"]


def test_a_revision_observed_after_the_candidate_entry_leaves_the_entry_standing(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
        for decided in (at(14, 12), at(14, 10)):                  # after the entry, and the exact tie
            with pytest.raises(c.ContractViolation, match="the entry stands"):
                _cancel(conn, sid, c.EventTimes(at(14, 8), decided, decided, "observed"), key=f"c{decided.minute}")
        store.append_transition(conn, setup_id=sid, to_state="entered", effective_at=at(14, 10), idempotency_key="e",
                                event=ev(at(14, 12)))
        assert states(conn, sid) == ["detected", "actionable", "entered"]


def test_a_cancellation_missing_its_observation_is_not_confirmed(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
        unknown = c.EventTimes(at(14, 8), None, at(14, 9), "observed")
        with pytest.raises(c.ContractViolation, match="chronology_ambiguous"):
            _cancel(conn, sid, unknown)
        with pytest.raises(c.ContractViolation, match="cannot be dated"):
            store.append_transition(conn, setup_id=sid, to_state="chronology_ambiguous", event=unknown,
                                    effective_at=at(14, 8), idempotency_key="a0")
        store.append_transition(conn, setup_id=sid, to_state="chronology_ambiguous", event=unknown, idempotency_key="a")
        assert states(conn, sid) == ["detected", "actionable", "chronology_ambiguous"]


def test_only_the_entry_bars_own_absence_cancels_after_it(engine):
    """The entry bar (14:10) is missing: its absence is knowable from its readable_at, 14:17."""
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
        s1, s2, s3 = (a_setup(conn, i, b, symbol=x, stock_id=n)["setup_id"] for n, x in ((1, "AAA"), (2, "BBB"), (3, "CCC")))
        absent = c.EventTimes(at(14, 17), at(14, 18), at(14, 18), "observed")
        with pytest.raises(c.ContractViolation, match="the entry stands"):
            _cancel(conn, s1, absent, reason="input_revised")                 # not the absence
        with pytest.raises(c.ContractViolation, match="the entry stands"):
            _cancel(conn, s2, c.EventTimes(at(14, 16), at(14, 18), at(14, 18), "observed"), reason="data_unavailable")
        _cancel(conn, s3, absent, reason="data_unavailable", key="ok")
        assert states(conn, s3)[-1] == "cancelled" and states(conn, s1)[-1] == states(conn, s2)[-1] == "actionable"


def test_scheduled_and_fill_times_recorded_later_remain_valid_but_expiry_is_fixed(engine):
    with engine.begin() as conn:
        i = ident(conn)
        b = batch(conn, i)
        sid = a_setup(conn, i, b)["setup_id"]
        store.append_transition(conn, setup_id=sid, to_state="entered", effective_at=at(14, 10), idempotency_key="e",
                                event=ev(at(14, 12)))
        store.append_transition(conn, setup_id=sid, to_state="exited", reason="liquidation", effective_at=T.liquidation_at,
                                idempotency_key="x", event=c.EventTimes(T.close, T.close, T.close + timedelta(minutes=5),
                                                                        "observed"))
        s2 = a_setup(conn, i, b, symbol="BBB", stock_id=2)["setup_id"]
        with pytest.raises(c.ContractViolation, match="cannot be dated"):
            store.append_transition(conn, setup_id=s2, to_state="expired", effective_at=at(14, 15),
                                    idempotency_key="x2", event=ev(at(14, 25)))
        store.append_transition(conn, setup_id=s2, to_state="expired", idempotency_key="x3", event=ev(at(14, 25)))
        rows = dict(conn.execute(select(m.transitions.c.idempotency_key, m.transitions.c.effective_at).where(
            m.transitions.c.idempotency_key.in_(("x", "x3")))).all())
    assert c.utc(rows["x"]) == T.liquidation_at and c.utc(rows["x3"]) == at(14, 20)


def test_a_replay_cancellation_takes_effect_at_eligible_plus_latency(engine):
    with engine.begin() as conn:
        i = ident(conn)
        store.ensure_replay_run(conn, "r1", input_snapshot_hash="h", as_of=at(21, 0), now=at(21, 0))
        sid = a_setup(conn, i, batch(conn, i, "r1"), run_key="r1")["setup_id"]
        rev = c.EventTimes(at(14, 8), None, at(14, 9), "replay_logical")
        with pytest.raises(c.ContractViolation, match="cannot be dated"):
            _cancel(conn, sid, rev, effective_at=at(14, 8))
        _cancel(conn, sid, rev)
        eff = conn.execute(select(m.transitions.c.effective_at).where(
            m.transitions.c.setup_id == sid, m.transitions.c.to_state == "cancelled")).scalar()
    assert c.utc(eff) == at(14, 9)                                   # 14:08 + the 60 s replay latency
    if PG:
        with engine.begin() as conn:
            s2 = a_setup(conn, i, batch(conn, i, "r1"), run_key="r1", symbol="BBB", stock_id=2)["setup_id"]
        with pytest.raises(DBAPIError, match="replay cancellation"):
            with engine.begin() as conn:
                conn.execute(m.transitions.insert().values(**_cancel_row(
                    s2, run_key="r1", clock_basis="replay_logical", evidence_eligible_at=at(14, 8),
                    decided_at=at(14, 9), effective_at=at(14, 8))))


# ── Candidate review ICR-02: consumed values are bound to their hashed evidence ───────────

def _native(t, *, stock_id=1, symbol="AAA", close=100.2, basis="raw", adj_close=None, high=100.5, volume=1000.0):
    return c.bar_evidence(dict(stock_id=stock_id, symbol=symbol, timeframe="5m", bar_open=t, open=100.0, high=high,
                               low=99.5, close=close, adj_close=adj_close, volume=volume, session="REGULAR"),
                          "unusual_whales", "provenance_matches",
                          dict(timeframe="5m", bar_open=t, open=100.0, high=high, low=99.5, close=close, volume=volume,
                               price_basis=basis, price_basis_reason="window basis"))


def _record(ev_, *, role="trigger", timeframe=None, bar_open=None, source=None, values=None):
    h = c.hashes_from_evidence(ev_)
    t = datetime.fromisoformat(ev_["transformed"]["bar_open"].replace("Z", "+00:00"))
    tf = timeframe or ev_["transformed"]["timeframe"]
    return c.InputRecord(role=role, timeframe=tf, bar_open=bar_open or t,
                         source=source or ("derived_from_5m" if "constituents" in ev_ else "native"),
                         values=c.consumed_values(ev_) if values is None else values, raw_hash=h["raw_hash"],
                         source_hash=h["source_hash"], input_hash=h["input_hash"], derived_hash=h["derived_hash"],
                         readable_at=c.readable_at(t, "15m" if "constituents" in ev_ else "5m", T), input_read_at=at(14, 7),
                         evidence=ev_)


def _derived(t, members):
    mts = [x["transformed"] for x in members]
    return c.derived_evidence(dict(timeframe="15m", bar_open=t, open=mts[0]["open"], high=max(x["high"] for x in mts),
                                   low=min(x["low"] for x in mts), close=mts[-1]["close"],
                                   volume=sum(x["volume"] for x in mts), price_basis=mts[0]["price_basis"],
                                   price_basis_reason=mts[0]["price_basis_reason"]), members)


def _write(conn, records, *, stock_id=1, symbol="AAA"):
    i = ident(conn)
    det = c.decide_at_detection(trigger_bar_open=at(14, 0), detected_at=at(14, 7), direction="bullish", times=T,
                                stop_breached=False, inputs_ok=True)
    return store.record_evaluation(conn, batch_id=batch(conn, i), run_key="observed", identity=i, stock_id=stock_id,
                                   symbol=symbol, trigger_bar_open=at(14, 0), result="setup", detected_at=at(14, 7),
                                   data_cutoff=at(14, 7), setup=SETUP, input_records=records, detection=det, times=T)


def _retimed(ev_, timeframe):
    return dict(ev_, transformed=dict(ev_["transformed"], timeframe=timeframe))


_GOOD = _native(at(14, 0))
_TAMPERED = {
    "consumed close": lambda: _record(_GOOD, values=dict(c.consumed_values(_GOOD), close=199.0)),
    "price basis": lambda: _record(_GOOD, values=dict(c.consumed_values(_GOOD), price_basis="adjusted")),
    "timeframe": lambda: _record(_GOOD, timeframe="15m"),
    "bar open": lambda: _record(_GOOD, bar_open=at(14, 5)),
    "source kind": lambda: _record(_GOOD, source="derived_from_5m"),
    "derived missing a constituent": lambda: _record(
        _derived(at(13, 45), [_native(at(13, 45)), _native(at(13, 50))]), role="context"),
    "derived not 15m": lambda: _record(_retimed(_derived(at(13, 45), [_native(at(13, 45 + 5 * k)) for k in range(3)]),
                                                "1h"), role="context"),
    "raw and transformed bar opens differ": lambda: _record(dict(_GOOD, raw=dict(_GOOD["raw"],
                                                                                bar_open=c._norm(at(14, 5))))),
    "constituent is itself derived": lambda: _record(_derived(at(13, 45), [
        _derived(at(13, 45), [_native(at(13, 45 + 5 * k)) for k in range(3)])] * 3), role="context"),
    "derived not the aggregate": lambda: _record(dict(
        _derived(at(13, 45), [_native(at(13, 45 + 5 * k)) for k in range(3)]),
        transformed=dict(_derived(at(13, 45), [_native(at(13, 45 + 5 * k)) for k in range(3)])["transformed"],
                         high=101.0)), role="context"),
}


@pytest.mark.parametrize("case", sorted(_TAMPERED))
def test_an_input_disagreeing_with_its_evidence_is_refused_before_any_write(engine, case):
    rec = _TAMPERED[case]()
    assert c.hashes_from_evidence(rec.evidence)["input_hash"] == rec.input_hash      # the hashes still recompute
    with engine.begin() as conn:
        with pytest.raises(c.ContractViolation):
            _write(conn, [rec])
        assert conn.execute(select(func.count()).select_from(m.evaluations)).scalar() == 0


def test_an_input_from_another_stock_is_refused(engine):
    with engine.begin() as conn:
        with pytest.raises(c.ContractViolation, match="another stock"):
            _write(conn, [_record(_GOOD)], stock_id=2, symbol="BBB")
        assert conn.execute(select(func.count()).select_from(m.evaluations)).scalar() == 0


def test_untampered_native_adjusted_and_derived_inputs_round_trip_from_storage_alone(engine):
    records = [_record(_GOOD),
               _record(_native(at(13, 55), basis="adjusted", adj_close=50.1), role="lookback"),
               _record(_derived(at(13, 45), [_native(at(13, 45 + 5 * k), close=100 + k, volume=10.0 * (k + 1))
                                             for k in range(3)]), role="context")]
    with engine.begin() as conn:
        sid = _write(conn, records)["setup_id"]
    with engine.begin() as conn:                                     # no source table exists in this database
        rows = conn.execute(select(m.inputs).where(m.inputs.c.setup_id == sid).order_by(m.inputs.c.id)).all()
    assert [r.source for r in rows] == ["native", "native", "derived_from_5m"]
    for r, rec in zip(rows, records):
        assert r.bar_values == c.consumed_values(r.evidence) == rec.values
        assert c.hashes_from_evidence(r.evidence) == dict(raw_hash=r.raw_hash, source_hash=r.source_hash,
                                                          input_hash=r.input_hash, derived_hash=r.derived_hash)
        assert r.first_available_at is None and r.availability_basis == "unknown_exact_version"


def test_a_bracket_rejection_is_a_fill_attempt_inside_the_entry_window(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
        with pytest.raises(c.ContractViolation, match="entry fill outside"):
            store.append_transition(conn, setup_id=sid, to_state="rejected_invalid_bracket_at_fill",
                                    effective_at=at(14, 25), idempotency_key="r", event=ev(at(14, 26)))
        store.append_transition(conn, setup_id=sid, to_state="rejected_invalid_bracket_at_fill",
                                effective_at=at(14, 10), idempotency_key="r2", event=ev(at(14, 12)))


@pg_only
def test_direct_sql_cannot_retime_expiry_decisions_detection_or_a_rejection(engine):
    with engine.begin() as conn:
        i = ident(conn)
        sid = a_setup(conn, i, batch(conn, i))["setup_id"]
    row = dict(setup_id=sid, run_key="observed", seq=3, from_state="actionable", clock_basis="observed",
               idempotency_key="raw", decided_at=at(14, 25))
    for bad, why in ((dict(to_state="expired", effective_at=at(14, 15)), "at expiry"),
                     (dict(to_state="chronology_ambiguous", effective_at=at(14, 8)), "when decided"),
                     (dict(to_state="rejected_invalid_bracket_at_fill", effective_at=at(14, 22)), "entry fill")):
        with pytest.raises(DBAPIError, match=why):
            with engine.begin() as conn:
                conn.execute(m.transitions.insert().values(**{**row, **bad}))
    with pytest.raises(DBAPIError, match="decided at detection"):
        with engine.begin() as conn:                                # a detection-time state dated later
            eid = conn.execute(m.evaluations.insert().values(
                evaluation_key="k" * 64, batch_id=batch(conn, i), run_key="observed", identity_hash=i, stock_id=9,
                symbol="ZZZ", session_date=D, trigger_bar_open=at(14, 0), result="setup", detected_at=at(14, 7),
                data_cutoff=at(14, 7)).returning(m.evaluations.c.id)).scalar()
            s2 = conn.execute(m.setups.insert().values(
                occurrence_key="o" * 64, evaluation_id=eid, run_key="observed", identity_hash=i, stock_id=9,
                symbol="ZZZ", session_date=D, trigger_bar_open=at(14, 0), direction="bullish", entry_reference=1,
                stop=0.5, target=2, risk_per_share=0.5, reasons={}, detected_at=at(14, 7), actionable_at=at(14, 7),
                entry_expires_at=at(14, 20), last_entry_at=at(19, 30), liquidation_at=at(19, 50),
                session_close=at(20, 0)).returning(m.setups.c.id)).scalar()
            base = dict(setup_id=s2, run_key="observed", clock_basis="observed")
            conn.execute(m.transitions.insert().values(**base, seq=1, from_state=None, to_state="detected",
                                                       decided_at=at(14, 7), effective_at=at(14, 7), idempotency_key="d1"))
            conn.execute(m.transitions.insert().values(**base, seq=2, from_state="detected", to_state="actionable",
                                                       decided_at=at(14, 9), effective_at=at(14, 9), idempotency_key="d2"))
