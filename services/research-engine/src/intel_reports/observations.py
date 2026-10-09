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

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from . import evidence_buckets as EB
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

#: What each unresolved state MEANS, in a reader's words. A dashboard showing a bare
#: UNRESOLVED_ADJUSTMENT_UNVERIFIED tells a reader nothing about what is missing or who can
#: close it, and a frontend keeping its own copy of this map is how four states once all
#: rendered as the single thing they were added to stop saying.
UNRESOLVED_LABEL = {
    "RESOLVED": "Resolved",
    "RESOLVED_ACQUISITION": "Resolved at acquisition consideration",
    "RESOLVED_BANKRUPTCY": "Resolved at terminal value",
    "UNRESOLVED_INSUFFICIENT_SESSIONS": "Pending — the horizon has not elapsed",
    "UNRESOLVED_PRICE_MISSING": "Price missing for an expected session",
    "UNRESOLVED_ADJUSTMENT_UNVERIFIED": "Corporate-action adjustment evidence unavailable",
    "UNRESOLVED_ADJUSTMENT_MISSING": "Corporate action requires adjusted prices",
    "UNRESOLVED_DELISTED_NO_TERMINAL_PRICE": "Delisted with no documented terminal value",
    "UNRESOLVED_IDENTIFIER_FOLLOW_REQUIRED": "Exchange transfer — follow the identifier",
    "UNRESOLVED_POLICY_UNSUPPORTED": "Frozen under a policy this resolver cannot execute",
    "NOT_RESOLVED": "Not yet scored",
    "INVALID_CAPTURE": "Invalid capture — excluded from publication",
}

#: Sentinel for outcome rows written before the resolver was fingerprinted. The code that
#: produced them is no longer present, so its fingerprint cannot be recomputed — and is not
#: guessed. Such a row is retained, never rewritten, and superseded by the current resolver.
UNFINGERPRINTED = "unrecorded-pre-fingerprint"


def _canonical_source(fn) -> str:
    """A function's BEHAVIOUR as text: AST-normalised, comments and docstrings removed.

    Prose must not mint a new resolver — a reworded comment would re-score the whole corpus
    and fill the supersession log with changes that altered nothing. `ast.unparse` drops `#`
    comments on its own; a docstring is a real expression node and has to be removed
    deliberately, which a test caught.
    """
    import ast
    import inspect
    import textwrap
    tree = ast.parse(textwrap.dedent(inspect.getsource(fn)))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef, ast.Module)) \
                and ast.get_docstring(node) is not None:
            node.body = node.body[1:]
    return ast.unparse(tree)


def outcome_contract() -> dict:
    """EVERY input that can change a resolved figure, named explicitly.

    The first version of the fingerprint hashed `resolve` and two constants. That is not the
    calculation: an outcome also depends on WHICH SESSIONS are selected, WHICH PRICES are read
    for them, and HOW the adjustment basis is verified. A correction to any of those could
    therefore land with the fingerprint unchanged — and because a resolved row is immutable
    within its resolver, the corrected figures would silently never be written. That is the
    exact failure this versioning exists to prevent, so the omission defeated it.

    Session boundaries and the holiday calendars are in here too: moving a market's hours, or
    adding a holiday, changes which dates are sessions and therefore changes past windows, not
    only future ones.

    ADDING A DEPENDENCY TO THE RESOLUTION PATH MEANS ADDING IT HERE. `test_the_contract_covers
    _every_outcome_changing_dependency` fails when a known one is missing, so the omission
    surfaces as a red test rather than as frozen stale rows.
    """
    from common import market_calendar as cal
    return {
        "policy": RESOLUTION_POLICY,
        "execution": EXECUTION_ASSUMPTIONS,
        "resolve": _canonical_source(resolve),
        # Session selection — which dates the return is measured across.
        "sessions_forward": _canonical_source(EB.sessions_forward),
        "sessions_back": _canonical_source(EB.sessions_back),
        "session_bounds": _canonical_source(cal.session_bounds),
        "session_hours": {"US": cal._US_SESSION, "HK": cal._HK_SESSION},
        "holidays": {"US": sorted(d.isoformat() for d in cal.NYSE_HOLIDAYS),
                     "HK": sorted(d.isoformat() for d in cal.HK_HOLIDAYS)},
        # Price selection — which number is read for each of those dates.
        "closes_by_date": _canonical_source(EB.closes_by_date),
        # Adjustment verification — whether both endpoints share one basis.
        "adjustment_evidence": _canonical_source(EB.adjustment_evidence),
        "adjustment_tolerance": EB.ADJUSTMENT_TOLERANCE,
        "split_factors": _canonical_source(EB.split_factors),
        "apply_adjustment": _canonical_source(EB.apply_adjustment),
        "adjustment_method": EB.ADJUSTMENT_METHOD,
        "evidence_status": _canonical_source(EB.evidence_status),
        "performance_eligibility": _canonical_source(EB.performance_eligibility),
        "return_bases": list(EB.RETURN_BASES),
        "share_count_actions": list(EB.SHARE_COUNT_ACTIONS),
        "distribution_actions": list(EB.DISTRIBUTION_ACTIONS),
    }


