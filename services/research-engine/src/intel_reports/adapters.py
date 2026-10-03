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
    Evidence, EvidenceBook, Field, FieldState, StatementClass,
    calculated, observed, stale, unavailable, unknown,
)

#: A daily bar older than this is not evidence about today's trend. CALENDAR days, which is
#: what the arithmetic below actually measures — an earlier version called this "sessions",
#: which it is not: counting stored rows cannot establish exchange sessions when bars are
#: missing, duplicated or (in a fixture) generated across a weekend.
_MAX_BAR_AGE_DAYS = 5
#: A signal older than this describes conditions that have since moved on.
_MAX_SIGNAL_AGE_HOURS = 36


def _naive(dt):
    if dt is None:
        return None
    return dt.astimezone(timezone.utc).replace(tzinfo=None) if dt.tzinfo else dt


def daily_bars(session, stock_id: int, limit: int = 70, *, cutoff: datetime) -> list[Price]:
    """Bars observable AT the report's cutoff. `cutoff` is required, not optional.

    THE LOOK-AHEAD HOLE THIS CLOSES. This used to select the latest bars unconditionally, so a
    report asked for a 25 September cutoff happily read the 2 October close — and the contract's
    timestamp fields did nothing about it, because preventing hindsight is an input-selection
    rule, not a schema. Making the parameter required means a new caller cannot omit it and
    silently get the old behaviour.
    """
    return list(session.execute(
        select(Price).where(Price.stock_id == stock_id, Price.timeframe == TimeFrame.D1,
                            Price.ts <= cutoff)
        .order_by(Price.ts.desc()).limit(limit)).scalars().all())


def record_bar(book: EvidenceBook, bar: Price) -> str:
    """Store the OBSERVATION, not just a pointer to a row that may later change."""
    return book.add(Evidence(
        evidence_id=f"price:{bar.stock_id}:{bar.ts:%Y-%m-%dT%H:%M}",
        source=f"prices:{bar.id}",
        value={"open": float(bar.open), "high": float(bar.high), "low": float(bar.low),
               "close": float(bar.close), "volume": float(bar.volume)},
        units="price", basis="unadjusted",
        observed_period=f"{bar.ts:%Y-%m-%d}",
        published_at=_naive(bar.ts), first_available_at=_naive(bar.ts),
        retrieved_at=_naive(datetime.now(timezone.utc))))


def session_return(bars: list[Price], sessions: int, *, label: str,
                   book: EvidenceBook | None = None) -> Field:
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
    ids = [record_bar(book, latest), record_bar(book, prior)] if book is not None else []
    return calculated(round(pct, 2), units="pct", evidence_ids=ids)


def price_as_of(bars: list[Price], now: datetime, *, book: EvidenceBook | None = None) -> Field:
    """The latest close, with its own age checked rather than assumed current."""
    if not bars:
        return unavailable("no daily bars ingested for this symbol")
    latest = bars[0]
    age_days = (_naive(now) - _naive(latest.ts)).days
    value = {"close": round(float(latest.close), 4), "ts": latest.ts.isoformat(),
             "basis": "unadjusted close", "session": "regular"}
    ids = [record_bar(book, latest)] if book is not None else []
    if age_days > _MAX_BAR_AGE_DAYS:
        return stale(value, f"latest daily bar is {age_days} calendar days old",
                     evidence_ids=ids)
    return observed(value, evidence_ids=ids)


def trend_structure(bars: list[Price], *, price_field: Field | None = None,
                    book: EvidenceBook | None = None) -> Field:
    """An OBSERVED description of the bars, deliberately not a forecast.

    The contract separates observed trend from forward outlook precisely so that this function
    cannot be read as a prediction: it says what the last N closes did, nothing about what
    happens next.
    """
    if len(bars) < 21:
        return unavailable(f"trend structure needs 21 daily bars, {len(bars)} available")
    # FRESHNESS PROPAGATES TO WHAT DEPENDS ON IT. These are the same bars `price_as_of` just
    # judged too old; describing a trend from them as current evidence while the price beside
    # it is flagged STALE lets a reader take the conclusion and miss the caveat.
    if price_field is not None and price_field.state is FieldState.STALE:
        return stale(None, f"derived from the same bars as the price, which is stale: "
                           f"{price_field.reason}")
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
        desc["sma50_note"] = "fewer than 50 daily bars stored; 50-bar average not computed"
    ids = [record_bar(book, b) for b in bars[:20]] if book is not None else []
    return calculated(desc, evidence_ids=ids)


