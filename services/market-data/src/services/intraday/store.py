"""Intraday Stage C writers (design §9). Insert-only: there is no update path in this module.

Each function runs inside the caller's transaction (`with engine.begin() as conn`). Duplicate
writes are idempotent (`ON CONFLICT DO NOTHING`, then read back). Transition and outcome appends
lock the setup row; on PostgreSQL the database trigger repeats the append validation, so a writer
that forgot the lock is still refused.

No generator calls these in Stage C; they exist so the contract is enforced and tested before one
is written.
"""
from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import and_, func, select, text

from . import contract as c
from .models import (batches, evaluation_counts, evaluations, identities, inputs, outcomes, runs, seals, setups,
                     transitions)


def _naive(dt):
    return None if dt is None else c.utc(dt).replace(tzinfo=None)


def _insert(conn, table):
    if conn.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return insert(table)


def _basis_for(run_key):
    """The only clock basis a record of this run may carry (review IC-02)."""
    return "observed" if run_key == c.OBSERVED_RUN else "replay_logical"


def _session_lock(conn, run_key, identity, session_date):
    """The lock every evaluation/setup insert takes in its trigger (review IC-01)."""
    if conn.dialect.name == "postgresql":
        conn.execute(text("SELECT intraday_session_lock(:rk, :ih, :sd)"),
                     {"rk": run_key, "ih": identity, "sd": session_date})


def _lock_setup(conn, setup_id):
    q = select(setups.c.id, setups.c.run_key, setups.c.detected_at, setups.c.actionable_at,
               setups.c.entry_expires_at).where(setups.c.id == setup_id)
    if conn.dialect.name == "postgresql":
        q = q.with_for_update()
    row = conn.execute(q).first()
    if row is None:
        raise c.ContractViolation(f"setup {setup_id} does not exist")
    return row


# ── Runs, identities, batches ─────────────────────────────────────────────────────────────

def ensure_replay_run(conn, run_key: str, *, input_snapshot_hash: str, as_of: datetime, now: datetime) -> str:
    if run_key == c.OBSERVED_RUN:
        raise c.ContractViolation("'observed' is the observed namespace, not a replay run")
    conn.execute(_insert(conn, runs).values(run_key=run_key, record_mode="replay", input_snapshot_hash=input_snapshot_hash,
                                            as_of=_naive(as_of), created_at=_naive(now))
                 .on_conflict_do_nothing(index_elements=["run_key"]))
    row = conn.execute(select(runs).where(runs.c.run_key == run_key)).first()
    if row.record_mode != "replay" or row.input_snapshot_hash != input_snapshot_hash:
        raise c.ContractViolation(f"replay run {run_key} exists with a different identity")
    return run_key


def ensure_identity(conn, *, strategy_version: str, strategy_source_hash: str, params: c.Params,
                    strategy_id: str = c.STRATEGY_ID) -> str:
    cfg = c.config_hash(params)
    ident = c.identity_hash(strategy_id=strategy_id, strategy_version=strategy_version,
                            strategy_source_hash=strategy_source_hash, contract_version=c.CONTRACT_VERSION,
                            config_hash=cfg)
    conn.execute(_insert(conn, identities).values(
        identity_hash=ident, strategy_id=strategy_id, strategy_version=strategy_version,
        strategy_source_hash=strategy_source_hash, contract_version=c.CONTRACT_VERSION, config_hash=cfg,
        config=params.as_config()).on_conflict_do_nothing(index_elements=["identity_hash"]))
    return ident


def start_batch(conn, *, run_key: str, identity: str, code_version: str, started_at, data_cutoff) -> str:
    mode = "observed" if run_key == c.OBSERVED_RUN else "replay"
    batch_id = uuid.uuid4().hex
    conn.execute(batches.insert().values(batch_id=batch_id, run_key=run_key, record_mode=mode, identity_hash=identity,
                                         code_version=code_version, started_at=_naive(started_at),
                                         data_cutoff=_naive(data_cutoff)))
    return batch_id


# ── Evaluations and setups ────────────────────────────────────────────────────────────────