def resolver_fingerprint() -> str:
    """Digest of the whole calculation contract, derived from it rather than maintained by hand.

    Same principle as `policy_fingerprint`: a version string someone has to remember to bump is
    a version string that silently goes stale.
    """
    return digest(outcome_contract())


def publishable_outcomes(session, *, origin: str, eligibility: str = "verified",
                         symbol: str | None = None):
    """The ONLY selector a published performance figure may be computed from.

    THREE conditions, and dropping any one of them pools readings that must not be pooled:

      * `resolver_fingerprint == resolver_fingerprint()` — scored by the resolver running now.
        A row written by an earlier resolver is not merely old: the six corrected defects each
        changed a figure, so mixing them measures two different definitions of excess return.
        This also excludes `UNFINGERPRINTED` rows, whose resolver no longer exists and
        therefore cannot be shown to agree with anything.
      * `superseded_by_id IS NULL` — not already replaced. Mostly redundant beside the test
        above, since the unique constraint allows one row per resolver per horizon, so a
        superseded row normally carries a different fingerprint. It earns its place on a
        ROLLBACK: revert the resolver and its fingerprint returns to an earlier value, which
        again matches a row that was explicitly replaced. (A sabotage run found the first two
        conditions caught everything and this one caught nothing — the claim, not the filter,
        was what needed correcting.)
      * one `origin` — a retrospective replay and a prospective capture are never pooled. The
        replay's rules were written with its outcomes already in existence.
      * `performance_eligibility == eligibility` — one POOL. This is the third of the three
        separate questions an outcome answers: whether a figure could be calculated, whether its
        adjustment evidence is verified, and which aggregate it may enter. Mixing pools is the
        failure this parameter exists to make impossible to do by accident.
      * `invalidated_reason IS NULL` — the CAPTURE itself is sound. The three above are all
        properties of the scoring; this one is not, and no amount of re-resolution can repair a
        capture whose inputs were produced by defective code. Without it, re-resolving an
        invalid observation under the current resolver would walk it straight back into
        publication.

    UNRESOLVED ROWS ARE NOT IN ANY POOL — they are `ineligible`, which is itself a selectable
    value so the coverage denominator stays answerable. The frozen policy requires any statistic
    over this set to disclose how many rows carry no return and why; `coverage_counts()` below
    produces exactly that breakdown without ever handing an unresolved row to a mean.

    `eligibility` SELECTS THE POOL, and there is no "all". A provisional figure is not a worse
    verified figure: its adjustment basis rests on what a source happened to return rather than
    on a guarantee that the list was complete, so it describes a different population. Averaging
    the two produces a number describing neither. Pass "verified" for a headline result and
    "provisional" for the separately-labelled one; read both, never added together.

    The caller must still separate by `return_basis`, which varies per row and so cannot be
    fixed here.
    """
    from db import IntelligenceObservation, ObservationOutcome
    q = (select(ObservationOutcome, IntelligenceObservation)
         .join(IntelligenceObservation,
               IntelligenceObservation.id == ObservationOutcome.observation_id)
         .where(ObservationOutcome.resolver_fingerprint == resolver_fingerprint(),
                ObservationOutcome.superseded_by_id.is_(None),
                ObservationOutcome.performance_eligibility == eligibility,
                IntelligenceObservation.origin == origin,
                IntelligenceObservation.invalidated_reason.is_(None)))
    if symbol:
        q = q.where(IntelligenceObservation.symbol == symbol)
    return session.execute(q.order_by(ObservationOutcome.id)).all()


