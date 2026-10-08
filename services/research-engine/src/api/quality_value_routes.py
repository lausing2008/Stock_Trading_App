"""Quality & Value: the SHADOW dashboard endpoint.

SHADOW MODE IS THE WHOLE POINT OF THIS STAGE. This endpoint evaluates, returns and persists
nothing; it sends no email, registers no alert type and creates no subscription. The plan's
step 3 is "display pilot research states and evidence; unknown moat/valuation remains unknown",
and reading this endpoint is how that unknown becomes countable.

WHAT IT IS ACTUALLY FOR. Today the answer for every symbol is `insufficient_evidence`, because
the readiness inventory measured that the two gates deciding eligibility — competitive
durability and valuation — have no stored evidence at all. A dashboard of 189 identical rows
would be useless; what IS useful is the per-gate coverage underneath, which says exactly which
inputs are missing for how many companies and therefore what acquiring would change. So the
response leads with gate coverage, not with a candidate list that is correctly empty.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select, func

from common.jwt_auth import get_current_username
from common.logging import get_logger
from common.market_calendar import is_trading_day, MIN_COVERAGE_YEAR
from db import SessionLocal, Stock, FinancialStatement, Price, TimeFrame

from ..intel_reports.quality_value import (
    ALL_GATES, REQUIRED_FOR_ENTRY, GATE_CLAIM, GateStatus, POLICY_VERSION, State, compose,
    COMPETITIVE_DURABILITY, VALUATION, VALUE_TRAP_RISK,
    business_quality_gate, durability_gate, valuation_gate, value_trap_gate,
    entry_condition_gate, naive_utc, policy_fingerprint, status_catalog,
    company_summary)
from ..intel_reports.quality_value_store import (
    record_evaluation, latest_evaluations, evaluation_history, freeze_inputs,
    latest_assessments, record_assessment)
from ..intel_reports.prospective_capture import (
    capture_earnings_estimates, capture_analyst_forwards)

log = get_logger("research-engine.quality_value")
router = APIRouter(prefix="/quality-value", tags=["quality-value"])

#: Bounded on purpose. The plan's pilot universe is deliberately small, and evaluating 189
#: symbols' price history on a page view is a cost nobody asked for while every result is
#: `insufficient_evidence`.
MAX_SYMBOLS = 200


@router.get("/setups")
def setups(market: str = Query("ALL", pattern="^(ALL|US|HK)$"),
           direction: str = Query("all", pattern="^(all|breakout|breakdown|breakout_watch|breakdown_watch|range|unknown)$"),
           limit: int = Query(20, ge=1, le=100), sector: str | None = None,
           symbols: str | None = None,
           _user: str = Depends(get_current_username)) -> dict:
    """Read-only technical screen over ALL active listings, before per-market ranking.

    Conservatively exclude today's local session even after close: intraday ingest can
    overwrite a daily bar. This intentionally trades freshness for a consistent EOD screen.
    No quality evaluation is created or reused by this endpoint.
    """
    from zoneinfo import ZoneInfo
    from ..intel_reports.direction_screen import (assess, select_setups, POLICY,
                                                  classify_instrument)
    from db import FinancialStatement
    now = datetime.now(timezone.utc)
    wanted = {s.strip().upper() for s in (symbols or "").split(",") if s.strip()}
    output, session_dates = [], {}
    with SessionLocal() as session:
        stocks = session.execute(select(Stock).where(
            Stock.active.is_(True), Stock.delisted.is_(False))).scalars().all()
        # ONE batched lookup, not a query per row. Statements are the only signal with no
        # observed counterexample (0 of 189 carry them without a sector), so they lead the
        # precedence in classify_instrument().
        with_statements = {r[0] for r in session.execute(
            select(FinancialStatement.symbol)
            .where(FinancialStatement.period_type == "annual").distinct()).all()}
        for venue, tz in (("US", "America/New_York"), ("HK", "Asia/Hong_Kong")):
            if market not in ("ALL", venue):
                continue
            local = now.astimezone(ZoneInfo(tz))
            # Calendar exhaustion must not manufacture a completed-session decision.
            if local.year > MIN_COVERAGE_YEAR:
                continue
            day = local.replace(hour=12, minute=0, second=0, microsecond=0) - timedelta(days=1)
            days = []
            while len(days) < 21:
                if is_trading_day(venue, day):
                    days.append(day.date().isoformat())
                day -= timedelta(days=1)
            days.reverse()
            session_dates[venue] = days[-1]
            members = [s for s in stocks if getattr(s.market, "value", s.market) == venue
                       and (not wanted or s.symbol in wanted)]
            if not members:
                continue
            # Daily ts dates are stored session labels, not intraday instants. Window rank
            # bounds data per issuer while avoiding an alphabetical universe truncation.
            ranked = select(
                Price.stock_id, Price.ts, Price.high, Price.low, Price.close,
                Price.volume, Price.adj_close,
                func.row_number().over(partition_by=Price.stock_id,
                                       order_by=Price.ts.desc()).label("rn")
            ).where(Price.stock_id.in_([s.id for s in members]),
                    Price.timeframe == TimeFrame.D1,
                    Price.ts >= datetime.fromisoformat(days[0]),
                    Price.ts < datetime.fromisoformat(days[-1]) + timedelta(days=1)
                    ).subquery()
            prices = session.execute(select(ranked).where(ranked.c.rn <= 21)
                                     .order_by(ranked.c.ts)).mappings().all()
            grouped = {}
            for r in prices:
                grouped.setdefault(r["stock_id"], []).append({
                    "date": r["ts"].date().isoformat(),
                    **{k: r[k] for k in ("high", "low", "close", "volume", "adj_close")}})
            for stock in members:
                output.append({"symbol": stock.symbol, "name": stock.name,
                               "market": venue, "sector": stock.sector,
                               "currency": stock.currency,
                               # A fund's break is a statement about its basket, and a
                               # leveraged fund's about a daily-reset multiple of one.
                               "instrument": classify_instrument(
                                   symbol=stock.symbol, name=stock.name,
                                   sector=stock.sector, industry=stock.industry,
                                   has_annual_statements=stock.symbol in with_statements),
                               "setup": assess(grouped.get(stock.id, []), days)})
    filtered = [r for r in output if (direction == "all" or r["setup"]["direction"] == direction)
                and (not sector or r["sector"] == sector)]
    return {"policy": POLICY, "as_of": now.isoformat(), "session_dates": session_dates,
            "scanned": len(output), "matching": len(filtered),
            "calendar_available": now.year <= MIN_COVERAGE_YEAR,
            "sectors": sorted({r["sector"] for r in output if r["sector"]}),
            "instrument_counts": {t: sum(1 for r in output if r["instrument"]["type"] == t)
                                  for t in ("operating_company", "fund", "unverified")},
            "rows": select_setups(output, market, direction, limit, sector),
            "note": "Up to the selected limit PER market among active platform listings, not the whole exchange. "
                    "Each row carries its instrument type and the basis for it: operating "
                    "company, fund, or unverified where the platform cannot establish either. "
                    "Latest local day excluded. "
                    "Order: observed range breaks, boundary watches, inside range, unknown; "
                    "then relative volume descending and boundary distance ascending. "
                    "Rules are uncalibrated: no high-chance claim or probability. "
                    "This screen does not change Quality & Value eligibility."}


def _statement_evidence(session, symbol: str, now: datetime) -> dict:
    now = naive_utc(now)
    rows = list(session.execute(
        select(FinancialStatement)
        .where(FinancialStatement.symbol == symbol,
               FinancialStatement.period_type == "annual")
        .order_by(FinancialStatement.period_end.desc()).limit(5)).scalars().all())
    if not rows:
        return {"annual_periods": 0}
    newest = rows[0]
    fcf = newest.free_cashflow
    if fcf is None and newest.operating_cashflow is not None \
            and newest.capital_expenditure is not None:
        fcf = newest.operating_cashflow + newest.capital_expenditure
    prior_fcf = None
    if len(rows) > 1:
        p = rows[1]
        prior_fcf = p.free_cashflow
        if prior_fcf is None and p.operating_cashflow is not None \
                and p.capital_expenditure is not None:
            prior_fcf = p.operating_cashflow + p.capital_expenditure
    nde = None
    if newest.total_debt is not None and newest.cash_and_equivalents is not None \
            and newest.total_equity:
        nde = (newest.total_debt - newest.cash_and_equivalents) / newest.total_equity
    return {
        "annual_periods": len(rows),
        "statement_ids": [r.id for r in rows],
        "revenue": newest.total_revenue,
        "free_cashflow": fcf,
        "free_cashflow_prior": prior_fcf,
        "net_debt_to_equity": nde,
        "reported_year_age_days": (now.date() - newest.period_end).days,
        "retrieval_age_days": ((now - naive_utc(newest.fetched_at)).days
                               if newest.fetched_at is not None else None),
    }


def _price_evidence(session, stock, now: datetime) -> dict:
    """Completed daily closes only. A forming bar is excluded by the cutoff, not by a flag."""
    rows = list(session.execute(
        select(Price.close, Price.ts)
        .where(Price.stock_id == stock.id, Price.timeframe == TimeFrame.D1)
        .order_by(Price.ts.desc()).limit(21)).all())
    closes = [float(r[0]) for r in rows if r[0] is not None]
    if len(closes) < 21:
        # NOT a 20-session average computed over whatever is there. A short window would
        # answer a different question under the same name.
        return {"recent_closes": closes[:2], "sessions_read": len(closes), "sma20": None, "session_complete": None}
    return {"recent_closes": closes[:2], "sessions_read": len(closes),
            # The average EXCLUDES the latest close it is compared against, so the rule is not
            # partly comparing the close with itself.
            "sma20": sum(closes[1:21]) / 20.0,
            "session_complete": True,
            "latest_session": rows[0][1].isoformat() if rows[0][1] else None}


@router.get("/evaluations")
def evaluations(symbols: str | None = Query(None, description="comma-separated; default: the "
                                            "active universe, bounded"),
                _user: str = Depends(get_current_username)) -> dict:
    now = naive_utc(datetime.now(timezone.utc))
    # ONE CUTOFF FOR THE WHOLE RUN, truncated to the minute. Every row in a run must share it,
    # or the same evaluation re-read a second later becomes a different stored row and the
    # idempotency the unique constraint provides is lost.
    # DAILY, NOT PER-MINUTE. At minute granularity every re-run wrote a new row, so the reuse
    # path never ran and 200 companies accumulated a verdict a minute. The gates read completed
    # daily sessions and once-fetched fundamentals, so a day is the resolution the inputs
    # actually have — and re-running within one now genuinely reuses.
    cutoff = now.replace(hour=0, minute=0, second=0, microsecond=0)
    out, coverage = [], {n: {s.value: 0 for s in GateStatus} for n in ALL_GATES}
    states = {s.value: 0 for s in State}
    stored, persist_errors, reused = 0, [], []

    with SessionLocal() as session:
        q = select(Stock).where(Stock.delisted.is_(False)).order_by(Stock.symbol)
        wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
        if wanted:
            q = q.where(Stock.symbol.in_(wanted))
        stocks = list(session.execute(q.limit(MAX_SYMBOLS)).scalars().all())
        # ONE QUERY, not one per symbol: statement presence is the strongest instrument signal
        # and is needed for every row.
        annual_statement_symbols = {r[0] for r in session.execute(
            select(FinancialStatement.symbol)
            .where(FinancialStatement.period_type == "annual").distinct()).all()}

        for stock in stocks:
            fin = _statement_evidence(session, stock.symbol, now)
            # Industry decides whether leverage is even a solvency reading.
            fin["industry"] = stock.industry
            px = _price_evidence(session, stock, now)
            # CONNECTED RESEARCH, where it exists. An assessment maps to its OWN verdict —
            # a completed review that found the evidence insufficient still blocks.
            found = latest_assessments(session, stock.symbol)
            gates = [business_quality_gate(fin),
                     durability_gate(assessment=found.get(COMPETITIVE_DURABILITY)),
                     valuation_gate(assessment=found.get(VALUATION)),
                     entry_condition_gate(px),
                     value_trap_gate(fin, assessment=found.get(VALUE_TRAP_RISK))]
            ev = compose(stock.symbol, gates)
            evaluated_here = {g.name for g in gates}
            for g in gates:
                coverage[g.name][g.status.value] += 1
            # EVERY ROW ACCOUNTS FOR EVERY COMPANY. A gate this run does not evaluate is
            # counted as not_assessed rather than left at zero, so the row reconciles and the
            # absence is stated rather than looking like 200 companies went missing.
            for name in ALL_GATES:
                if name not in evaluated_here:
                    coverage[name][GateStatus.NOT_ASSESSED.value] += 1
            states[ev.state.value] += 1

            # PERSIST, OR THIS IS A PREVIEW RATHER THAN A SHADOW TRIAL. A screen that only
            # recomputes cannot be measured: the inputs move underneath it, so three months on
            # there is no way to say what it concluded on any given day. The write is
            # idempotent per (symbol, cutoff, policy fingerprint), so repeated page views do
            # not accumulate rows, and a rule change lands beside the old verdict rather than
            # over it. A storage failure must not take the view down.
            persisted = None
            try:
                # THE VALUES, NOT ONLY THE POINTERS. financial_statements is refreshed in
                # place and a market cap is refetched, so references alone cannot reproduce
                # this verdict later — see freeze_inputs().
                row, created = record_evaluation(
                    session, ev, cutoff=cutoff,
                    evidence_refs=freeze_inputs(
                        {"fundamentals": {k: v for k, v in fin.items() if k != "industry"},
                         "industry": fin.get("industry"),
                         "prices": {k: px.get(k) for k in
                                    ("recent_closes", "sma20", "session_complete",
                                     "latest_session", "sessions_read")}},
                        refs={"financial_statements": fin.get("statement_ids") or [],
                              "stock_id": stock.id}))
                # A REUSE IS NOT A SILENCE. "0 new evaluations stored" left a reader unable
                # to tell a preserved result from a dropped write, so the existing row's id and
                # its ORIGINAL cutoff travel with it.
                persisted = {"id": row.id if row else None, "created": created,
                             "reused": (not created),
                             "original_cutoff": (row.cutoff.isoformat()
                                                 if row is not None and not created else None)}
                stored += 1 if created else 0
                if row is not None and not created:
                    reused.append({"symbol": stock.symbol, "id": row.id,
                                   "cutoff": row.cutoff.isoformat()})
            except Exception as exc:          # noqa: BLE001
                session.rollback()
                persist_errors.append(f"{stock.symbol}: {type(exc).__name__}")
                log.warning("quality_value.persist_failed", symbol=stock.symbol,
                            error=str(exc)[:200])

            # WHAT KIND OF THING IS THIS? A company assessment applied to a pooled vehicle is
            # not weak evidence, it is about the wrong subject — GLD was shown as
            # "Fund (inferred)" while being reported as missing annual statements and lacking a
            # moat, neither of which is a finding about a gold trust.
            instrument = classify_instrument(
                symbol=stock.symbol, name=stock.name, sector=stock.sector,
                industry=stock.industry,
                has_annual_statements=stock.symbol in annual_statement_symbols)
            out.append({**ev.as_dict(), "name": stock.name, "sector": stock.sector,
                        "persisted": persisted, "instrument": instrument,
                        # THE CONCLUSION, ABOVE THE AUDIT DETAIL.
                        "summary": company_summary(ev, gates, instrument=instrument)})

    # THE EXPLANATION IS GENERATED FROM THE RESULTS, not written once and left behind. The
    # previous banner was a fixed string saying assessments "are not connected" — true when it
    # was written and false the moment MU and CRDO were connected, while the table beside it
    # showed their verdicts.
    with_assessment = sorted({e["symbol"] for e in out
                              if any((g.get("evidence") or {}).get("verdict")
                                     for g in e["gates"])})
    without = len(out) - len(with_assessment)
    eligible = states.get(State.ENTRY_REVIEW_READY.value, 0)
    if eligible:
        headline = (f"{eligible} company(ies) reached entry review at this cutoff.")
    elif with_assessment and len(with_assessment) <= 6:
        headline = (
            f"No entry-review candidates. {', '.join(with_assessment)} "
            f"{'has' if len(with_assessment) == 1 else 'have'} stored assessments, but required "
            f"evidence remains insufficient."
            + (f" Assessments are missing for the other {without} companies." if without else ""))
    elif with_assessment:
        headline = (
            f"No entry-review candidates. {len(with_assessment)} companies have stored "
            f"assessments with required evidence still insufficient"
            + (f"; assessments are missing for the other {without}." if without else "."))
    else:
        headline = ("No entry-review candidates. No company has a stored durability, valuation "
                    "or risk assessment yet, so the empty list reflects unfinished research "
                    "rather than a judgement about any company.")

    return {
        "mode": "shadow",
        "headline": headline,
        "assessment_coverage": {"with_assessment": with_assessment,
                                "without_assessment": without},
        "reused_evaluations": reused,
        "as_of": now.isoformat(),
        "cutoff": cutoff.isoformat(),
        "policy_version": POLICY_VERSION,
        "policy_fingerprint": policy_fingerprint(),
        "evaluated": len(out),
        "stored_new": stored,
        "persist_errors": persist_errors,
        "states": states,
        "gate_coverage": coverage,
        "gate_claims": GATE_CLAIM,
        # ONE MAPPING, SERVED. The page must not keep its own copy — see status_catalog().
        "status_catalog": status_catalog(),
        "required_for_entry": list(REQUIRED_FOR_ENTRY),
        "evaluations": out,
        "notes": [
            "SHADOW MODE: evaluations are persisted immutably so the screen can later be "
            "measured, but no alert type is registered and no email can be sent from here.",
            "Each verdict is stored under a fingerprint of the rules that produced it. Change "
            "any threshold and new rows land BESIDE the old ones — nothing is rewritten.",
            "An empty eligible list is a valid result, not a defect. A gate reading a stored "
            "assessment that concluded 'insufficient' is finished work AND a reason to stay "
            "ineligible — those are not in tension.",
            "A research state is not an order recommendation, and 'entry review ready' names a "
            "point at which to research further.",
            "Read each gate with its `does_not_establish`: a fundamentals PASS means the stored "
            "data cleared the configured checks, NOT that this is a quality business, and a "
            "price-rule PASS means a timing rule was satisfied, NOT that an entry is suitable.",
        ],
    }


@router.get("/history/{symbol}")
def history(symbol: str, _user: str = Depends(get_current_username)) -> dict:
    """Every stored verdict for one symbol, ACROSS policies.

    Deliberately unscoped by fingerprint: this is the view that answers whether the screen's
    answer changed because the company changed or because we changed the rules, and that needs
    both kinds of row side by side with the policy stamped on each.
    """
    with SessionLocal() as session:
        rows = evaluation_history(session, symbol.strip().upper())
        return {"symbol": symbol.strip().upper(),
                "current_policy_fingerprint": policy_fingerprint(),
                "evaluations": [
                    {"cutoff": r.cutoff.isoformat(), "state": r.state,
                     "policy_version": r.policy_version,
                     "policy_fingerprint": r.policy_fingerprint,
                     "produced_under_current_policy":
                         r.policy_fingerprint == policy_fingerprint(),
                     "blocking": r.blocking, "evidence_refs": r.evidence_refs,
                     "recorded_at": r.created_at.isoformat() if r.created_at else None}
                    for r in rows]}


@router.post("/assessments/seed")
def seed_assessments(_user: str = Depends(get_current_username)) -> dict:
    """Write the reviewed MU and CRDO assessments as versioned rows. Idempotent per version."""
    from ..intel_reports.assessment_seed import ASSESSMENTS, CUTOFF, AUTHOR
    written, existing = [], []
    with SessionLocal() as session:
        for spec in ASSESSMENTS:
            _id, created = record_assessment(session, spec, cutoff=CUTOFF, author=AUTHOR)
            (written if created else existing).append(
                f"{spec['symbol']}/{spec['dimension']}/v{spec.get('version', 1)}")
    return {"written": written, "already_present": existing,
            "note": "a revision must arrive as a new version; nothing here updates a row."}


@router.get("/assessments/{symbol}")
def assessments(symbol: str, _user: str = Depends(get_current_username)) -> dict:
    """Every connected assessment for one issuer, newest version per dimension."""
    with SessionLocal() as session:
        return {"symbol": symbol.strip().upper(),
                "assessments": latest_assessments(session, symbol.strip().upper())}


@router.post("/capture/estimates")
def capture_estimates(_user: str = Depends(get_current_username)) -> dict:
    """Snapshot the current consensus for scheduled, not-yet-released earnings events.

    A POST because it WRITES. It makes no provider call and spends no budget — it copies rows
    another service already fetched into an append-only table, which is the only way the
    consensus as it stood today remains recoverable tomorrow.
    """
    with SessionLocal() as session:
        earnings = capture_earnings_estimates(session)
        forwards = capture_analyst_forwards(session)
        return {
            "earnings_estimates": earnings,
            "analyst_forwards": forwards,
            "note": (
                "MEASURED 2026-10-06: earnings_events.eps_estimate holds values only for "
                "events that have ALREADY REPORTED (676 rows) and none for the 129 scheduled "
                "ones, so the earnings arm will capture nothing until an upstream job writes "
                "a forward consensus. It is left in place because it costs nothing and will "
                "begin capturing the moment one does. The analyst arm captures what this "
                "platform genuinely holds forward: target price and forward P/E, both "
                "overwritten in place by every refresh."),
        }