def latest_signal(session, stock_id: int, now: datetime, *,
                  book: EvidenceBook | None = None) -> Field:
    """The SIGNAL ENGINE's latest row, reported as a separate source with its time.

    NOT a decision-engine verdict and not an eligibility assessment — it reads the `signals`
    table. The field was previously named for the decision engine, which implied an
    authoritative risk-checked judgement it never was.

    The templates require an existing assessment to be attributed and timestamped rather than
    folded into the report's own reasoning: it is another system's claim, not this report's
    observation, and its age matters.
    """
    row = session.execute(
        select(Signal).where(Signal.stock_id == stock_id, Signal.ts <= now)
        .order_by(Signal.ts.desc()).limit(1)).scalars().first()
    if row is None:
        return unavailable("no signal has been generated for this symbol")
    age_h = (_naive(now) - _naive(row.ts)).total_seconds() / 3600
    value = {
        "signal": row.signal.value if hasattr(row.signal, "value") else str(row.signal),
        "horizon": row.horizon.value if hasattr(row.horizon, "value") else str(row.horizon),
        "confidence": row.confidence,
        "ts": row.ts.isoformat(),
        "source": f"signals table, written by {row.source}",
        # NOT a probability of profit, and labelled so. The platform's own audits measured
        # confidence bands as flat, so presenting it as a forecast probability would be a claim
        # the evidence does not support.
        "confidence_note": "engine confidence score, not a calibrated probability",
    }
    ids = []
    if book is not None:
        ids = [book.add(Evidence(
            evidence_id=f"signal:{row.id}", source=f"signals:{row.id}", value=value,
            observed_period=f"{row.ts:%Y-%m-%d}", published_at=_naive(row.ts),
            first_available_at=_naive(row.ts),
            retrieved_at=_naive(datetime.now(timezone.utc))))]
    if age_h > _MAX_SIGNAL_AGE_HOURS:
        return stale(value, f"latest signal is {age_h:.0f}h old", evidence_ids=ids)
    return Field(value=value, state=FieldState.OK, statement=StatementClass.MODEL_FORECAST,
                 evidence_ids=ids)


def next_earnings_event(session, stock_id: int, today, *,
                        book: EvidenceBook | None = None) -> Field:
    row = session.execute(
        select(EarningsEvent).where(EarningsEvent.stock_id == stock_id,
                                    EarningsEvent.report_date >= today)
        .order_by(EarningsEvent.report_date.asc()).limit(1)).scalars().first()
    if row is None:
        return unavailable("no scheduled earnings event on file")
    ids = []
    if book is not None:
        ids = [book.add(Evidence(
            evidence_id=f"earnings_event:{row.id}", source=f"earnings_events:{row.id}",
            value={"report_date": row.report_date.isoformat()},
            observed_period=row.report_date.isoformat(),
            retrieved_at=_naive(datetime.now(timezone.utc))))]
    return observed({"report_date": row.report_date.isoformat(),
                     "calendar_days_away": (row.report_date - today).days,
                     "release_time_state": "UNKNOWN"}, evidence_ids=ids)


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