def coverage_counts(session, *, origin: str, symbol: str | None = None,
                    capture_policy: str | None = None) -> dict:
    """How many rows sit in each pool, which is what a statistic must disclose beside itself.

    A performance figure computed over the `verified` pool is incomplete without this: the
    reader needs to know how many observations it EXCLUDED and why, or a 100% win rate over two
    eligible rows reads like a result.
    """
    from db import IntelligenceObservation, ObservationOutcome
    # SPLIT BY CAPTURE GENERATION. A historical observation keeps its original rules AND its
    # eventual outcome — both are preserved — but it must not be added into the aggregate for
    # the rules in force, which describes a different population.
    q = (select(ObservationOutcome.performance_eligibility,
                ObservationOutcome.evidence_status,
                ObservationOutcome.resolution_state,
                IntelligenceObservation.policy_fingerprint,
                func.count())
         .join(IntelligenceObservation,
               IntelligenceObservation.id == ObservationOutcome.observation_id)
         .where(ObservationOutcome.resolver_fingerprint == resolver_fingerprint(),
                ObservationOutcome.superseded_by_id.is_(None),
                IntelligenceObservation.origin == origin)
         .group_by(ObservationOutcome.performance_eligibility,
                   ObservationOutcome.evidence_status,
                   ObservationOutcome.resolution_state,
                   IntelligenceObservation.policy_fingerprint))
    if symbol:
        q = q.where(IntelligenceObservation.symbol == symbol)
    pools: dict = {}
    historical: dict = {}
    for eligibility, ev, state, fp, n in session.execute(q).all():
        key = eligibility or "ineligible"
        target = pools if (capture_policy is None or fp == capture_policy) else historical
        target.setdefault(key, {"total": 0, "by_reason": {}})
        target[key]["total"] += n
        reason = f"{ev or 'unclassified'} / {state}"
        target[key]["by_reason"][reason] = target[key]["by_reason"].get(reason, 0) + n
    iq = select(func.count()).select_from(IntelligenceObservation).where(
        IntelligenceObservation.origin == origin,
        IntelligenceObservation.invalidated_reason.isnot(None))
    if symbol:
        iq = iq.where(IntelligenceObservation.symbol == symbol)
    return {"origin": origin, "pools": pools,
            # Retained and answerable, never added into the current aggregate.
            "historical_pools": historical,
            "capture_policy": capture_policy,
            "invalidated_captures": session.execute(iq).scalar(),
            "note": "A verified aggregate must disclose these counts beside it. A provisional "
                    "figure is a different population, not a lower-quality verified one."}


