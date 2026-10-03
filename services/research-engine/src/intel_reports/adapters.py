"""Reads existing platform data into contract Fields — including the reads that FAIL.

THE DESIGN RULE. Every function here returns a `Field`, never a bare value and never None. A
dimension this platform cannot source yet returns `unavailable("...")` with the reason; one
whose data is too old returns `stale(...)`. That is what makes a partial report useful instead
of misleading, and it is why no caller needs a try/except around a missing table.

NOTHING HERE FETCHES FROM A PROVIDER. These read what the platform has already ingested, so a
report costs no provider quota and cannot be blocked by a rate limit. A dimension that would
need a live call is reported UNAVAILABLE with that as its reason, rather than quietly fetched.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select, func

from db import Price, Signal, Stock, EarningsEvent
from db.models import TimeFrame
from intelligence.report_contract import (
    Evidence, Field, FieldState, StatementClass,
    calculated, observed, stale, unavailable, unknown,
)

#: A daily bar older than this is not evidence about today's trend.
_MAX_BAR_AGE_DAYS = 5
#: A signal older than this describes conditions that have since moved on.
_MAX_SIGNAL_AGE_HOURS = 36


def _naive(dt):
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def daily_bars(session, stock_id: int, limit: int = 70) -> list[Price]:
    return list(session.execute(
        select(Price).where(Price.stock_id == stock_id, Price.timeframe == TimeFrame.D1)
        .order_by(Price.ts.desc()).limit(limit)).scalars().all())


def session_return(bars: list[Price], sessions: int, *, label: str) -> Field:
    """Return over N EXCHANGE SESSIONS — bars, not calendar days.

    Counting calendar days would silently include weekends and holidays, which is how a "5-day
    return" becomes a 3-session return over a long weekend. The templates ask for session counts
    and this honours that literally.
    """
    if len(bars) < sessions + 1:
        return unavailable(
            f"{label} needs {sessions + 1} daily bars, {len(bars)} available",
            units="pct")
    latest, prior = bars[0], bars[sessions]
    if not prior.close:
        return unavailable(f"{label}: the reference bar has no close", units="pct")
    pct = (latest.close - prior.close) / prior.close * 100.0
    return calculated(round(pct, 2), units="pct",
                      evidence_ids=[f"price:{latest.stock_id}:{latest.ts:%Y-%m-%d}",
                                    f"price:{prior.stock_id}:{prior.ts:%Y-%m-%d}"])


def price_as_of(bars: list[Price], now: datetime) -> Field:
    """The latest close, with its own age checked rather than assumed current."""
    if not bars:
        return unavailable("no daily bars ingested for this symbol")
    latest = bars[0]
    age_days = (_naive(now) - _naive(latest.ts)).days
    value = {"close": round(float(latest.close), 4), "ts": latest.ts.isoformat(),
             "basis": "unadjusted close", "session": "regular"}
    if age_days > _MAX_BAR_AGE_DAYS:
        return stale(value, f"latest daily bar is {age_days} sessions old")
    return observed(value, evidence_ids=[f"price:{latest.stock_id}:{latest.ts:%Y-%m-%d}"])


def trend_structure(bars: list[Price]) -> Field:
    """An OBSERVED description of the bars, deliberately not a forecast.

    The contract separates observed trend from forward outlook precisely so that this function
    cannot be read as a prediction: it says what the last N closes did, nothing about what
    happens next.
    """
    if len(bars) < 21:
        return unavailable(f"trend structure needs 21 daily bars, {len(bars)} available")
    closes = [float(b.close) for b in bars]
    sma20 = sum(closes[:20]) / 20
    sma50 = sum(closes[:50]) / 50 if len(closes) >= 50 else None
    last = closes[0]
    above20 = last > sma20
    desc = {
        "last_close": round(last, 4),
        "sma20": round(sma20, 4),
        "sma50": round(sma50, 4) if sma50 is not None else None,
        "above_sma20": above20,
        "above_sma50": (last > sma50) if sma50 is not None else None,
        "structure": ("above both moving averages" if sma50 is not None and last > sma50 and above20
                      else "above the 20-session average" if above20
                      else "below the 20-session average"),
    }
    if sma50 is None:
        desc["sma50_note"] = "fewer than 50 sessions ingested; 50-session average not computed"
    return calculated(desc)


def latest_signal(session, stock_id: int, now: datetime) -> Field:
    """The decision engine's own latest verdict, reported as a SEPARATE source with its time.

    The templates require an existing assessment to be attributed and timestamped rather than
    folded into the report's own reasoning: it is another system's claim, not this report's
    observation, and its age matters.
    """
    row = session.execute(
        select(Signal).where(Signal.stock_id == stock_id)
        .order_by(Signal.ts.desc()).limit(1)).scalars().first()
    if row is None:
        return unavailable("no signal has been generated for this symbol")
    age_h = (_naive(now) - _naive(row.ts)).total_seconds() / 3600
    value = {
        "signal": row.signal.value if hasattr(row.signal, "value") else str(row.signal),
        "horizon": row.horizon.value if hasattr(row.horizon, "value") else str(row.horizon),
        "confidence": row.confidence,
        "ts": row.ts.isoformat(),
        "source": row.source,
        # NOT a probability of profit, and labelled so. The platform's own audits measured
        # confidence bands as flat, so presenting it as a forecast probability would be a claim
        # the evidence does not support.
        "confidence_note": "engine confidence score, not a calibrated probability",
    }
    if age_h > _MAX_SIGNAL_AGE_HOURS:
        return stale(value, f"latest signal is {age_h:.0f}h old")
    return Field(value=value, state=FieldState.OK, statement=StatementClass.MODEL_FORECAST,
                 evidence_ids=[f"signal:{row.id}"])


def next_earnings_event(session, stock_id: int, today) -> Field:
    row = session.execute(
        select(EarningsEvent).where(EarningsEvent.stock_id == stock_id,
                                    EarningsEvent.report_date >= today)
        .order_by(EarningsEvent.report_date.asc()).limit(1)).scalars().first()
    if row is None:
        return unavailable("no scheduled earnings event on file")
    return observed({"report_date": row.report_date.isoformat(),
                     "sessions_away": None,
                     "period_label_state": "UNKNOWN"},
                    evidence_ids=[f"earnings_event:{row.id}"])


def fiscal_period(event: EarningsEvent) -> Field:
    """ALWAYS UNKNOWN from this source, and that is the correct answer.

    `EarningsEvent.fiscal_quarter` / `.fiscal_year` are derived from the calendar month of the
    period-end date (`(month - 1) // 3 + 1`), which is wrong for every issuer whose fiscal year
    is not the calendar year — MU's Q4 is stored as "Q3". See
    docs/incidents/inferred-fiscal-period-mislabels-non-calendar-years.md.

    The templates are explicit: the fiscal period comes from source evidence, not from the
    release month, and must show unknown if uncertain. Reporting the stored value as fact would
    put a known-wrong label on the one field that identifies WHICH results these are.
    """
    return unknown(
        "fiscal period on file is inferred from the period-end calendar month, which is "
        "incorrect for non-calendar fiscal years; no source-confirmed period is stored",
        evidence_ids=[f"earnings_event:{event.id}"])


def earnings_actuals(event: EarningsEvent) -> dict[str, Field]:
    """Actuals and estimates as separate fields, with the basis problem stated.

    The templates forbid comparing GAAP actuals against adjusted estimates. This platform does
    not store the accounting basis of either number, so the comparison cannot be shown to be
    like-for-like — the surprise is still computed (it is what the provider reports) but carries
    that limitation rather than an implied guarantee.
    """
    out: dict[str, Field] = {}
    for name, est, act in (("eps", event.eps_estimate, event.eps_actual),
                           ("revenue", event.revenue_estimate, event.revenue_actual)):
        out[f"{name}_estimate"] = (observed(est, evidence_ids=[f"earnings_event:{event.id}"])
                                   if est is not None
                                   else unavailable(f"no {name} estimate on file"))
        out[f"{name}_actual"] = (observed(act, evidence_ids=[f"earnings_event:{event.id}"])
                                 if act is not None
                                 else unavailable(f"no {name} actual on file"))
        out[f"{name}_surprise_pct"] = surprise_pct(est, act, label=name)
    out["accounting_basis"] = unknown(
        "the platform does not store whether these are GAAP or adjusted figures; a GAAP actual "
        "and an adjusted estimate are not comparable, so this surprise may not be like-for-like")
    return out


def surprise_pct(estimate, actual, *, label: str) -> Field:
    """(actual - estimate) / abs(estimate), and NOT_APPLICABLE where that is meaningless.

    A near-zero estimate makes the percentage explode or invert sign — the templates call for
    reporting the absolute difference and a not-applicable reason instead of a number that reads
    as a 4000% beat. The absolute difference is always reported, because it is always meaningful.
    """
    if estimate is None or actual is None:
        return unavailable(f"{label}: needs both an estimate and an actual")
    diff = actual - estimate
    if abs(estimate) < 1e-9:
        from intelligence.report_contract import not_applicable
        return not_applicable(
            f"{label}: the estimate is zero, so a percentage surprise is undefined; the "
            f"absolute difference is {diff:+.4f}")
    return calculated({"pct": round(diff / abs(estimate) * 100.0, 2),
                       "absolute": round(diff, 4)}, units="pct")


def post_event_reaction(event: EarningsEvent) -> dict[str, Field]:
    out = {}
    for label, value, window in (("return_1d", event.post_earnings_return_1d, "1 session"),
                                 ("return_5d", event.post_earnings_return_5d, "5 sessions")):
        if value is None:
            out[label] = unknown(
                f"{window} after the release has not matured, or the outcome has not been "
                f"recorded yet")
        else:
            out[label] = observed(round(float(value), 4), units="pct",
                                  evidence_ids=[f"earnings_event:{event.id}"])
    return out


def breadth(session, market: str, today) -> Field:
    """Share of covered symbols above their own 20-session average.

    LABELLED AS COVERAGE-LIMITED, deliberately. The templates warn that this is participation
    among the symbols THIS PLATFORM INGESTS, not index breadth — the universe is a watchlist,
    not a constituent list, so calling it "market breadth" without that qualifier would
    overstate what was measured.
    """
    stock_ids = list(session.execute(
        select(Stock.id).where(Stock.active.is_(True), Stock.market == market,
                               Stock.delisted.is_(False))).scalars().all())
    if not stock_ids:
        return unavailable(f"no active {market} symbols in the universe")
    above = total = 0
    for sid in stock_ids:
        bars = daily_bars(session, sid, limit=21)
        if len(bars) < 21:
            continue
        closes = [float(b.close) for b in bars]
        total += 1
        if closes[0] > sum(closes[:20]) / 20:
            above += 1
    if total == 0:
        return unavailable(
            f"none of the {len(stock_ids)} active {market} symbols has 21 daily bars ingested")
    return calculated(
        {"above_sma20": above, "covered": total, "universe": len(stock_ids),
         "pct": round(above / total * 100.0, 1),
         "basis": "share of COVERED universe symbols above their own 20-session average; this "
                  "is participation within an ingested watchlist, not index constituent breadth"},
        units="pct")


def sector_leadership(session, market: str, sessions: int = 20) -> Field:
    """Sector-relative returns across the covered universe, ranked."""
    rows = list(session.execute(
        select(Stock.id, Stock.sector).where(
            Stock.active.is_(True), Stock.market == market,
            Stock.delisted.is_(False), Stock.sector.is_not(None))).all())
    if not rows:
        return unavailable(f"no active {market} symbols carry a sector classification")
    by_sector: dict[str, list[float]] = {}
    for sid, sector in rows:
        bars = daily_bars(session, sid, limit=sessions + 1)
        if len(bars) < sessions + 1 or not bars[sessions].close:
            continue
        pct = (float(bars[0].close) - float(bars[sessions].close)) / float(bars[sessions].close) * 100
        by_sector.setdefault(sector, []).append(pct)
    if not by_sector:
        return unavailable(
            f"no {market} sector has a symbol with {sessions + 1} daily bars ingested")
    ranked = sorted(((s, round(sum(v) / len(v), 2), len(v)) for s, v in by_sector.items()),
                    key=lambda r: r[1], reverse=True)
    return calculated(
        {"sessions": sessions,
         "ranked": [{"sector": s, "mean_return_pct": r, "symbols": n} for s, r, n in ranked],
         "basis": "equal-weighted mean of covered symbols per sector, not a sector index"},
        units="pct")