def record_evaluation(conn, *, batch_id: str, run_key: str, identity: str, stock_id: int, symbol: str,
                      trigger_bar_open, result: str, detected_at, data_cutoff, setup: dict | None = None,
                      input_records: list | None = None, detection: c.Detection | None = None,
                      times: c.SessionTimes | None = None) -> dict:
    """Write one evaluation; for result 'setup' also its setup, inputs, the first transition and
    the detection-time transition, all in this transaction. Returns {'evaluation_id',
    'setup_id', 'created'}. A duplicate (same evaluation_key) writes nothing."""
    if result not in c.RESULTS:
        raise c.ContractViolation(f"unknown result {result!r}")
    if (result == "setup") != (setup is not None):
        raise c.ContractViolation("a setup is written exactly when result == 'setup'")
    # Every check that can refuse runs BEFORE the first insert: nothing half-written.
    basis = _basis_for(run_key)
    for i in input_records or []:
        if i.read_clock_basis != basis:
            raise c.ContractViolation(f"input read clock {i.read_clock_basis} in a {basis} run")
        stated = {"raw_hash": i.raw_hash, "source_hash": i.source_hash, "input_hash": i.input_hash,
                  "derived_hash": i.derived_hash}
        if c.hashes_from_evidence(i.evidence) != stated:
            raise c.ContractViolation("input hashes do not recompute from its evidence")
    if setup is not None:
        if detection is None or times is None:
            raise c.ContractViolation("a setup needs its detection decision and session times")
        c.check_setup_invariants(detected_at=detected_at, actionable_at=detection.actionable_at,
                                 entry_expires_at=detection.entry_expires_at, times=times,
                                 inputs=[dict(readable_at=i.readable_at, input_read_at=i.input_read_at)
                                         for i in (input_records or [])])
    tbo = c.utc(trigger_bar_open)
    ekey = c.evaluation_key(run_key=run_key, identity=identity, stock_id=stock_id, trigger_bar_open=tbo)
    sdate = c.session_date_of(tbo)
    ins = _insert(conn, evaluations).values(
        evaluation_key=ekey, batch_id=batch_id, run_key=run_key, identity_hash=identity, stock_id=stock_id,
        symbol=symbol.upper(), session_date=sdate, trigger_bar_open=_naive(tbo), result=result,
        detected_at=_naive(detected_at), data_cutoff=_naive(data_cutoff),
    ).on_conflict_do_nothing(index_elements=["evaluation_key"]).returning(evaluations.c.id)
    new_id = conn.execute(ins).scalar()
    if new_id is None:
        existing = conn.execute(select(evaluations.c.id, evaluations.c.result).where(
            evaluations.c.evaluation_key == ekey)).first()
        sid = conn.execute(select(setups.c.id).where(setups.c.evaluation_id == existing.id)).scalar()
        return {"evaluation_id": existing.id, "setup_id": sid, "created": False}
    if setup is None:
        return {"evaluation_id": new_id, "setup_id": None, "created": True}

    direction = setup["direction"]
    okey = c.occurrence_key(run_key=run_key, identity=identity, symbol=symbol, trigger_bar_open=tbo,
                            direction=direction)
    sid = conn.execute(setups.insert().values(
        occurrence_key=okey, evaluation_id=new_id, run_key=run_key, identity_hash=identity, stock_id=stock_id,
        symbol=symbol.upper(), session_date=sdate, trigger_bar_open=_naive(tbo), direction=direction,
        entry_reference=setup["entry_reference"], stop=setup["stop"], target=setup["target"],
        risk_per_share=setup["risk_per_share"], reasons=setup.get("reasons") or {},
        detected_at=_naive(detected_at), actionable_at=_naive(detection.actionable_at),
        entry_expires_at=_naive(detection.entry_expires_at), last_entry_at=_naive(times.last_entry_at),
        liquidation_at=_naive(times.liquidation_at), session_close=_naive(times.close),
    ).returning(setups.c.id)).scalar()
    for i in input_records or []:
        conn.execute(inputs.insert().values(
            setup_id=sid, run_key=run_key, role=i.role, timeframe=i.timeframe, bar_open=_naive(i.bar_open),
            source=i.source, bar_values=i.values, evidence=i.evidence, raw_hash=i.raw_hash, source_hash=i.source_hash,
            input_hash=i.input_hash, derived_hash=i.derived_hash, readable_at=_naive(i.readable_at),
            input_read_at=_naive(i.input_read_at), read_clock_basis=i.read_clock_basis,
            first_available_at=_naive(i.first_available_at),
            availability_basis=i.availability_basis, provider_published_at=_naive(i.provider_published_at)))
    conn.execute(transitions.insert().values(
        setup_id=sid, run_key=run_key, seq=1, from_state=None, to_state="detected", decided_at=_naive(detected_at),
        effective_at=_naive(detected_at), clock_basis=basis, idempotency_key=c.sha(["t", okey, 1])))
    conn.execute(transitions.insert().values(
        setup_id=sid, run_key=run_key, seq=2, from_state="detected", to_state=detection.state,
        reason=detection.reason, decided_at=_naive(detected_at), effective_at=_naive(detected_at),
        clock_basis=basis, idempotency_key=c.sha(["t", okey, 2])))
    return {"evaluation_id": new_id, "setup_id": sid, "created": True}