def earnings_actuals(event: EarningsEvent, *, frozen_eps=None, frozen_revenue=None,
                     frozen_from=None) -> dict[str, Field]:
    """Actuals against the expectation that was FROZEN, with the later revision shown separately.

    WHY THE FROZEN NUMBER DRIVES THE TABLE. `event.eps_estimate` is mutable: the provider
    revises it, including after the release. Using it here while the verdict used the frozen one
    put two different expectations in the same report — a measured -11.79% "miss" beside a
    verdict of "above", from the same actual. One expectation drives both, and the revision is
    reported in its own field rather than quietly replacing the baseline.

    The accounting basis is still unknown, and a surprise computed across an unverified
    GAAP/adjusted boundary is NOT a validated beat or miss. That caveat sits on the comparison
    itself, not only in a footnote elsewhere.
    """
    out: dict[str, Field] = {}
    frozen = {"eps": frozen_eps, "revenue": frozen_revenue}
    for name, current_est, act in (("eps", event.eps_estimate, event.eps_actual),
                                   ("revenue", event.revenue_estimate, event.revenue_actual)):
        baseline = frozen[name]
        used_frozen = baseline is not None
        expectation = baseline if used_frozen else current_est

        if expectation is None:
            out[f"{name}_expectation"] = unavailable(
                f"no {name} expectation: none was frozen before the release and none is on file")
        else:
            out[f"{name}_expectation"] = observed(
                {"value": expectation,
                 "source": ("frozen pre-release baseline"
                            f" (report {getattr(frozen_from, 'id', '?')})" if used_frozen
                            else "current stored estimate — NOT a frozen baseline, so this "
                                 "comparison is not protected against later revision"),
                 "is_frozen": used_frozen},
                evidence_ids=[f"earnings_event:{event.id}"] if not used_frozen else [])

        out[f"{name}_actual"] = (observed(act, evidence_ids=[f"earnings_event:{event.id}"])
                                 if act is not None
                                 else unavailable(f"no {name} actual on file"))
        out[f"{name}_surprise_pct"] = surprise_pct(expectation, act, label=name)

        # The revision is real information; it is shown, dated by its own source, and kept out
        # of the comparison.
        if used_frozen and current_est is not None and current_est != baseline:
            out[f"{name}_estimate_revised_since"] = observed(
                {"frozen": baseline, "current_on_file": current_est,
                 "note": "the stored estimate changed after the baseline was frozen; the "
                         "comparison above deliberately still uses the frozen figure"},
                evidence_ids=[f"earnings_event:{event.id}"])

    out["accounting_basis"] = unknown(
        "the platform does not store whether these are GAAP or adjusted figures; a GAAP actual "
        "and an adjusted estimate are not comparable, so the surprises above are factual "
        "differences and NOT validated beats or misses")
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


def record_event(book: EvidenceBook, event: EarningsEvent) -> str:
    return book.add(Evidence(
        evidence_id=f"earnings_event:{event.id}", source=f"earnings_events:{event.id}",
        value={"report_date": event.report_date.isoformat(),
               "eps_estimate": event.eps_estimate, "eps_actual": event.eps_actual,
               "revenue_estimate": event.revenue_estimate,
               "revenue_actual": event.revenue_actual},
        observed_period=event.report_date.isoformat(),
        retrieved_at=_naive(datetime.now(timezone.utc)),
        note="estimates on this row are mutable and may be revised after the release"))


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


def breadth(session, market: str, today, *, cutoff: datetime) -> Field:
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
        bars = daily_bars(session, sid, limit=21, cutoff=cutoff)
        if len(bars) < 21:
            continue
        closes = [float(b.close) for b in bars]
        total += 1
        if closes[0] > sum(closes[:20]) / 20:
            above += 1
    if total == 0:
        return unavailable(
            f"none of the {len(stock_ids)} active {market} symbols has 21 daily bars ingested")
    # BOTH DENOMINATORS, because they answer different questions and sharing one "pct" invites
    # the wrong reading: participation is above/covered, while coverage is covered/universe.
    return calculated(
        {"above_sma20": above, "covered": total, "universe": len(stock_ids),
         "participation_pct": round(above / total * 100.0, 1),
         "coverage_pct": round(total / len(stock_ids) * 100.0, 1),
         "basis": "participation = share of COVERED symbols above their own 20-bar average; "
                  "coverage = share of the active universe with enough bars to judge. This is "
                  "an ingested watchlist, not an index constituent list."},
        units="pct")


def sector_leadership(session, market: str, sessions: int = 20, *, cutoff: datetime) -> Field:
    """Sector-relative returns across the covered universe, ranked."""
    rows = list(session.execute(
        select(Stock.id, Stock.sector).where(
            Stock.active.is_(True), Stock.market == market,
            Stock.delisted.is_(False), Stock.sector.is_not(None))).all())
    if not rows:
        return unavailable(f"no active {market} symbols carry a sector classification")
    by_sector: dict[str, list[float]] = {}
    for sid, sector in rows:
        bars = daily_bars(session, sid, limit=sessions + 1, cutoff=cutoff)
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
