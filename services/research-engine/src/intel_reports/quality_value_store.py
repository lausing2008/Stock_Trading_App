"""Persisting Quality & Value evaluations, and capturing expectations before the fact.

APPEND-ONLY, EVERYWHERE IN THIS MODULE. There is no update path to any of the three tables,
and that is the property that makes a shadow trial measurable rather than decorative: a verdict
recorded on a date must still say, three months later, what it concluded and under which rules.
An UPDATE anywhere here would quietly rewrite that history the first time a threshold moved.

Every write uses INSERT ... ON CONFLICT DO NOTHING, so a re-run is idempotent and a concurrent
run cannot produce a duplicate or a lost write — the database decides, not a read-then-write in
application code, which is the race this codebase has already paid for twice.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .quality_value import POLICY_VERSION, policy_fingerprint, naive_utc


def freeze_inputs(values: dict, *, refs: dict | None = None) -> dict:
    """Package the resolved input VALUES with a digest, not just pointers to them.

    WHY VALUES AND NOT ONLY REFERENCES. A fingerprint of the rules plus a cutoff cannot
    reproduce an evaluation: the rows it read can change afterwards. `financial_statements` is
    refreshed in place, a provider revises a figure, a market capitalisation is refetched — and
    then the stored verdict is a conclusion whose premises no longer exist. References alone
    only locate what WAS read if the thing they point at is itself immutable, and most of these
    are not.

    So the resolved values travel with the verdict, and `input_digest` makes a later change to
    them detectable rather than invisible. The references are kept too: they answer "what has
    this become since", which the frozen copy deliberately cannot.
    """
    canonical = json.dumps(values, sort_keys=True, separators=(",", ":"), default=str)
    return {
        "frozen_inputs": values,
        "input_digest": hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32],
        "refs": refs or {},
        "note": "frozen_inputs are the VALUES the gates were computed from, copied at the "
                "cutoff. refs locate the source rows, which may have changed since — comparing "
                "the two is how a changed premise is detected rather than silently adopted.",
    }


def verify_inputs(stored: dict) -> dict:
    """Recompute the digest of a stored evaluation's frozen inputs.

    A stored row that cannot reproduce its own digest has been altered after the fact, which is
    a different and more serious problem than its premises having moved on.
    """
    if not isinstance(stored, dict) or "frozen_inputs" not in stored:
        return {"verifiable": False,
                "reason": "this row stores references only, with no frozen input values, so the "
                          "evaluation cannot be reproduced from it"}
    canonical = json.dumps(stored["frozen_inputs"], sort_keys=True,
                           separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    return {"verifiable": True, "intact": digest == stored.get("input_digest"),
            "expected": stored.get("input_digest"), "recomputed": digest}


def record_evaluation(session, evaluation, *, cutoff: datetime,
                      evidence_refs: dict | None = None) -> tuple:
    """Store one evaluation immutably. Returns (row, created).

    IDEMPOTENT UNDER IDENTICAL RULES, NEW ROW UNDER CHANGED ONES. The conflict target is
    (symbol, cutoff, policy_fingerprint): re-running today's evaluation writes nothing, while
    changing any threshold or claim sentence produces a different fingerprint and therefore a
    new row BESIDE the old one. Nothing that was concluded is ever overwritten.
    """
    from db import QualityValueEvaluation

    cutoff = naive_utc(cutoff)
    fp = policy_fingerprint()
    payload = evaluation.as_dict()
    stmt = (pg_insert(QualityValueEvaluation)
            .values(symbol=evaluation.symbol, cutoff=cutoff,
                    policy_version=POLICY_VERSION, policy_fingerprint=fp,
                    state=payload["state"], gates=payload["gates"],
                    evidence_refs=evidence_refs or {},
                    blocking=payload.get("blocking") or [],
                    explanation=payload.get("explanation") or [])
            .on_conflict_do_nothing(
                index_elements=["symbol", "cutoff", "policy_fingerprint"])
            .returning(QualityValueEvaluation.id))
    new_id = session.execute(stmt).scalar()
    session.commit()
    row = session.execute(
        select(QualityValueEvaluation).where(
            QualityValueEvaluation.symbol == evaluation.symbol,
            QualityValueEvaluation.cutoff == cutoff,
            QualityValueEvaluation.policy_fingerprint == fp)).scalars().first()
    return row, new_id is not None


def latest_evaluations(session, *, symbols=None, limit: int = 500) -> list:
    """The newest stored evaluation per symbol UNDER THE CURRENT POLICY.

    Scoped to the current fingerprint deliberately. Mixing verdicts produced under different
    rules into one list would present them as comparable, which is the thing the fingerprint
    exists to prevent.
    """
    from db import QualityValueEvaluation

    q = (select(QualityValueEvaluation)
         .where(QualityValueEvaluation.policy_fingerprint == policy_fingerprint())
         .order_by(QualityValueEvaluation.symbol,
                   QualityValueEvaluation.cutoff.desc()))
    if symbols:
        q = q.where(QualityValueEvaluation.symbol.in_(list(symbols)))
    seen, out = set(), []
    for row in session.execute(q).scalars():
        if row.symbol in seen:
            continue
        seen.add(row.symbol)
        out.append(row)
        if len(out) >= limit:
            break
    return out


def evaluation_history(session, symbol: str, limit: int = 50) -> list:
    """Every stored verdict for one symbol, ACROSS policies, newest first.

    Unscoped by fingerprint on purpose: this is the view that answers "did the screen's answer
    change because the company changed, or because we changed the rules", and that question
    needs both kinds of row side by side with their policy stamped on each.
    """
    from db import QualityValueEvaluation
    return list(session.execute(
        select(QualityValueEvaluation)
        .where(QualityValueEvaluation.symbol == symbol)
        .order_by(QualityValueEvaluation.cutoff.desc(),
                  QualityValueEvaluation.id.desc())
        .limit(limit)).scalars().all())


class BackdatedCapture(Exception):
    """An attempt to record an expectation that could not have been held at the time."""


def capture_estimate(session, *, symbol: str, target_period: str, metric: str, provider: str,
                     value, units=None, accounting_basis=None, analyst_count=None,
                     source_as_of=None, target_period_end=None, captured_at=None,
                     raw=None) -> tuple:
    """Record one estimate observation. A revision is a NEW ROW, never an update.

    `captured_at` defaults to now and is never accepted from a caller trying to claim an
    observation older than the row it is replacing — see `_reject_backdating`.
    """
    from db import EstimateSnapshot

    captured_at = naive_utc(captured_at or datetime.now(timezone.utc))
    stmt = (pg_insert(EstimateSnapshot)
            .values(symbol=symbol, target_period=target_period,
                    target_period_end=target_period_end, metric=metric, units=units,
                    accounting_basis=accounting_basis, provider=provider, value=value,
                    analyst_count=analyst_count,
                    source_as_of=naive_utc(source_as_of) if source_as_of else None,
                    captured_at=captured_at, raw=raw)
            .on_conflict_do_nothing(
                index_elements=["symbol", "target_period", "metric", "provider", "captured_at"])
            .returning(EstimateSnapshot.id))
    new_id = session.execute(stmt).scalar()
    session.commit()
    return new_id, new_id is not None


def capture_macro_expectation(session, *, release_key: str, reference_period: str,
                              expectation, expectation_source: str, scheduled_at=None,
                              units=None, release_name=None, expectation_captured_at=None,
                              published_at=None, raw=None) -> tuple:
    """Record what was expected for a scheduled release, BEFORE it prints.

    REFUSES A CAPTURE AT OR AFTER PUBLICATION. A consensus read once the number is out is
    contaminated by the number, and storing it as an expectation would make every surprise
    computed from it look smaller than it was — a bias in the one direction that flatters the
    platform. The refusal is an exception rather than a silent skip, because a capture job that
    is running too late is a scheduling defect that must surface.
    """
    from db import MacroExpectation

    captured = naive_utc(expectation_captured_at or datetime.now(timezone.utc))
    pub = naive_utc(published_at) if published_at else None
    if pub is not None and captured >= pub:
        raise BackdatedCapture(
            f"{release_key}/{reference_period}: the expectation was captured at {captured} but "
            f"the release published at {pub}. A consensus observed at or after publication is "
            f"contaminated by the result and is not evidence of what was expected.")

    stmt = (pg_insert(MacroExpectation)
            .values(release_key=release_key, release_name=release_name,
                    reference_period=reference_period, scheduled_at=naive_utc(scheduled_at)
                    if scheduled_at else None, published_at=pub, units=units,
                    expectation=expectation, expectation_source=expectation_source,
                    expectation_captured_at=captured, raw=raw)
            .on_conflict_do_nothing(
                index_elements=["release_key", "reference_period"])
            .returning(MacroExpectation.id))
    new_id = session.execute(stmt).scalar()
    session.commit()
    return new_id, new_id is not None


def record_first_actual(session, *, release_key: str, reference_period: str, actual,
                        captured_at=None, published_at=None) -> str:
    """Write the FIRST published figure once, and every later change as a revision.

    THE FIRST PRINT IS NEVER OVERWRITTEN. A statistical agency revises; a market reacted to the
    number published on the day. Collapsing both into one "actual" makes every historical
    surprise wrong in the direction of looking more predictable than it was. So the first write
    sets `first_actual` and any subsequent different value appends to `revisions`.

    Returns "first", "revision" or "unchanged" so a caller can log which happened.
    """
    from db import MacroExpectation

    captured = naive_utc(captured_at or datetime.now(timezone.utc))
    row = session.execute(
        select(MacroExpectation).where(
            MacroExpectation.release_key == release_key,
            MacroExpectation.reference_period == reference_period)
        .with_for_update()).scalars().first()
    if row is None:
        raise BackdatedCapture(
            f"{release_key}/{reference_period}: no expectation row exists, so this actual has "
            f"nothing to be a surprise against. It is NOT created here — an expectation that "
            f"was never captured cannot be reconstructed from the result.")

    if row.first_actual is None:
        row.first_actual = actual
        row.first_actual_captured_at = captured
        if published_at is not None and row.published_at is None:
            row.published_at = naive_utc(published_at)
        outcome = "first"
    elif row.first_actual == actual:
        outcome = "unchanged"
    else:
        revisions = list(row.revisions or [])
        revisions.append({"value": actual, "captured_at": captured.isoformat(),
                          "supersedes": row.first_actual if not revisions
                          else revisions[-1]["value"]})
        row.revisions = revisions
        outcome = "revision"
    session.commit()
    return outcome
