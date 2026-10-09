"""Stock summary, observation capture and outcome resolution.

Read-only except for two POSTs that WRITE an observation. No email, no alert type, no trading
behaviour — this endpoint cannot change any of them.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from common.jwt_auth import get_current_username
from common.logging import get_logger
from common.market_calendar import is_trading_day, session_bounds
from db import SessionLocal, Stock, Price, TimeFrame

from ..intel_reports import evidence_buckets as EB
from ..intel_reports.corporate_actions import COMPLETENESS_BASIS as CA_COMPLETENESS
from ..intel_reports.direction_screen import assess
from ..intel_reports.observations import (HORIZONS, PROSPECTIVE, REPLAY, UNRESOLVED_LABEL,
                                          coverage_counts, record_buckets, record_observation,
                                          resolve, resolver_fingerprint)
from ..intel_reports.quality_value_store import latest_assessments
from ..intel_reports.quality_value import (COMPETITIVE_DURABILITY, VALUATION as QV_VALUATION,
                                           VALUE_TRAP_RISK, naive_utc)

log = get_logger("research-engine.observations")
router = APIRouter(prefix="/intelligence", tags=["intelligence"])

BENCHMARK = {"US": "SPY", "HK": "2800.HK"}


def _sessions_back(venue: str, anchor: datetime, n: int) -> list:
    """Thin adapter over the ORM-free implementation, which the tests import directly."""
    return EB.sessions_back(venue, anchor, n, is_trading_day=is_trading_day,
                            session_bounds=session_bounds)


def _bars(session, stock_id: int, dates: list) -> list:
    rows = session.execute(
        select(Price.ts, Price.high, Price.low, Price.close, Price.volume, Price.adj_close)
        .where(Price.stock_id == stock_id, Price.timeframe == TimeFrame.D1,
               Price.ts >= datetime.fromisoformat(dates[0]),
               Price.ts < datetime.fromisoformat(dates[-1]) + timedelta(days=1))
        .order_by(Price.ts)).mappings().all()
    return [{"date": r["ts"].date().isoformat(),
             **{k: r[k] for k in ("high", "low", "close", "volume", "adj_close")}}
            for r in rows]


def _closes_for(session, stock_id: int, dates: list) -> dict:
    """Read the stored closes for exactly these session dates. Selection logic is in
    `EB.closes_by_date`, which is pure, tested, and inside the fingerprinted contract —
    price selection changes outcome figures, so it may not sit outside it."""
    if not dates:
        return {}
    rows = session.execute(
        select(Price.ts, Price.close).where(
            Price.stock_id == stock_id, Price.timeframe == TimeFrame.D1,
            Price.ts >= datetime.fromisoformat(dates[0]),
            Price.ts < datetime.fromisoformat(dates[-1]) + timedelta(days=1))
    ).all()
    return EB.closes_by_date(
        [{"date": r[0].date().isoformat(), "close": r[1]} for r in rows], dates)


def _adjustment_bars(session, stock_id: int, dates: list) -> list:
    """Close AND adj_close for every date, so a corporate action inside the window is visible."""
    if not dates:
        return []
    rows = session.execute(
        select(Price.ts, Price.close, Price.adj_close).where(
            Price.stock_id == stock_id, Price.timeframe == TimeFrame.D1,
            Price.ts >= datetime.fromisoformat(dates[0]),
            Price.ts < datetime.fromisoformat(dates[-1]) + timedelta(days=1))
    ).all()
    got = {r[0].date().isoformat(): r for r in rows}
    out = []
    for d in dates:
        r = got.get(d)
        out.append({"date": d,
                    "close": float(r[1]) if r is not None and r[1] is not None else None,
                    "adj_close": float(r[2]) if r is not None and r[2] is not None else None})
    return out


def _actions_for(session, symbol: str, window: list) -> list:
    """Sourced corporate actions for this symbol inside the window, as plain dicts."""
    from db import CorporateAction
    if not window:
        return []
    rows = session.execute(select(CorporateAction).where(
        CorporateAction.symbol == symbol,
        CorporateAction.ex_date >= date.fromisoformat(window[0]),
        CorporateAction.ex_date <= date.fromisoformat(window[-1]))
        .order_by(CorporateAction.ex_date)).scalars().all()
    return [{"action_type": r.action_type, "ex_date": r.ex_date.isoformat(),
             "split_ratio": r.split_ratio, "cash_amount": r.cash_amount,
             "currency": r.currency, "source": r.source, "source_ref": r.source_ref,
             "retrieved_at": r.retrieved_at.isoformat() if r.retrieved_at else None}
            for r in rows]


def _coverage_for(session, symbol: str) -> dict | None:
    """The action-history coverage CLAIM, which is what lets absence be read as absence."""
    from db import CorporateActionCoverage
    r = session.execute(select(CorporateActionCoverage).where(
        CorporateActionCoverage.symbol == symbol,
        CorporateActionCoverage.method == EB.ADJUSTMENT_METHOD)
        .order_by(CorporateActionCoverage.retrieved_at.desc())).scalars().first()
    if not r:
        return None
    # THE KEYS `adjustment_evidence` ACTUALLY READS. A rename on the model left this returning
    # `covers_from`/`covers_to` and every replay raised AttributeError — no test touched it,
    # the same gap that let an unimported name ship. `test_coverage_dict_matches_what_the_
    # evidence_reader_consumes` now pins the two together.
    return {"source": r.source, "method": r.method,
            "requested_from": r.requested_from.isoformat(),
            "requested_to": r.requested_to.isoformat(),
            "evidenced_from": r.evidenced_from.isoformat(),
            "evidenced_to": r.evidenced_to.isoformat(),
            "completeness_basis": r.completeness_basis,
            "completeness_note": CA_COMPLETENESS.get(r.completeness_basis),
            "retrieved_at": r.retrieved_at.isoformat() if r.retrieved_at else None}


def _existing_observation(session, subject_key, as_of, horizon_sessions, origin):
    from db import IntelligenceObservation
    from ..intel_reports.evidence_buckets import policy_fingerprint
    return session.execute(select(IntelligenceObservation).where(
        IntelligenceObservation.subject_key == subject_key,
        IntelligenceObservation.observed_at == as_of,
        IntelligenceObservation.horizon_sessions == horizon_sessions,
        IntelligenceObservation.policy_fingerprint == policy_fingerprint(),
        IntelligenceObservation.origin == origin)).scalars().first()


def build_buckets(session, stock, *, as_of: datetime) -> tuple:
    """The thirteen buckets for one issuer, from what the platform actually holds."""
    venue = getattr(stock.market, "value", stock.market)
    dates = _sessions_back(venue, as_of, 21)
    bars = _bars(session, stock.id, dates)
    setup = assess(bars, dates) if len(bars) == 21 else None

    from db import FinancialStatement
    stmts = list(session.execute(
        select(FinancialStatement)
        .where(FinancialStatement.symbol == stock.symbol,
               FinancialStatement.period_type == "annual",
               FinancialStatement.period_end <= as_of.date())
        .order_by(FinancialStatement.period_end.desc()).limit(5)).scalars().all())
    # PERIOD END IS NOT PUBLICATION. A statement whose period closed before the cutoff may not
    # have been FILED by then, and no filing date is stored for these rows — only a retrieval
    # time (documented in the fundamentals audit). For a replay this cannot be established, so
    # the bucket reports it rather than assuming availability.
    available_at_cutoff = all(
        st.fetched_at is not None and naive_utc(st.fetched_at) <= as_of for st in stmts)
    fin = None
    if stmts and not available_at_cutoff:
        fin = {"annual_periods": 0,
               "unavailable_reason": "no filing date is stored for these statements, and their "
                                     "retrieval time is after this cutoff — availability at "
                                     "the cutoff cannot be established"}
    elif stmts:
        fin = {"annual_periods": len(stmts), "revenue": stmts[0].total_revenue,
               "revenue_prior": stmts[1].total_revenue if len(stmts) > 1 else None,
               "period_end": stmts[0].period_end.isoformat(),
               "reported_year_age_days": (as_of.date() - stmts[0].period_end).days,
               "retrieval_age_days": ((as_of - naive_utc(stmts[0].fetched_at)).days
                                      if stmts[0].fetched_at else None)}

    # CUTOFF-FILTERED. A replay must not read an assessment written after the cutoff it is
    # replaying — that is the historical identity mixed with current evidence, and it would
    # make a retrospective look better informed than anything could have been at the time.
    found = {k: v for k, v in latest_assessments(session, stock.symbol).items()
             if not v.get("cutoff") or naive_utc(datetime.fromisoformat(v["cutoff"])) <= as_of}
    nothing = "no assessment record is stored for this issuer"
    buckets = [
        EB.technical_bucket(setup, session=dates[-1] if dates else None),
        EB.fundamentals_bucket(fin),
        EB.from_assessment(EB.VALUATION, found.get(QV_VALUATION), absent=nothing),
        EB.from_assessment(EB.RISK, found.get(VALUE_TRAP_RISK), absent=nothing),
        # Durability has no bucket of its own in the thirteen; it is risk-adjacent evidence
        # and is folded into INDUSTRY so nothing researched is discarded.
        EB.from_assessment(EB.INDUSTRY, found.get(COMPETITIVE_DURABILITY), absent=nothing),
        EB.unmeasured(EB.MARKET, EB.NOT_IMPLEMENTED,
                      "no multi-horizon market regime is computed for the intelligence layer"),
        EB.unmeasured(EB.SECTOR, EB.NOT_IMPLEMENTED,
                      "sector rotation is stored but not composed into a bucket reading"),
        EB.unmeasured(EB.EARNINGS, EB.NOT_IMPLEMENTED,
                      "earnings events are stored but not composed into a bucket reading"),
        EB.unmeasured(EB.REVISIONS, EB.NOT_COLLECTED,
                      "no forward EPS or revenue consensus history exists in this platform"),
        EB.unmeasured(EB.RELATIVE_STRENGTH, EB.NOT_IMPLEMENTED,
                      "no systematic relative-strength matrix is computed"),
        EB.unmeasured(EB.OPTIONS, EB.NOT_IMPLEMENTED,
                      "options data is stored but not composed into a bucket reading"),
        EB.unmeasured(EB.NEWS, EB.NOT_IMPLEMENTED,
                      "classified headlines are stored but not composed into a bucket reading"),
        EB.unmeasured(EB.CATALYSTS, EB.NOT_IMPLEMENTED,
                      "scheduled events are stored but not composed into a bucket reading"),
    ]
    latest_close = bars[-1]["close"] if bars else None
    return buckets, {"setup": setup, "sessions": dates, "fin": fin,
                     "latest_close": latest_close,
                     "latest_session": dates[-1] if dates else None, "venue": venue}


def _capture(session, stock, *, as_of: datetime, origin: str) -> dict:
    buckets, ctx = build_buckets(session, stock, as_of=as_of)
    subject = f"stock:{stock.symbol}"
    bucket_ids = record_buckets(session, subject_key=subject, as_of=as_of, buckets=buckets)
    setup = ctx["setup"] or {}
    out = {"subject": subject, "as_of": as_of.isoformat(), "origin": origin,
           "latest_session": ctx["latest_session"], "buckets": [b.as_dict() for b in buckets],
           "bucket_ids": bucket_ids, "observations": []}
    for sessions, label in HORIZONS:
        summary = EB.summarise(subject, buckets, horizon_label=label,
                               horizon_sessions=sessions)
        triggers = EB.direction_triggers(summary["direction"],
                                         support=setup.get("support"),
                                         resistance=setup.get("resistance"))
        existing = _existing_observation(session, subject, as_of, sessions, origin)
        if existing is not None:
            # REUSE RETURNS THE STORED RECORD. Returning a freshly computed summary beside an
            # existing id would show the reader today's reasoning under yesterday's identity.
            out["observations"].append({
                "id": existing.id, "horizon": label, "horizon_sessions": sessions,
                "direction": existing.direction, "created": False, "reused": True,
                "reference_price": existing.reference_price,
                "reference_price_as_of": (existing.reference_price_as_of.isoformat()
                                          if existing.reference_price_as_of else None),
                "frozen_inputs_digest": existing.frozen_inputs_digest,
                "summary": existing.summary,
                "note": "stored record returned unchanged; the summary and inputs are the ones "
                        "frozen at capture, not recomputed"})
            continue
        row, created = record_observation(
            session, subject_key=subject, symbol=stock.symbol, origin=origin,
            observed_at=as_of, horizon_sessions=sessions, horizon_label=label,
            direction=summary["direction"], support_quality=summary["support_quality"],
            reference_price=ctx["latest_close"],
            reference_price_as_of=(datetime.fromisoformat(ctx["latest_session"])
                                   if ctx["latest_session"] else None),
            reference_price_source="completed daily close",
            # The conclusion is formed after a completed close, so it could only have been
            # acted on at the next session. Both returns are reported at resolution.
            reference_price_basis="formed_at_close",
            benchmark_symbol=BENCHMARK.get(ctx["venue"]),
            # ORIENTED BY THE READING, not by a fixed template. The boundaries are symmetric
            # facts about the range; which one confirms depends on what is being claimed.
            confirmation_rule=triggers["confirms"],
            invalidation_rule=triggers["invalidates"],
            bucket_ids=bucket_ids, frozen_inputs={"setup": setup, "fundamentals": ctx["fin"],
                                                  "sessions": ctx["sessions"],
                                                  "triggers": triggers},
            summary=summary)
        out["observations"].append({"id": row.id, "horizon": label,
                                    "horizon_sessions": sessions,
                                    "direction": row.direction, "created": created,
                                    "reference_price": row.reference_price,
                                    "reference_price_as_of": (
                                        row.reference_price_as_of.isoformat()
                                        if row.reference_price_as_of else None),
                                    "frozen_inputs_digest": row.frozen_inputs_digest,
                                    "summary": summary})
    return out


@router.get("/stock/{symbol}")
def stock_summary(symbol: str, _user: str = Depends(get_current_username)) -> dict:
    """The summary above, the thirteen buckets below. Captures a PROSPECTIVE observation."""
    now = naive_utc(datetime.now(timezone.utc)).replace(hour=0, minute=0, second=0,
                                                        microsecond=0)
    # as_of stays NAIVE for storage (every timestamp in this schema is); _sessions_back makes
    # its own aware copy for the calendar question.
    with SessionLocal() as session:
        stock = session.execute(
            select(Stock).where(Stock.symbol == symbol.strip().upper())).scalars().first()
        if not stock:
            raise HTTPException(404, f"{symbol} is not an active listing")
        body = _capture(session, stock, as_of=now, origin=PROSPECTIVE)
    body["note"] = ("Prospective observation stored. Outcomes are PENDING until the sessions "
                    "elapse — a stored observation makes future measurement possible and "
                    "establishes no predictive skill by itself.")
    return body


@router.post("/replay/{symbol}")
def replay(symbol: str, sessions_ago: int = Query(90, ge=25, le=400),
           _user: str = Depends(get_current_username)) -> dict:
    """A RETROSPECTIVE observation at a past cutoff, then resolved.

    This proves the machinery. It is NOT evidence of predictive performance: the rules were
    written with the outcomes already in existence, and the result is labelled `replay` so it
    can never be pooled with a prospective one.
    """
    with SessionLocal() as session:
        stock = session.execute(
            select(Stock).where(Stock.symbol == symbol.strip().upper())).scalars().first()
        if not stock:
            raise HTTPException(404, f"{symbol} is not an active listing")
        venue = getattr(stock.market, "value", stock.market)
        now = naive_utc(datetime.now(timezone.utc))
        past = _sessions_back(venue, now, sessions_ago)[0]
        as_of = datetime.fromisoformat(past)
        body = _capture(session, stock, as_of=as_of, origin=REPLAY)

        from db import IntelligenceObservation
        bench = session.execute(select(Stock).where(
            Stock.symbol == BENCHMARK.get(venue, ""))).scalars().first()
        # The benchmark measured from the SAME reference session as the stock, so the excess is
        # a difference over one window rather than two.
        bench_ref = None
        if bench and body["latest_session"]:
            bench_ref = _closes_for(session, bench.id, [body["latest_session"]]).get(
                body["latest_session"])
        for o in body["observations"]:
            row = session.execute(select(IntelligenceObservation).where(
                IntelligenceObservation.id == o["id"])).scalars().first()
            # Sessions are CONSTRUCTED and capped at the last completed day, so a missing bar
            # is a gap rather than a shifted endpoint, and nothing resolves against a forming
            # or future session.
            fwd = EB.sessions_forward(venue, as_of, row.horizon_sessions,
                                      is_trading_day=is_trading_day, not_after=now,
                                      session_bounds=session_bounds)
            closes = _closes_for(session, stock.id, fwd)
            bcloses = _closes_for(session, bench.id, fwd) if bench else None
            # ADJUSTMENT VERIFIED OVER THE OUTCOME WINDOW, NOT ASSERTED. This used to pass
            # `adjustment_consistent=True` on the strength of the direction screen's check —
            # but that check covers the 21-session SETUP window BEFORE the cutoff, and says
            # nothing about a split or distribution during the 5/20/63 sessions the return is
            # actually measured over, for the stock or for the benchmark. The window spans the
            # REFERENCE session too: an action between the reference close and the first
            # measured session corrupts the descriptive return just as badly as one in the
            # middle.
            window = ([body["latest_session"]] if body["latest_session"] else []) + fwd
            series = {"stock": _adjustment_bars(session, stock.id, window)}
            actions = {"stock": _actions_for(session, stock.symbol, window)}
            coverage = {}
            if (cov := _coverage_for(session, stock.symbol)):
                coverage["stock"] = cov
            if bench:
                series["benchmark"] = _adjustment_bars(session, bench.id, window)
                actions["benchmark"] = _actions_for(session, bench.symbol, window)
                if (bcov := _coverage_for(session, bench.symbol)):
                    coverage["benchmark"] = bcov
            adj = EB.adjustment_evidence(series, basis=EB.SPLIT_ADJUSTED_PRICE,
                                         actions=actions, coverage=coverage)
            outcome, created = resolve(
                session, row, session_closes=closes, expected_sessions=fwd,
                benchmark_reference=bench_ref, benchmark_closes=bcloses,
                adjustment_consistent=adj["consistent"], adjustment_basis=adj["reason"],
                adjustment_factors=adj["factors"], adjustment=adj,
                return_basis=adj["basis"] if adj["consistent"] else None)
            o["outcome"] = {
                "resolution_state": outcome.resolution_state,
                "sessions_elapsed": outcome.sessions_elapsed,
                "descriptive_return": outcome.descriptive_return,
                "simulated_next_session_close_return":
                    outcome.simulated_executable_return,
                "benchmark_return_same_window": outcome.benchmark_return,
                "benchmark_return_entry_window": outcome.benchmark_entry_return,
                "excess_return": outcome.excess_return,
                "return_basis": outcome.return_basis,
                "superseded_state": outcome.superseded_state,
                # WHICH RESOLVER produced this figure, and whether a later one has replaced it.
                # A performance display must read only rows whose `superseded_by` is null, or
                # it pools a corrected reading with the defective one it replaced.
                "resolver_fingerprint": outcome.resolver_fingerprint,
                "superseded_by": outcome.superseded_by_id,
                # THREE SEPARATE ANSWERS, never collapsed into one.
                "evidence_status": outcome.evidence_status,
                "evidence_label": EB.EVIDENCE_LABEL.get(outcome.evidence_status or ""),
                "performance_eligibility": outcome.performance_eligibility,
                "adjustment_evidence": adj,
                "attempts": len(outcome.attempts or []),
                "basis": outcome.resolution_basis, "created": created}
    body["note"] = ("RETROSPECTIVE REPLAY. Proves the capture and resolution machinery works. "
                    "NOT evidence of predictive performance — the rules were written with these "
                    "outcomes already in existence. Never pool with prospective results.")
    return body


@router.get("/outcomes/{symbol}")
def outcomes(symbol: str, _user: str = Depends(get_current_username)) -> dict:
    """Every observation for a symbol with its CURRENT outcome, plus the superseded history.

    SEPARATED BY ORIGIN AND RETURN BASIS, never pooled: a retrospective replay's rules were
    written with its outcomes already in existence, and two different return bases are two
    different measurements.
    """
    from db import IntelligenceObservation, ObservationOutcome
    sym = symbol.strip().upper()
    with SessionLocal() as session:
        obs = session.execute(select(IntelligenceObservation).where(
            IntelligenceObservation.symbol == sym)
            .order_by(IntelligenceObservation.observed_at,
                      IntelligenceObservation.horizon_sessions)).scalars().all()
        rows, current_fp = [], resolver_fingerprint()
        # WHICH CAPTURE POLICY IS CURRENT. A rule change files a NEW observation beside the old
        # one rather than rewriting it, so a symbol legitimately carries several generations.
        # The reader's conclusion must come from the current one — the screen was showing a
        # superseded capture's triggers because the renderer took the FIRST row it found.
        capture_fp = EB.policy_fingerprint()
        for o in obs:
            outs = session.execute(select(ObservationOutcome).where(
                ObservationOutcome.observation_id == o.id)
                .order_by(ObservationOutcome.id)).scalars().all()
            current = next((c for c in outs if c.resolver_fingerprint == current_fp
                            and c.superseded_by_id is None), None)
            rows.append({
                "observation_id": o.id, "origin": o.origin,
                "observed_at": o.observed_at.isoformat(),
                "horizon": o.horizon_label, "horizon_sessions": o.horizon_sessions,
                "direction": o.direction, "support_quality": o.support_quality,
                "reference_price": o.reference_price,
                "reference_price_as_of": (o.reference_price_as_of.isoformat()
                                          if o.reference_price_as_of else None),
                # THE TRIGGERS, which are the actionable half of a reading and were being held
                # on the observation without ever being served.
                "confirmation_rule": o.confirmation_rule,
                "invalidation_rule": o.invalidation_rule,
                # The full oriented record, including the non-directional case where neither
                # boundary confirms anything and saying otherwise would invent a thesis.
                "triggers": (o.frozen_inputs or {}).get("triggers"),
                "summary": o.summary,
                "invalidated_reason": o.invalidated_reason,
                # WHICH GENERATION THIS ROW BELONGS TO. Historical rows keep their original
                # rules unchanged — that is the point of freezing them — and are marked rather
                # than hidden, so a reader can see both and tell which is in force.
                "policy_fingerprint": o.policy_fingerprint,
                "is_current_policy": o.policy_fingerprint == capture_fp,
                "publishable": o.invalidated_reason is None and current is not None,
                "outcome": None if current is None else {
                    "id": current.id, "state": current.resolution_state,
                    "sessions_elapsed": current.sessions_elapsed,
                    "descriptive_return": current.descriptive_return,
                    "simulated_next_session_close_return":
                        current.simulated_executable_return,
                    "benchmark_return_same_window": current.benchmark_return,
                    "benchmark_return_entry_window": current.benchmark_entry_return,
                    "excess_return": current.excess_return,
                    "return_basis": current.return_basis,
                    "evidence_status": current.evidence_status,
                    "evidence_label": EB.EVIDENCE_LABEL.get(current.evidence_status or ""),
                    "performance_eligibility": current.performance_eligibility,
                    "reason": current.resolution_basis},
                # RETAINED, NOT SHOWN AS RESULTS. Earlier versions stay auditable and are
                # explicitly excluded from any performance reading.
                "superseded": [{"id": c.id, "resolver": c.resolver_fingerprint,
                                "superseded_by": c.superseded_by_id,
                                "state": c.resolution_state,
                                "descriptive_return": c.descriptive_return,
                                "excess_return": c.excess_return}
                               for c in outs if c is not current]})
        counts = {}
        for r in rows:
            key = (r["outcome"] or {}).get("state") or "NOT_RESOLVED"
            if r["invalidated_reason"]:
                key = "INVALID_CAPTURE"
            counts[key] = counts.get(key, 0) + 1
        pools = coverage_counts(session, origin=REPLAY, symbol=sym)
        pools_prospective = coverage_counts(session, origin=PROSPECTIVE, symbol=sym)

        # WHAT A COUNT LIKE "6 pending" IS MADE OF. Six pending outcomes on one day is two
        # captures of three horizons, not six independent observations, and the difference
        # decides whether a reader reads it as coverage or as repetition.
        captures: dict = {}
        for r in rows:
            key = (r["origin"], r["observed_at"][:10], r["policy_fingerprint"])
            c = captures.setdefault(key, {
                "origin": r["origin"], "captured_on": r["observed_at"][:10],
                "policy_fingerprint": r["policy_fingerprint"],
                "is_current_policy": r["is_current_policy"],
                "horizons": [], "observation_ids": []})
            c["horizons"].append(r["horizon"])
            c["observation_ids"].append(r["observation_id"])
        capture_list = sorted(captures.values(),
                              key=lambda c: (c["captured_on"], c["is_current_policy"]))
    return {"symbol": sym, "resolver_fingerprint": current_fp,
            "capture_policy_fingerprint": capture_fp,
            "captures": capture_list,
            "observations": rows,
            "state_counts": counts,
            "reason_labels": UNRESOLVED_LABEL,
            # SERVED, NEVER COPIED client-side.
            "evidence_labels": EB.EVIDENCE_LABEL,
            "provisional_remedy": EB.PROVISIONAL_REMEDY,
            "coverage": {"replay": pools, "prospective": pools_prospective},
            "capture_note": ("A rule change files a NEW observation beside the old one and "
                             "never rewrites it, so a symbol can carry several capture "
                             "generations. Only rows marked `is_current_policy` supply the "
                             "conclusion in force; the rest are retained with their original "
                             "rules and are visible as history."),
            "note": ("Superseded outcomes are retained as audit records and are excluded from "
                     "every performance reading. Replay and prospective origins are never "
                     "pooled, and neither are two different return bases.")}


@router.post("/corporate-actions/{symbol}")
def ingest_corporate_actions(symbol: str,
                             covers_from: str = Query(...), covers_to: str = Query(...),
                             _user: str = Depends(get_current_username)) -> dict:
    """Ingest a SOURCED corporate-action history for one symbol over a bounded span.

    Deliberately per-symbol and span-bounded: the pilot is MU and its benchmark over the
    affected replay windows, and widening to the universe is a separate decision with its own
    request budget.
    """
    from ..intel_reports import corporate_actions as CA
    with SessionLocal() as session:
        try:
            return CA.ingest(session, symbol.strip().upper(),
                             covers_from=date.fromisoformat(covers_from),
                             covers_to=date.fromisoformat(covers_to))
        except Exception as exc:  # the provider is the likeliest failure, and it must say so
            log.warning("corporate_action.ingest_failed", symbol=symbol, error=str(exc))
            raise HTTPException(502, f"{type(exc).__name__}: {exc}")