def resolve(session, observation, *, session_closes: dict, expected_sessions: list,
            benchmark_reference: float | None = None,
            benchmark_closes: dict | None = None,
            delisting: dict | None = None,
            adjustment_consistent: bool | None = None,
            adjustment_basis: str | None = None,
            adjustment_factors: dict | None = None,
            adjustment: dict | None = None,
            return_basis: str | None = None,
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
            adjustment_basis or
            "no split/dividend adjustment check was performed. Unchecked is not the same as "
            "consistent, and defaulting it to consistent would assert something nobody verified")
    elif adjustment_consistent is False:
        state, basis = "UNRESOLVED_ADJUSTMENT_MISSING", (
            (adjustment_basis + " — " if adjustment_basis else "") +
            "a split is an adjustment basis, not a reason to void: the observation resolves "
            "once adjusted closes are supplied for both endpoints")
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
            # ADJUSTMENT APPLIED HERE, FROM THE EVIDENCE. The reference price is restated too:
            # a split between the reference close and the first measured session corrupts the
            # descriptive return exactly as badly as one in the middle, and the reference
            # session is inside the window the evidence was built over.
            f_stock = (adjustment_factors or {}).get("stock")
            adj_closes = EB.apply_adjustment(session_closes, f_stock)
            ref_date = (observation.reference_price_as_of.date().isoformat()
                        if observation.reference_price_as_of else None)
            ref = observation.reference_price * (
                (f_stock or {}).get(ref_date, 1.0) if f_stock else 1.0)
            end = adj_closes[wanted[-1]]
            entry = adj_closes[wanted[0]]
            descriptive = (end - ref) / ref
            executable = (end - entry) / entry
            basis = (f"descriptive from the reference close; simulated entry at the close of "
                     f"{wanted[0]}, the first session that OPENED after the observation "
                     f"instant and has since closed"
                     + (f". {adjustment_basis}" if adjustment_basis else ""))
            if benchmark_closes:
                f_bench = (adjustment_factors or {}).get("benchmark")
                adj_bench = EB.apply_adjustment(benchmark_closes, f_bench)
                benchmark_reference = (benchmark_reference * (f_bench or {}).get(ref_date, 1.0)
                                       if (benchmark_reference and f_bench)
                                       else benchmark_reference)
                b_end = adj_bench.get(wanted[-1])
                b_entry = adj_bench.get(wanted[0])
                # SAME WINDOW ON BOTH SIDES. An earlier version measured the stock from its
                # reference close and the benchmark from the first subsequent close, then
                # subtracted them — two different windows, so the difference was not an excess.
                if b_end and benchmark_reference:
                    bench_ret = (b_end - benchmark_reference) / benchmark_reference
                    excess = descriptive - bench_ret
                if b_end and b_entry:
                    bench_entry_ret = (b_end - b_entry) / b_entry

    resolved = state.startswith("RESOLVED")
    # THREE SEPARATE QUESTIONS. `state` answered only whether a figure could be CALCULATED.
    ev_status = EB.evidence_status(adjustment)
    eligibility = EB.performance_eligibility(
        state, ev_status,
        capture_invalidated=getattr(observation, "invalidated_reason", None) is not None)
    attempt = {"at": now.isoformat(), "state": state, "basis": basis,
               "evidence_status": ev_status, "performance_eligibility": eligibility}

    fingerprint = resolver_fingerprint()
    attempt["resolver"] = fingerprint

    # IMMUTABILITY IS SCOPED TO ONE RESOLVER. Re-running THIS resolver must never rewrite a
    # resolved row. A CORRECTED resolver is a different question, and refusing it outright is
    # how the six-defect fix produced a rerun that changed nothing: every stored figure stayed
    # as the defective resolver had written it, with no sign on the row that it was stale.
    existing = session.execute(select(ObservationOutcome).where(
        ObservationOutcome.observation_id == observation.id,
        ObservationOutcome.horizon_sessions == need,
        ObservationOutcome.resolver_fingerprint == fingerprint)).scalars().first()

    if existing is None:
        row = ObservationOutcome(
            observation_id=observation.id, horizon_sessions=need,
            descriptive_return=descriptive, simulated_executable_return=executable,
            benchmark_return=bench_ret, excess_return=excess,
            return_basis=return_basis or observation.return_basis,
            delisting_cause=(delisting or {}).get("cause"), resolution_state=state,
            sessions_elapsed=len(expected_sessions), resolution_basis=basis,
            resolved_at=now if resolved else None, resolver_fingerprint=fingerprint,
            evidence_status=ev_status, performance_eligibility=eligibility,
            attempts=[attempt], benchmark_entry_return=bench_entry_ret)
        session.add(row)
        session.flush()  # needs row.id to point the superseded originals at it
        # EXPLICIT SUPERSESSION, ORIGINALS RETAINED. Rows from any earlier resolver keep every
        # figure they were written with; they gain a pointer to this one and an appended note.
        # Nothing is deleted or edited in place, so both readings stay inspectable side by side.
        for prior in session.execute(select(ObservationOutcome).where(
                ObservationOutcome.observation_id == observation.id,
                ObservationOutcome.horizon_sessions == need,
                ObservationOutcome.id != row.id)).scalars().all():
            prior.superseded_by_id = row.id
            prior.attempts = list(prior.attempts or []) + [{
                "at": now.isoformat(), "superseded_by": row.id,
                "note": f"retained unchanged; a corrected resolver ({fingerprint}) scored this "
                        f"observation afresh. The figures above are those of resolver "
                        f"{prior.resolver_fingerprint or UNFINGERPRINTED} and must not be "
                        f"pooled with current ones"}]
        session.commit()
        return row, True

    # A GUARDED TRANSITION. An earlier version used ON CONFLICT DO NOTHING, so the first
    # pending insert became permanent and a horizon could never resolve once its sessions
    # elapsed. A pending row may advance; a RESOLVED one is immutable, and every attempt is
    # appended so the supersession is visible rather than silent.
    if existing.resolution_state.startswith("RESOLVED"):
        existing.attempts = list(existing.attempts or []) + [
            {**attempt, "ignored": "already resolved by this same resolver; a resolved outcome "
                                   "is never rewritten under the rules that produced it"}]
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
        existing.evidence_status = ev_status
        existing.performance_eligibility = eligibility
        existing.sessions_elapsed = len(expected_sessions)
        existing.resolution_basis = basis
        existing.superseded_state = previous
        existing.resolved_at = now if resolved else None
    session.commit()
    return existing, False
