"""Stock summary, observation capture and outcome resolution.

Read-only except for two POSTs that WRITE an observation. No email, no alert type, no trading
behaviour — this endpoint cannot change any of them.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select

from common.jwt_auth import get_current_username
from common.logging import get_logger
from common.market_calendar import is_trading_day
from db import SessionLocal, Stock, Price, TimeFrame

from ..intel_reports import evidence_buckets as EB
from ..intel_reports.direction_screen import assess
from ..intel_reports.observations import (HORIZONS, PROSPECTIVE, REPLAY,
                                          record_buckets, record_observation, resolve)
from ..intel_reports.quality_value_store import latest_assessments
from ..intel_reports.quality_value import (COMPETITIVE_DURABILITY, VALUATION as QV_VALUATION,
                                           VALUE_TRAP_RISK, naive_utc)

log = get_logger("research-engine.observations")
router = APIRouter(prefix="/intelligence", tags=["intelligence"])

BENCHMARK = {"US": "SPY", "HK": "2800.HK"}


def _sessions_back(venue: str, anchor: datetime, n: int) -> list:
    """The n completed trading sessions ending strictly before `anchor`'s local day."""
    out, day = [], anchor - timedelta(days=1)
    while len(out) < n:
        if is_trading_day(venue, day):
            out.append(day.date().isoformat())
        day -= timedelta(days=1)
    return list(reversed(out))


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


def _closes_after(session, stock_id: int, after: datetime, n: int) -> list:
    rows = session.execute(
        select(Price.close).where(Price.stock_id == stock_id,
                                  Price.timeframe == TimeFrame.D1, Price.ts > after)
        .order_by(Price.ts).limit(n)).scalars().all()
    return [float(c) if c is not None else None for c in rows]


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
    fin = None
    if stmts:
        fin = {"annual_periods": len(stmts), "revenue": stmts[0].total_revenue,
               "revenue_prior": stmts[1].total_revenue if len(stmts) > 1 else None,
               "period_end": stmts[0].period_end.isoformat(),
               "reported_year_age_days": (as_of.date() - stmts[0].period_end).days,
               "retrieval_age_days": ((as_of - naive_utc(stmts[0].fetched_at)).days
                                      if stmts[0].fetched_at else None)}

    found = latest_assessments(session, stock.symbol)
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
            confirmation_rule=(f"a completed close above {setup.get('resistance')}"
                               if setup.get("resistance") else None),
            invalidation_rule=(f"a completed close below {setup.get('support')}"
                               if setup.get("support") else None),
            bucket_ids=bucket_ids, frozen_inputs={"setup": setup, "fundamentals": ctx["fin"],
                                                  "sessions": ctx["sessions"]},
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
        for o in body["observations"]:
            row = session.execute(select(IntelligenceObservation).where(
                IntelligenceObservation.id == o["id"])).scalars().first()
            closes = _closes_after(session, stock.id, as_of, row.horizon_sessions)
            bcloses = (_closes_after(session, bench.id, as_of, row.horizon_sessions)
                       if bench else None)
            outcome, created = resolve(session, row, closes=closes, benchmark_closes=bcloses)
            o["outcome"] = {
                "resolution_state": outcome.resolution_state,
                "sessions_elapsed": outcome.sessions_elapsed,
                "descriptive_return": outcome.descriptive_return,
                "simulated_executable_return": outcome.simulated_executable_return,
                "benchmark_return": outcome.benchmark_return,
                "excess_return": outcome.excess_return,
                "return_basis": outcome.return_basis,
                "basis": outcome.resolution_basis, "created": created}
    body["note"] = ("RETROSPECTIVE REPLAY. Proves the capture and resolution machinery works. "
                    "NOT evidence of predictive performance — the rules were written with these "
                    "outcomes already in existence. Never pool with prospective results.")
    return body
