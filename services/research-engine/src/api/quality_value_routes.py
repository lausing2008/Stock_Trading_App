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
from db import SessionLocal, Stock, FinancialStatement, Price, TimeFrame

from ..intel_reports.quality_value import (
    ALL_GATES, REQUIRED_FOR_ENTRY, GATE_CLAIM, GateStatus, POLICY_VERSION, State, compose,
    business_quality_gate, durability_gate, valuation_gate, value_trap_gate,
    entry_condition_gate, naive_utc, policy_fingerprint)
from ..intel_reports.quality_value_store import (
    record_evaluation, latest_evaluations, evaluation_history, freeze_inputs)
from ..intel_reports.prospective_capture import (
    capture_earnings_estimates, capture_analyst_forwards)

log = get_logger("research-engine.quality_value")
router = APIRouter(prefix="/quality-value", tags=["quality-value"])

#: Bounded on purpose. The plan's pilot universe is deliberately small, and evaluating 189
#: symbols' price history on a page view is a cost nobody asked for while every result is
#: `insufficient_evidence`.
MAX_SYMBOLS = 200


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
    cutoff = now.replace(second=0, microsecond=0)
    out, coverage = [], {n: {s.value: 0 for s in GateStatus} for n in ALL_GATES}
    states = {s.value: 0 for s in State}
    stored, persist_errors = 0, []

    with SessionLocal() as session:
        q = select(Stock).where(Stock.delisted.is_(False)).order_by(Stock.symbol)
        wanted = [s.strip().upper() for s in symbols.split(",") if s.strip()] if symbols else None
        if wanted:
            q = q.where(Stock.symbol.in_(wanted))
        stocks = list(session.execute(q.limit(MAX_SYMBOLS)).scalars().all())

        for stock in stocks:
            fin = _statement_evidence(session, stock.symbol, now)
            # Industry decides whether leverage is even a solvency reading.
            fin["industry"] = stock.industry
            px = _price_evidence(session, stock, now)
            gates = [business_quality_gate(fin), durability_gate(), valuation_gate(),
                     entry_condition_gate(px), value_trap_gate(fin)]
            ev = compose(stock.symbol, gates)
            for g in gates:
                coverage[g.name][g.status.value] += 1
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
                persisted = {"id": row.id if row else None, "created": created}
                stored += 1 if created else 0
            except Exception as exc:          # noqa: BLE001
                session.rollback()
                persist_errors.append(f"{stock.symbol}: {type(exc).__name__}")
                log.warning("quality_value.persist_failed", symbol=stock.symbol,
                            error=str(exc)[:200])

            out.append({**ev.as_dict(), "name": stock.name, "sector": stock.sector,
                        "persisted": persisted})

    return {
        "mode": "shadow",
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
        "required_for_entry": list(REQUIRED_FOR_ENTRY),
        "evaluations": out,
        "notes": [
            "SHADOW MODE: evaluations are persisted immutably so the screen can later be "
            "measured, but no alert type is registered and no email can be sent from here.",
            "Each verdict is stored under a fingerprint of the rules that produced it. Change "
            "any threshold and new rows land BESIDE the old ones — nothing is rewritten.",
            "An empty eligible list is a valid result, not a defect. Competitive durability and "
            "valuation have no stored evidence, and both are required for entry research.",
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
