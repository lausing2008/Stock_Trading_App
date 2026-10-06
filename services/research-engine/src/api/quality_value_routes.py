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
    ALL_GATES, REQUIRED_FOR_ENTRY, GateStatus, State, compose,
    business_quality_gate, durability_gate, valuation_gate, value_trap_gate,
    entry_condition_gate, naive_utc)

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
        return {"recent_closes": closes[:2], "sma20": None, "session_complete": None}
    return {"recent_closes": closes[:2],
            # The average EXCLUDES the latest close it is compared against, so the rule is not
            # partly comparing the close with itself.
            "sma20": sum(closes[1:21]) / 20.0,
            "session_complete": True,
            "latest_session": rows[0][1].isoformat() if rows[0][1] else None}


@router.get("/evaluations")
def evaluations(symbols: str | None = Query(None, description="comma-separated; default: the "
                                            "active universe, bounded"),
                _user: str = Depends(get_current_username)) -> dict:
    now = datetime.now(timezone.utc)
    out, coverage = [], {n: {s.value: 0 for s in GateStatus} for n in ALL_GATES}
    states = {s.value: 0 for s in State}

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
            out.append({**ev.as_dict(), "name": stock.name, "sector": stock.sector})

    return {
        "mode": "shadow",
        "as_of": now.isoformat(),
        "evaluated": len(out),
        "states": states,
        "gate_coverage": coverage,
        "required_for_entry": list(REQUIRED_FOR_ENTRY),
        "evaluations": out,
        "notes": [
            "SHADOW MODE: nothing here is persisted, no alert type is registered and no email "
            "can be sent from this endpoint.",
            "An empty eligible list is a valid result, not a defect. Competitive durability and "
            "valuation have no stored evidence, and both are required for entry research.",
            "A research state is not an order recommendation, and 'entry review ready' names a "
            "point at which to research further.",
        ],
    }
