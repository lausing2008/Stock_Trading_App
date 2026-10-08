"""Storing directional observations and resolving their outcomes under a frozen policy.

APPEND-ONLY. No UPDATE path to an observation exists. A conclusion that is rendered but not
stored is unmeasurable forever after — the inputs move, and nothing later recovers what the
screen concluded on a date it was not written down.

THE RESOLUTION POLICY IS WRITTEN AT CAPTURE, BEFORE ANY RESULT EXISTS. Deciding how to handle a
gap, a missing price or a delisting after seeing outcomes is a bias vector, and the convenient
choice is always available in hindsight.
"""
from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from .evidence_buckets import digest, policy_fingerprint, POLICY_VERSION

REPLAY, PROSPECTIVE = "replay", "prospective"

#: Horizons, in TRADING SESSIONS. A calendar horizon silently shortens across a holiday week
#: and differs between the US and HK, both of which this platform serves.
HORIZONS = ((5, "1-5d"), (20, "1-4w"), (63, "1-3m"))

#: Fixed in advance and stored on every observation. See the architecture document for why each
#: branch reads the way it does — in particular why a delisting is never an automatic void.
RESOLUTION_POLICY = {
    "version": "res-1",
    "horizon_unit": "trading_sessions",
    "insufficient_sessions": "UNRESOLVED_INSUFFICIENT_SESSIONS — excluded from every "
                             "aggregate, never counted as flat",
    "price_missing": "UNRESOLVED_PRICE_MISSING — retried; never substituted from a "
                     "neighbouring session",
    "delisting": {
        "acquisition": "RESOLVED_ACQUISITION at documented consideration",
        "bankruptcy": "RESOLVED_BANKRUPTCY at the terminal traded price, or a documented zero",
        "exchange_transfer": "not a delisting — follow the identifier and resolve normally",
        "no_terminal_price": "UNRESOLVED_DELISTED_NO_TERMINAL_PRICE — the row stays in the "
                             "coverage denominator AND any statistic computed from the set "
                             "must disclose how many rows were excluded this way",
    },
    "corporate_actions": "splits and dividends are an adjustment basis, not a void. Both "
                         "endpoints must share one basis; missing adjustment data is "
                         "UNRESOLVED_ADJUSTMENT_MISSING",
    "benchmark_missing": "the absolute return resolves; excess_return stays NULL",
    "policy_change": "scored under the policy_fingerprint it was made with; a rule change "
                     "files a new observation and never re-scores an old one",
}

#: Everything the simulated executable return takes for granted. Listed because none of it is
#: modelled, and an unqualified "executable" would assert a fill nobody has shown is achievable.
EXECUTION_ASSUMPTIONS = {
    "modelled": [],
    "unmodelled": ["bid/ask spread", "slippage against displayed size",
                   "order timing within the session", "available liquidity at the price",
                   "trading halts and auction imbalances"],
    "note": "the next tradeable price is an EXECUTION ASSUMPTION, not proof of an achievable "
            "fill. Until the above are modelled the figure is simulated, and is named so.",
}


def record_observation(session, *, subject_key, symbol, origin, observed_at,
                       horizon_sessions, horizon_label, direction, support_quality,
                       reference_price, reference_price_as_of, reference_price_source,
                       reference_price_basis, benchmark_symbol, confirmation_rule,
                       invalidation_rule, bucket_ids, frozen_inputs, summary,
                       return_basis="price_return") -> tuple:
    """Store one observation immutably. Returns (row, created).

    IDEMPOTENT on (subject, observed_at, horizon, policy, origin): re-running the same
    observation writes nothing and hands back the existing row, so a repeated render does not
    accumulate duplicates. A changed policy files a NEW row beside the old one.
    """
    from db import IntelligenceObservation

    fp = policy_fingerprint()
    stmt = (pg_insert(IntelligenceObservation)
            .values(subject_key=subject_key, symbol=symbol, origin=origin,
                    observed_at=observed_at, horizon_sessions=horizon_sessions,
                    horizon_label=horizon_label, direction=direction,
                    support_quality=support_quality,
                    predictive_confidence=None,          # never set at capture
                    reference_price=reference_price,
                    reference_price_as_of=reference_price_as_of,
                    reference_price_source=reference_price_source,
                    reference_price_basis=reference_price_basis,
                    return_basis=return_basis, benchmark_symbol=benchmark_symbol,
                    confirmation_rule=confirmation_rule,
                    invalidation_rule=invalidation_rule,
                    resolution_policy=RESOLUTION_POLICY,
                    execution_assumptions=EXECUTION_ASSUMPTIONS,
                    policy_fingerprint=fp, bucket_ids=bucket_ids,
                    frozen_inputs=frozen_inputs,
                    frozen_inputs_digest=digest(frozen_inputs), summary=summary)
            .on_conflict_do_nothing(
                index_elements=["subject_key", "observed_at", "horizon_sessions",
                                "policy_fingerprint", "origin"])
            .returning(IntelligenceObservation.id))
    new_id = session.execute(stmt).scalar()
    session.commit()
    row = session.execute(select(IntelligenceObservation).where(
        IntelligenceObservation.subject_key == subject_key,
        IntelligenceObservation.observed_at == observed_at,
        IntelligenceObservation.horizon_sessions == horizon_sessions,
        IntelligenceObservation.policy_fingerprint == fp,
        IntelligenceObservation.origin == origin)).scalars().first()
    return row, new_id is not None


