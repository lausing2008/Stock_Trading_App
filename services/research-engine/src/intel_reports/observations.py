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


#: Resolution-policy versions this resolver can execute. A stored observation carrying an
#: unknown version is NOT scored under today's code — the policy frozen with it is the one it
#: must be scored by, and silently applying a different one is the bias the freezing prevents.
SUPPORTED_POLICY_VERSIONS = ("res-1",)


def resolve(session, observation, *, session_closes: dict, expected_sessions: list,
            benchmark_reference: float | None = None,
            benchmark_closes: dict | None = None,
            delisting: dict | None = None,
            adjustment_consistent: bool | None = None,
            now: datetime | None = None) -> tuple:
    """Score one observation under ITS OWN frozen policy. Returns (outcome_row, created).

    `expected_sessions` are the trading-session dates CONSTRUCTED for this horizon, and
    `session_closes` maps date -> close (or None where the row is missing). A date absent from
    the map is a missing bar, not a reason to reach further forward.

    `delisting` is `{"cause": ..., "proceeds_per_share": ..., "evidence": ...}`. A cause alone
    never resolves: an acquisition without documented consideration has no computable return,
    and marking it RESOLVED would invent one.

    `adjustment_consistent` is TRI-STATE. `None` means nobody checked, which is not the same as
    checked-and-fine; it resolves to UNRESOLVED_ADJUSTMENT_UNVERIFIED rather than defaulting to
    True as an earlier version did.
    """
    from db import ObservationOutcome

    now = now or datetime.now(timezone.utc).replace(tzinfo=None)
    need = observation.horizon_sessions
    policy = (observation.resolution_policy or {}).get("version")
    state = descriptive = executable = bench_ret = excess = None
    bench_entry_ret = None
    basis = ""

    def _finish(st, why):
        return st, why

    if policy not in SUPPORTED_POLICY_VERSIONS:
        state, basis = "UNRESOLVED_POLICY_UNSUPPORTED", (
            f"the observation was frozen under resolution policy {policy!r}, which this "
            f"resolver does not implement. Scoring it under today's rules would discard the "
            f"freezing entirely")
    elif adjustment_consistent is None:
        state, basis = "UNRESOLVED_ADJUSTMENT_UNVERIFIED", (
            "no split/dividend adjustment check was performed. Unchecked is not the same as "
            "consistent, and defaulting it to consistent would assert something nobody verified")
    elif adjustment_consistent is False:
        state, basis = "UNRESOLVED_ADJUSTMENT_MISSING", (
            "the adjustment basis could not be applied consistently across both endpoints; a "
            "split is an adjustment basis, not a reason to void")
    elif delisting:
        cause = delisting.get("cause")
        proceeds = delisting.get("proceeds_per_share")
        if cause == "exchange_transfer":
            state, basis = "UNRESOLVED_IDENTIFIER_FOLLOW_REQUIRED", (
                "an exchange transfer is not a delisting; the security continues under a new "
                "venue or symbol and must be followed rather than scored here")
        elif cause in ("acquisition", "bankruptcy") and proceeds is not None \
                and observation.reference_price:
            state = "RESOLVED_ACQUISITION" if cause == "acquisition" else "RESOLVED_BANKRUPTCY"
            descriptive = (proceeds - observation.reference_price) / observation.reference_price
            basis = (f"{cause} resolved at documented consideration of {proceeds} per share "
                     f"({delisting.get('evidence') or 'evidence reference not supplied'})")
        else:
            state, basis = "UNRESOLVED_DELISTED_NO_TERMINAL_PRICE", (
                f"delisted ({cause or 'cause unrecorded'}) with no documented terminal value. "
                f"A cause alone does not produce a return. The row stays in the coverage "
                f"denominator and any statistic over this set must disclose the exclusion")
    elif len(expected_sessions) < need:
        state, basis = "UNRESOLVED_INSUFFICIENT_SESSIONS", (
            f"{len(expected_sessions)} of {need} trading sessions have completed. Not a partial "
            f"return, and excluded from every aggregate rather than counted as flat")
    elif observation.reference_price in (None, 0):
        state, basis = "UNRESOLVED_PRICE_MISSING", "no reference price on the observation"
    else:
        wanted = expected_sessions[:need]
        missing = [d for d in wanted if session_closes.get(d) is None]
        if missing:
            state, basis = "UNRESOLVED_PRICE_MISSING", (
                f"{len(missing)} of {need} expected session close(s) are absent "
                f"({', '.join(missing[:3])}). The endpoint is NEVER moved forward to the next "
                f"stored row — that would measure a different horizon")
        else:
            state = "RESOLVED"
            end = session_closes[wanted[-1]]
            entry = session_closes[wanted[0]]
            descriptive = (end - observation.reference_price) / observation.reference_price
            executable = (end - entry) / entry
            basis = (f"descriptive from the reference close; simulated entry at the close of "
                     f"{wanted[0]}, the first completed session after the observation")
            if benchmark_closes:
                b_end = benchmark_closes.get(wanted[-1])
                b_entry = benchmark_closes.get(wanted[0])
                # SAME WINDOW ON BOTH SIDES. An earlier version measured the stock from its
                # reference close and the benchmark from the first subsequent close, then
                # subtracted them — two different windows, so the difference was not an excess.
                if b_end and benchmark_reference:
                    bench_ret = (b_end - benchmark_reference) / benchmark_reference
                    excess = descriptive - bench_ret
                if b_end and b_entry:
                    bench_entry_ret = (b_end - b_entry) / b_entry

    resolved = state.startswith("RESOLVED")
    attempt = {"at": now.isoformat(), "state": state, "basis": basis}

    existing = session.execute(select(ObservationOutcome).where(
        ObservationOutcome.observation_id == observation.id,
        ObservationOutcome.horizon_sessions == need)).scalars().first()

    if existing is None:
        row = ObservationOutcome(
            observation_id=observation.id, horizon_sessions=need,
            descriptive_return=descriptive, simulated_executable_return=executable,
            benchmark_return=bench_ret, excess_return=excess,
            return_basis=observation.return_basis,
            delisting_cause=(delisting or {}).get("cause"), resolution_state=state,
            sessions_elapsed=len(expected_sessions), resolution_basis=basis,
            resolved_at=now if resolved else None,
            attempts=[attempt], benchmark_entry_return=bench_entry_ret)
        session.add(row)
        session.commit()
        return row, True

    # A GUARDED TRANSITION. An earlier version used ON CONFLICT DO NOTHING, so the first
    # pending insert became permanent and a horizon could never resolve once its sessions
    # elapsed. A pending row may advance; a RESOLVED one is immutable, and every attempt is
    # appended so the supersession is visible rather than silent.
    if existing.resolution_state.startswith("RESOLVED"):
        existing.attempts = list(existing.attempts or []) + [
            {**attempt, "ignored": "already resolved; a resolved outcome is never rewritten"}]
        session.commit()
        return existing, False

    previous = existing.resolution_state
    existing.attempts = list(existing.attempts or []) + [attempt]
    if resolved or state != previous:
        existing.descriptive_return = descriptive
        existing.simulated_executable_return = executable
        existing.benchmark_return = bench_ret
        existing.excess_return = excess
        existing.benchmark_entry_return = bench_entry_ret
        existing.resolution_state = state
        existing.sessions_elapsed = len(expected_sessions)
        existing.resolution_basis = basis
        existing.superseded_state = previous
        existing.resolved_at = now if resolved else None
    session.commit()
    return existing, False