# ── Transitions ───────────────────────────────────────────────────────────────────────────

def append_transition(conn, *, setup_id: int, to_state: str, event: c.EventTimes, reason: str | None = None,
                      evidence: dict | None = None, idempotency_key: str, effective_at=None) -> dict:
    """Append one state change after locking the setup. A repeat of the same idempotency key is
    a no-op that returns the stored row (checked again under the lock, so a concurrent identical
    retry returns the stored row rather than hitting the unique key).

    `effective_at` is when the research event happened (an entry fill's bar open, a scheduled
    exit's open); it defaults to `decided_at` for a decision. It may precede `decided_at` (a fill
    recorded later) but never the previous transition's, and neither time may go backwards. The
    clock basis must be the run's own: observed runs observe, replays use the logical clock."""
    def _done():
        row = conn.execute(select(transitions.c.seq).where(transitions.c.idempotency_key == idempotency_key)).first()
        return None if row is None else {"seq": row.seq, "created": False}
    if (d := _done()) is not None:
        return d
    setup = _lock_setup(conn, setup_id)
    if (d := _done()) is not None:
        return d
    if event.clock_basis != _basis_for(setup.run_key):
        raise c.ContractViolation(f"{event.clock_basis} clock on a {_basis_for(setup.run_key)} record")
    if event.decided_at is None:
        raise c.ContractViolation("a transition needs its decided_at")
    if event.clock_basis == "observed":
        c.effective_time(event)            # validates eligible <= observed <= decided
    eff = c.utc(effective_at) if effective_at is not None else c.utc(event.decided_at)
    if eff > c.utc(event.decided_at):
        raise c.ContractViolation("an event cannot take effect after it was decided/recorded")
    latest = conn.execute(select(transitions.c.seq, transitions.c.to_state, transitions.c.effective_at,
                                 transitions.c.decided_at)
                          .where(transitions.c.setup_id == setup_id)
                          .order_by(transitions.c.seq.desc()).limit(1)).first()
    latest_t = (latest.seq, latest.to_state) if latest else None
    seq = (latest.seq + 1) if latest else 1
    from_state = latest.to_state if latest else None
    c.check_append(latest_t, seq, from_state, to_state)
    if latest is not None and (eff < c.utc(latest.effective_at) or c.utc(event.decided_at) < c.utc(latest.decided_at)):
        raise c.ContractViolation("transition chronology goes backwards")
    if to_state == "entered" and not (setup.actionable_at is not None
                                      and c.utc(setup.actionable_at) < eff < c.utc(setup.entry_expires_at)):
        raise c.ContractViolation("entry fill outside (actionable_at, entry_expires_at)")
    conn.execute(transitions.insert().values(
        setup_id=setup_id, run_key=setup.run_key, seq=seq, from_state=from_state, to_state=to_state,
        evidence_eligible_at=_naive(event.eligible_at), evidence_observed_at=_naive(event.observed_at),
        decided_at=_naive(event.decided_at), effective_at=_naive(eff), clock_basis=event.clock_basis,
        reason=reason, evidence=evidence, idempotency_key=idempotency_key))
    return {"seq": seq, "created": True}


# ── Outcomes ──────────────────────────────────────────────────────────────────────────────

def record_outcome(conn, *, setup_id: int, kind: str, window: str, evaluator_version: str, evidence_hash: str,
                   status: str, values: dict, now) -> dict:
    """Append an outcome revision on the (setup, kind, window) chain. Identical evidence under the
    same evaluator is a duplicate; changed evidence is a new revision superseding the tip."""
    setup = _lock_setup(conn, setup_id)
    okey_setup = conn.execute(select(setups.c.occurrence_key).where(setups.c.id == setup_id)).scalar()
    key = c.outcome_key(occurrence=okey_setup, kind=kind, window=window, evaluator_version=evaluator_version,
                        evidence_hash=evidence_hash)
    existing = conn.execute(select(outcomes.c.id).where(outcomes.c.outcome_key == key)).scalar()
    if existing is not None:
        return {"outcome_id": existing, "created": False}
    chain = conn.execute(select(outcomes.c.id, outcomes.c.supersedes_id).where(
        outcomes.c.setup_id == setup_id, outcomes.c.kind == kind, outcomes.c.window == window)).all()
    superseded = {r.supersedes_id for r in chain}
    tips = [r.id for r in chain if r.id not in superseded]
    if len(tips) > 1:
        raise c.ContractViolation("outcome chain has more than one tip")
    oid = conn.execute(outcomes.insert().values(
        outcome_key=key, setup_id=setup_id, run_key=setup.run_key, kind=kind, window=window,
        evaluator_version=evaluator_version, evidence_hash=evidence_hash, supersedes_id=tips[0] if tips else None,
        status=status, outcome_values=values, recorded_at=_naive(now)).returning(outcomes.c.id)).scalar()
    return {"outcome_id": oid, "created": True}