def record_buckets(session, *, subject_key, as_of, buckets) -> list:
    """Store the thirteen bucket rows immutably. Returns their ids, in bucket order."""
    from db import EvidenceBucket

    fp = policy_fingerprint()
    ids = []
    for b in buckets:
        d = b.as_dict()
        stmt = (pg_insert(EvidenceBucket)
                .values(subject_key=subject_key, bucket=b.name, as_of=as_of,
                        direction=d["direction"], strength=d["strength"],
                        support_quality=d["support_quality"],
                        predictive_confidence=None,
                        status=d["status"], evidence=d["evidence"],
                        contradictions=d["contradictions"], inputs=d["inputs"],
                        inputs_digest=d["inputs_digest"], policy_fingerprint=fp)
                .on_conflict_do_nothing(
                    index_elements=["subject_key", "bucket", "as_of", "policy_fingerprint"])
                .returning(EvidenceBucket.id))
        session.execute(stmt)
        session.commit()
        row = session.execute(select(EvidenceBucket.id).where(
            EvidenceBucket.subject_key == subject_key, EvidenceBucket.bucket == b.name,
            EvidenceBucket.as_of == as_of,
            EvidenceBucket.policy_fingerprint == fp)).scalar()
        ids.append(row)
    return ids


def resolve(session, observation, *, closes: list, benchmark_closes: list | None = None,
            delisting_cause: str | None = None, adjustment_consistent: bool = True,
            now: datetime | None = None) -> tuple:
    """Score one observation under ITS OWN frozen policy. Returns (outcome_row, created).

    `closes` are the completed sessions strictly AFTER `observed_at`, ascending. The caller
    supplies them so this stays testable; the policy below decides what they mean.
    """
    from db import ObservationOutcome

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    need = observation.horizon_sessions
    state, descriptive, executable, bench_ret, excess, basis = None, None, None, None, None, ""

    if not adjustment_consistent:
        state = "UNRESOLVED_ADJUSTMENT_MISSING"
        basis = ("a split or dividend adjustment could not be applied consistently across both "
                 "endpoints; splits are an adjustment basis, not a reason to void")
    elif delisting_cause == "acquisition":
        state, basis = "RESOLVED_ACQUISITION", "resolved at documented consideration"
    elif delisting_cause == "bankruptcy":
        state, basis = "RESOLVED_BANKRUPTCY", "resolved at the terminal traded price"
    elif delisting_cause == "no_terminal_price":
        state = "UNRESOLVED_DELISTED_NO_TERMINAL_PRICE"
        basis = ("delisted with no terminal price. The row stays in the coverage denominator "
                 "and any statistic over this set must disclose the exclusion")
    elif len(closes) < need:
        state = "UNRESOLVED_INSUFFICIENT_SESSIONS"
        basis = (f"{len(closes)} of {need} trading sessions elapsed. Not a partial return, and "
                 f"excluded from every aggregate rather than counted as flat")
    elif observation.reference_price in (None, 0):
        state, basis = "UNRESOLVED_PRICE_MISSING", "no reference price on the observation"
    elif closes[need - 1] is None:
        state = "UNRESOLVED_PRICE_MISSING"
        basis = "the close for the resolution session is missing; never substituted"
    else:
        state = "RESOLVED"
        end = closes[need - 1]
        descriptive = (end - observation.reference_price) / observation.reference_price
        # The next tradeable price, given the conclusion was formed after a completed close.
        entry = closes[0] if closes and closes[0] else None
        if entry:
            executable = (end - entry) / entry
        basis = (f"descriptive from the reference price; simulated executable from the next "
                 f"session's close ({'available' if entry else 'unavailable'})")
        if benchmark_closes and len(benchmark_closes) >= need and benchmark_closes[0]:
            b0, b1 = benchmark_closes[0], benchmark_closes[need - 1]
            if b0 and b1:
                bench_ret = (b1 - b0) / b0
                if descriptive is not None:
                    excess = descriptive - bench_ret

    stmt = (pg_insert(ObservationOutcome)
            .values(observation_id=observation.id, horizon_sessions=need,
                    descriptive_return=descriptive,
                    simulated_executable_return=executable,
                    benchmark_return=bench_ret, excess_return=excess,
                    return_basis=observation.return_basis,
                    delisting_cause=delisting_cause, resolution_state=state,
                    sessions_elapsed=len(closes), resolution_basis=basis,
                    resolved_at=now if state.startswith("RESOLVED") else None)
            .on_conflict_do_nothing(
                index_elements=["observation_id", "horizon_sessions"])
            .returning(ObservationOutcome.id))
    new_id = session.execute(stmt).scalar()
    session.commit()
    row = session.execute(select(ObservationOutcome).where(
        ObservationOutcome.observation_id == observation.id,
        ObservationOutcome.horizon_sessions == need)).scalars().first()
    return row, new_id is not None