# ── Seals and retention ───────────────────────────────────────────────────────────────────

def seal_session(conn, *, run_key: str, identity: str, session_date: date, now) -> bool:
    """Close membership. Takes the session lock first, so it waits for any insert in flight
    and every insert after it sees the seal (review IC-01)."""
    _session_lock(conn, run_key, identity, session_date)
    r = conn.execute(_insert(conn, seals).values(run_key=run_key, identity_hash=identity, session_date=session_date,
                                                 sealed_at=_naive(now)).on_conflict_do_nothing())
    return bool(r.rowcount)


def _retention_on(conn):
    if conn.dialect.name == "postgresql":
        conn.execute(text("SET LOCAL stockai.intraday_retention = 'on'"))


def archive_session(conn, *, run_key: str, identity: str, session_date: date, now) -> dict:
    """Archive per-result counts for a SEALED session, verify them, then delete its non-setup
    evaluations. One transaction (the caller's): a failure commits nothing, a retry after success
    finds identical counts and nothing to delete."""
    _session_lock(conn, run_key, identity, session_date)       # serialise archives and late inserts
    sealed = conn.execute(select(seals.c.sealed_at).where(
        seals.c.run_key == run_key, seals.c.identity_hash == identity, seals.c.session_date == session_date)).first()
    if sealed is None:
        raise c.ContractViolation("only a sealed session can be archived")
    key = and_(evaluations.c.run_key == run_key, evaluations.c.identity_hash == identity,
               evaluations.c.session_date == session_date)
    live = dict(conn.execute(select(evaluations.c.result, func.count()).where(key).group_by(evaluations.c.result)).all())
    stored = dict(conn.execute(select(evaluation_counts.c.result, evaluation_counts.c.count).where(
        evaluation_counts.c.run_key == run_key, evaluation_counts.c.identity_hash == identity,
        evaluation_counts.c.session_date == session_date)).all())
    if not stored:
        for result, n in live.items():
            conn.execute(_insert(conn, evaluation_counts).values(
                run_key=run_key, identity_hash=identity, session_date=session_date, result=result, count=n,
                archived_at=_naive(now)).on_conflict_do_nothing())
        stored = dict(live)
    # After a successful archive, non-setup rows are gone: live 'setup' counts must still match,
    # and every non-setup live count must be zero or equal to the archive.
    for result, n in live.items():
        if stored.get(result) != n:
            raise c.ContractViolation(f"archive mismatch for {result}: stored {stored.get(result)} live {n}")
    _retention_on(conn)
    deleted = conn.execute(evaluations.delete().where(key, evaluations.c.result != "setup")).rowcount
    return {"counts": stored, "deleted": deleted}


def delete_setup(conn, *, setup_id: int) -> None:
    """400-day deletion of one setup and everything that references it. Refuses an unresolved,
    unreconciled exposure."""
    latest = conn.execute(select(transitions.c.to_state).where(transitions.c.setup_id == setup_id)
                          .order_by(transitions.c.seq.desc()).limit(1)).scalar()
    if latest not in c.FINAL:
        raise c.ContractViolation(f"setup {setup_id} is not final ({latest}); never deleted")
    ev = conn.execute(select(setups.c.evaluation_id).where(setups.c.id == setup_id)).scalar()
    _retention_on(conn)
    conn.execute(outcomes.delete().where(outcomes.c.setup_id == setup_id))      # whole chains, one statement
    conn.execute(transitions.delete().where(transitions.c.setup_id == setup_id))
    conn.execute(inputs.delete().where(inputs.c.setup_id == setup_id))
    conn.execute(setups.delete().where(setups.c.id == setup_id))
    conn.execute(evaluations.delete().where(evaluations.c.id == ev))


def purge_inputs(conn, *, setup_id: int) -> int:
    """35-day input retention for a FINAL setup (never an unresolved exposure)."""
    latest = conn.execute(select(transitions.c.to_state).where(transitions.c.setup_id == setup_id)
                          .order_by(transitions.c.seq.desc()).limit(1)).scalar()
    if latest not in c.FINAL:
        raise c.ContractViolation(f"setup {setup_id} is not final ({latest}); inputs kept")
    _retention_on(conn)
    return conn.execute(inputs.delete().where(inputs.c.setup_id == setup_id)).rowcount
