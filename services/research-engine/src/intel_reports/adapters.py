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

from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import select, func, text

from db import Price, Signal, Stock, EarningsEvent
from db.models import TimeFrame
from common.market_calendar import calendar_coverage, session_bounds
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


def daily_bars(session, stock_id: int, limit: int = 70, *, cutoff: datetime,
               through: date | None = None) -> list[Price]:
    """Bars observable AT the report's cutoff. `cutoff` is required, not optional.

    THE LOOK-AHEAD HOLE THIS CLOSES. This used to select the latest bars unconditionally, so a
    report asked for a 25 September cutoff happily read the 2 October close — and the contract's
    timestamp fields did nothing about it, because preventing hindsight is an input-selection
    rule, not a schema. Making the parameter required means a new caller cannot omit it and
    silently get the old behaviour.

    `through` (from `session_window`) additionally drops rows of sessions that are not
    completed and verified — a forming daily row is not a close (audit F01).
    """
    q = select(Price).where(Price.stock_id == stock_id, Price.timeframe == TimeFrame.D1,
                            Price.ts <= cutoff)
    if through is not None:
        q = q.where(Price.ts < datetime.combine(through + timedelta(days=1), time()))
    return list(session.execute(q.order_by(Price.ts.desc()).limit(limit)).scalars().all())


# ── Completed, VERIFIED exchange sessions (audit F01/F03, 2026-10-21) ─────────────
# A stored D1 row is keyed by its session date at midnight and is REWRITTEN during that session
# by the intraday refresh jobs, so `ts <= now` admits a forming bar as "the latest daily close"
# (measured in production: HK rows dated the current day at 13:00 HKT).
#
# A session's row counts as final only when the scheduler's post-close ingest for that market
# (`us_post_close` / `hk_post_close`, 16:30 local) is RECORDED as having succeeded after that
# session's 16:30 start — the clock passing 16:30 is not evidence. The record is the
# `scheduler:job:<name>` Redis key the scheduler writes on completion (latest run only).
#
# What this does NOT establish: that every symbol's fetch succeeded inside an "ok" batch
# (ingest_universe logs per-symbol failures and still reports ok) — no per-row write time is
# stored. Only the LATEST expected session needs the record: each daily ingest re-fetches from
# six days before the last stored bar, so a later successful run finalises earlier sessions.
POST_CLOSE_INGEST_LOCAL = time(16, 30)
#: The post-close ingest is EXPECTED to have completed by 16:45 local (it took 2-3 minutes on
#: 2026-10-20). Before then the previous session is the latest one expected, so a plan built at
#: 16:10 is not marked stale merely because the ingest has not run yet.
POST_CLOSE_SETTLE = timedelta(minutes=15)
#: Verification applies to live requests; a historical cutoff is not judged by today's record.
LIVE_WINDOW = timedelta(hours=36)
_TZ = {"US": ZoneInfo("America/New_York"), "HK": ZoneInfo("Asia/Hong_Kong")}
_POST_CLOSE_JOB = {"US": "us_post_close", "HK": "hk_post_close"}
_UNSET = object()


def _aware(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _real_now() -> datetime:
    return datetime.now(timezone.utc)


def ingest_started_at(market: str, d: date) -> datetime:
    return datetime.combine(d, POST_CLOSE_INGEST_LOCAL, tzinfo=_TZ[market]).astimezone(timezone.utc)


def session_settled_at(market: str, d: date) -> datetime | None:
    """UTC instant by which `d`'s post-close ingest is EXPECTED to be done, or None."""
    bounds = session_bounds(market, d)
    if bounds is None:
        return None
    return max(bounds[1].astimezone(timezone.utc), ingest_started_at(market, d)) + POST_CLOSE_SETTLE


def last_completed_session(market: str, now: datetime) -> date | None:
    """Latest session expected to be complete at `now`. None = cannot be determined."""
    if market not in _TZ:
        return None
    aware = _aware(now)
    d = aware.astimezone(_TZ[market]).date()
    if d.year > calendar_coverage()["hkex" if market == "HK" else "nyse"]:
        return None
    for _ in range(15):
        settled = session_settled_at(market, d)
        if settled is not None and aware >= settled:
            return d
        d -= timedelta(days=1)
    return None


def _previous_session(market: str, d: date) -> date | None:
    for _ in range(15):
        d -= timedelta(days=1)
        if session_bounds(market, d) is not None:
            return d
    return None


def sessions_after(market: str, start: date, end: date) -> int:
    """Number of sessions in (start, end]."""
    n, d = 0, start + timedelta(days=1)
    while d <= end:
        n += session_bounds(market, d) is not None
        d += timedelta(days=1)
    return n


def read_post_close_record(market: str) -> dict:
    """The scheduler's latest post-close ingest record: status ok|error|missing|unreadable."""
    import json
    try:
        from common.redis_client import get_redis
        raw = get_redis().get(f"scheduler:job:{_POST_CLOSE_JOB[market]}")
    except Exception as exc:                     # noqa: BLE001 — unverifiable, not "ok"
        return {"status": "unreadable", "error": str(exc)[:160]}
    if not raw:
        return {"status": "missing"}
    try:
        rec = json.loads(raw)
        return {"status": rec.get("status"), "error": rec.get("error"),
                "last_run": _aware(datetime.fromisoformat(rec["last_run"]))}
    except Exception as exc:                     # noqa: BLE001
        return {"status": "unreadable", "error": f"malformed record: {exc}"[:160]}


def session_window(market: str, ref: datetime, *, record=_UNSET) -> dict:
    """Which daily rows are usable at `ref`, and whether the latest session is verified.

    expected_session: the latest session whose post-close ingest should be done by `ref`.
    usable_through:   the latest session whose row may be read as a close.
    unverified:       why expected_session's row is not usable (None when it is).
    """
    expected = last_completed_session(market, ref)
    if expected is None:
        return {"market": market, "expected_session": None, "usable_through": None,
                "reason": f"no exchange calendar covers {market} at this time, so completed "
                          f"sessions cannot be determined"}
    out = {"market": market, "expected_session": expected, "usable_through": expected,
           "unverified": None, "verification": None}
    if abs(_real_now() - _aware(ref)) > LIVE_WINDOW:
        out["verification"] = ("historical cutoff: the latest ingest record is not evidence "
                               "about it; a later daily ingest re-fetches the prior six days")
        return out
    rec = read_post_close_record(market) if record is _UNSET else record
    job = _POST_CLOSE_JOB[market]
    started = ingest_started_at(market, expected)
    if rec.get("status") == "ok" and rec.get("last_run") is not None and rec["last_run"] >= started:
        out["verification"] = f"{job} recorded ok at {rec['last_run']:%Y-%m-%d %H:%M} UTC"
        return out
    if rec.get("status") == "ok":
        why = f"the last recorded {job} run ({rec['last_run']:%Y-%m-%d %H:%M} UTC) predates this session's"
    elif rec.get("status") == "error":
        why = f"the last {job} run failed ({rec.get('error') or 'no detail'})"
    elif rec.get("status") == "missing":
        why = f"no {job} completion is recorded"
    else:
        why = f"the {job} record could not be read ({rec.get('error') or rec.get('status')})"
    out["usable_through"] = _previous_session(market, expected)
    out["unverified"] = (f"the {expected.isoformat()} close is not verified as ingested: {why}; "
                         f"the previous session's close is shown instead")
    return out


def read_symbol_fetch(session, stock_id: int) -> dict | None:
    """market-data's per-symbol daily fetch evidence (`daily_ingest_records`), or None."""
    try:
        with session.begin_nested():          # a missing table must not poison the transaction
            row = session.execute(text(
                "SELECT last_success_at, last_observed_session, last_provider, last_failure_at, "
                "last_error FROM daily_ingest_records WHERE stock_id = :i"), {"i": stock_id}).first()
    except Exception:                          # noqa: BLE001 — absent evidence, not a failure
        return None
    if row is None:
        return None
    return dict(zip(("last_success_at", "last_observed_session", "last_provider",
                     "last_failure_at", "last_error"), row))


def _symbol_verification(market: str, window: dict, fetch: dict | None) -> dict:
    """Narrow a batch-verified window to what THIS symbol's own fetch evidence supports."""
    expected = window["expected_session"]
    if fetch is None:
        window["verification"] = (f"batch only — {window['verification']}; no per-symbol fetch "
                                  f"record exists yet, so this symbol's close is not verified")
        window["per_symbol"] = "missing"
        return window
    ok_at, seen = fetch["last_success_at"], fetch["last_observed_session"]
    seen_d = seen.date() if hasattr(seen, "date") else seen
    started = ingest_started_at(market, expected).replace(tzinfo=None)
    if ok_at is not None and _naive(ok_at) >= started and seen_d is not None and seen_d >= expected:
        window["verification"] = (f"this symbol fetched ok at {_naive(ok_at):%Y-%m-%d %H:%M} UTC "
                                  f"({fetch['last_provider']}, newest session {seen_d})")
        window["per_symbol"] = "verified"
        return window
    why = (f"its last successful fetch ({_naive(ok_at):%Y-%m-%d %H:%M} UTC, newest session {seen_d}) "
           f"does not cover it" if ok_at is not None else "it has no successful fetch on record")
    if fetch.get("last_failure_at") and (ok_at is None or _naive(fetch["last_failure_at"]) > _naive(ok_at)):
        why += f"; the latest fetch failed ({fetch.get('last_error') or 'no detail'})"
    window["usable_through"] = _previous_session(market, expected)
    window["unverified"] = (f"the {expected.isoformat()} close is not verified for this symbol: "
                            f"{why}; the previous session's close is shown instead")
    window["per_symbol"] = "not_covered"
    return window


def completed_daily_bars(session, stock, limit: int, *, now: datetime,
                         record=_UNSET, fetch=_UNSET) -> tuple[list[Price], dict]:
    """Daily bars of COMPLETED, verified sessions only, plus the window they are judged by."""
    market = getattr(stock.market, "value", stock.market)
    window = session_window(market, now, record=record)
    if (window.get("expected_session") is not None and not window.get("unverified")
            and window.get("verification") and not window["verification"].startswith("historical")):
        window = _symbol_verification(market, window, read_symbol_fetch(session, stock.id)
                                      if fetch is _UNSET else fetch)
    if window["expected_session"] is None or window["usable_through"] is None:
        return [], {**window, "expected_session": None,
                    "reason": window.get("reason") or "no usable completed session"}
    return daily_bars(session, stock.id, limit, cutoff=_naive(now),
                      through=window["usable_through"]), window


def record_bar(book: EvidenceBook, bar: Price) -> str:
    """Store the OBSERVATION, not just a pointer to a row that may later change.

    `first_available_at` IS DELIBERATELY UNKNOWN. A daily bar timestamped at midnight does not
    establish that its CLOSING price was knowable at midnight — the close is not known until the
    session ends, and a revised or backfilled row cannot acquire historical availability merely
    from the date it describes. This platform does not store an ingestion or publication time
    for price rows, so the honest value is absent, with the limitation recorded on the evidence
    itself rather than implied by an observation date standing in for it.

    The consequence is deliberate: anything that needs point-in-time availability — a backtest,
    or pre-event accountability — must treat these inputs as unproven rather than assume the
    missing timestamp was measured.
    """
    return book.add(Evidence(
        evidence_id=f"price:{bar.stock_id}:{bar.ts:%Y-%m-%dT%H:%M}",
        source=f"prices:{bar.id}",
        value={"open": float(bar.open), "high": float(bar.high), "low": float(bar.low),
               "close": float(bar.close), "volume": float(bar.volume)},
        units="price", basis="unadjusted",
        observed_period=f"{bar.ts:%Y-%m-%d}",
        published_at=None,
        first_available_at=None,
        retrieved_at=_naive(datetime.now(timezone.utc)),
        state=FieldState.UNKNOWN,
        note="observation date only; no ingestion or publication time is stored, so the instant "
             "this close became knowable is not established and point-in-time availability "
             "cannot be claimed from this record"))


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


def price_as_of(bars: list[Price], now: datetime, *, book: EvidenceBook | None = None,
                sessions: dict | None = None) -> Field:
    """The latest close, with its own age checked rather than assumed current.

    With `sessions` (from `completed_daily_bars`) freshness is judged in completed exchange
    sessions: the latest bar must BE the latest completed session. Calendar-day age (the
    generic report rule) let a four-session-old close read as current (audit F03).
    """
    if sessions is not None and sessions.get("expected_session") is None:
        return unavailable(sessions["reason"])
    if not bars:
        return unavailable("no daily bars ingested for this symbol")
    latest = bars[0]
    age_days = (_naive(now) - _naive(latest.ts)).days
    value = {"close": round(float(latest.close), 4), "ts": latest.ts.isoformat(),
             "basis": "unadjusted close", "session": "regular"}
    ids = [record_bar(book, latest)] if book is not None else []
    if sessions is not None:
        expected, have = sessions["expected_session"], _naive(latest.ts).date()
        value["expected_session"] = expected.isoformat()
        if sessions.get("verification"):
            value["ingest_verification"] = sessions["verification"]
        if sessions.get("unverified"):
            return stale(value, sessions["unverified"], evidence_ids=ids)
        missing = sessions_after(sessions["market"], have, expected)
        if missing:
            return stale(value, f"the latest stored daily close is for {have.isoformat()}, but "
                                f"{missing} later completed session(s) exist (latest "
                                f"{expected.isoformat()}); the data has not caught up",
                         evidence_ids=ids)
        return observed(value, evidence_ids=ids)
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
        # signal-engine writes every horizon with the same timestamp; without a tie-break the
        # database may return any of them, so copilot/report fingerprints flipped between calls
        # (409 "plan changed" on the Game Plan's LLM alternative, 2026-10-21). Newest id wins.
        .order_by(Signal.ts.desc(), Signal.id.desc()).limit(1)).scalars().first()
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


#: THE REPORT'S ONE RETURN UNIT IS PERCENT, and the conversion happens here, at the single
#: boundary where a fractional source is read.
#:
#: THE DEFECT THIS FIXES. `_compute_post_earnings_returns` computes `price / baseline - 1` — a
#: FRACTION — while its own docstring calls it "the % change". Those values were emitted with
#: `units="pct"` and no conversion, so MU's stored 0.15382 rendered as "0.1538 pct" when it
#: means +15.38%, and -0.01853 as "-0.0185 pct" when it means -1.85%. A hundredfold error in
#: the most quotable number a report carries.
#:
#: Every other return in these reports (`session_return`) is already percent, so converting
#: here makes one unit true everywhere and leaves the renderers with no unit logic to get wrong.
def fraction_to_pct(value: float) -> float:
    """0.15382 -> 15.38. The ONE place a stored fractional return becomes a reported percent."""
    return round(float(value) * 100.0, 2)


def reaction_window_dates(session, stock_id: int, report_date) -> dict:
    """The ACTUAL baseline and endpoint bars the stored returns were computed from.

    WHY EXACT DATES AND NOT A SPAN LABEL. The producer takes the last close STRICTLY BEFORE the
    report date as its baseline, and `after[1]` / `after[5]` as endpoints — where `after`
    INCLUDES the report-date bar. So for a release on a trading day, `return_1d` spans the
    baseline to the day AFTER the report date: TWO close-to-close intervals, not one. Labelling
    it "1 session" understates the window it measures, and no span label is right for every
    case anyway, because a release on a non-trading date shifts both ends.

    The legacy values are preserved exactly; what changes is that the report now names the two
    dates the number was computed between, so a reader can check it instead of inferring a
    window from a word.
    """
    bars = list(session.execute(
        select(Price.ts, Price.close).where(Price.stock_id == stock_id,
                                            Price.timeframe == TimeFrame.D1)
        .order_by(Price.ts.asc())).all())
    before = [b for b in bars if b[0].date() < report_date]
    after = [b for b in bars if b[0].date() >= report_date]
    out = {"baseline_date": before[-1][0].date().isoformat() if before else None,
           "report_date": report_date.isoformat()}
    for key, idx in (("endpoint_1d", 1), ("endpoint_5d", 5)):
        out[key] = after[idx][0].date().isoformat() if len(after) > idx else None
    # How many close-to-close intervals the baseline->endpoint span actually covers.
    if before and len(after) > 1:
        out["intervals_1d"] = len([b for b in bars
                                   if before[-1][0] < b[0] <= after[1][0]])
    if before and len(after) > 5:
        out["intervals_5d"] = len([b for b in bars
                                   if before[-1][0] < b[0] <= after[5][0]])
    return out


def announcement_date_is_verified(event: EarningsEvent) -> bool:
    """Whether `report_date` is an announcement date at all.

    `substituted_period_end` means it is the fiscal PERIOD END standing in — weeks earlier than
    the release. Anything anchored on it (a reaction window, an event age, a "not yet matured"
    verdict) is measuring the wrong dates, so the honest answer downstream is "cannot determine",
    not a number.
    """
    return getattr(event, "report_date_source", None) != "substituted_period_end"


def post_event_reaction(event: EarningsEvent, *, window_dates: dict | None = None
                        ) -> dict[str, Field]:
    wd = window_dates or {}
    out = {}
    if not announcement_date_is_verified(event):
        # NOT "not yet matured" — that says the window is still running and an outcome is
        # coming. Nothing is coming: there is no verified date to measure from, which is a
        # different state and needs a different answer.
        reason = (
            "cannot determine: the announcement date is unverified. The stored date is the "
            f"fiscal PERIOD END ({event.period_end or event.report_date}) standing in, and the "
            "release followed it by an unknown interval — so no reaction window can be placed. "
            "This is not an outcome awaiting maturity.")
        return {"return_1d": unknown(reason), "return_5d": unknown(reason)}
    for label, value, key, idx in (("return_1d", event.post_earnings_return_1d, "endpoint_1d", 1),
                                   ("return_5d", event.post_earnings_return_5d, "endpoint_5d", 5)):
        if value is None:
            out[label] = unknown(
                f"the endpoint {idx} trading day(s) after the report date has not matured, or "
                f"the outcome has not been recorded yet")
            continue
        baseline, endpoint = wd.get("baseline_date"), wd.get(key)
        intervals = wd.get(f"intervals_{label.split('_')[1]}")
        if baseline and endpoint:
            window = f"{baseline} close to {endpoint} close"
            basis = (f"{intervals} close-to-close interval(s) spanning the stored report date "
                     f"{wd.get('report_date')}" if intervals else
                     f"spanning the stored report date {wd.get('report_date')}")
        else:
            window = "UNRESOLVED"
            basis = ("the bars this return was computed from are not on file, so its exact "
                     "baseline and endpoint cannot be named")
        out[label] = observed(
            {"pct": fraction_to_pct(value), "window": window, "basis": basis,
             "note": "baseline is the last close BEFORE the report date and the endpoint is "
                     "indexed from the report date's own bar, so this is not normalised to the "
                     "announcement instant, which is not on file"},
            units="pct", evidence_ids=[f"earnings_event:{event.id}"])
    return out


def _population_fingerprint(stock_ids) -> str:
    """A stable identity for a set of covered symbols, so two readings can be shown comparable."""
    import hashlib
    joined = ",".join(str(i) for i in sorted(stock_ids))
    return f"sha256-pop:{hashlib.sha256(joined.encode()).hexdigest()[:16]}"


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
    covered_ids = []
    through = session_window(getattr(market, "value", market), cutoff)["usable_through"]
    for sid in stock_ids:
        bars = daily_bars(session, sid, limit=21, cutoff=cutoff, through=through)
        if len(bars) < 21:
            continue
        closes = [float(b.close) for b in bars]
        total += 1
        covered_ids.append(sid)
        if closes[0] > sum(closes[:20]) / 20:
            above += 1
    if total == 0:
        return unavailable(
            f"none of the {len(stock_ids)} active {market} symbols has 21 daily bars ingested")
    # BOTH DENOMINATORS, because they answer different questions and sharing one "pct" invites
    # the wrong reading: participation is above/covered, while coverage is covered/universe.
    return calculated(
        {"above_sma20": above, "covered": total, "universe": len(stock_ids),
         # WHICH SYMBOLS, not just how many. Two readings with the same count can cover
         # different companies — one symbol dropping out as another gains its 21st bar leaves
         # the denominator unchanged while the population is not the same, and a difference
         # between them is then partly a composition change rather than a market one.
         "population_fingerprint": _population_fingerprint(covered_ids),
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
    through = session_window(getattr(market, "value", market), cutoff)["usable_through"]
    for sid, sector in rows:
        bars = daily_bars(session, sid, limit=sessions + 1, cutoff=cutoff, through=through)
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


def peer_basket(session, stock, sessions: int = 20, *, cutoff: datetime) -> Field:
    """The subject's own INDUSTRY peers, with the basket's membership fully disclosed.

    WHY THE SECTOR MEAN WAS NOT THIS. MU was compared against 59 covered "Technology" symbols —
    a sector that holds software, hardware and services alongside semiconductors — and the
    basket INCLUDED MU ITSELF, so the stock was partly being compared with its own return. Both
    facts were invisible in the output.

    This returns the narrower industry basket, EXCLUDES the subject, and states membership,
    weighting and window, because a relative figure whose basket is unknown is not checkable.
    The sector figure stays available as broader context and is labelled as such.
    """
    industry = getattr(stock, "industry", None)
    if not industry:
        return unavailable(
            "no industry classification is stored for this issuer, so an industry peer basket "
            "cannot be assembled; the broader sector mean is the only comparison available")
    rows = list(session.execute(
        select(Stock.id, Stock.symbol).where(
            Stock.active.is_(True), Stock.market == stock.market,
            Stock.delisted.is_(False), Stock.industry == industry,
            Stock.id != stock.id)).all())
    if not rows:
        return unavailable(
            f"no other covered symbol carries the industry {industry!r}, so no peer basket "
            f"can be formed. A one-member basket is the subject itself.")

    members, returns = [], []
    through = session_window(getattr(stock.market, "value", stock.market), cutoff)["usable_through"]
    for sid, sym in rows:
        bars = daily_bars(session, sid, limit=sessions + 1, cutoff=cutoff, through=through)
        if len(bars) < sessions + 1 or not bars[sessions].close:
            continue
        base = float(bars[sessions].close)
        members.append(sym)
        returns.append((float(bars[0].close) - base) / base * 100)
    if not returns:
        return unavailable(
            f"no {industry} peer has {sessions + 1} daily bars ingested at this cutoff")
    return calculated(
        {"industry": industry,
         "sessions": sessions,
         "mean_return_pct": round(sum(returns) / len(returns), 2),
         "members": sorted(members),
         "member_count": len(members),
         "subject_included": False,
         "weighting": "equal-weighted",
         "basis": (f"equal-weighted mean of {len(members)} covered {industry} symbols over "
                   f"{sessions} sessions on unadjusted closes. The subject is EXCLUDED. This is "
                   f"an ingested watchlist, NOT an industry index, and is not "
                   f"capitalisation-weighted."),
         "price_convention": "unadjusted close"},
        units="pct")


#: FRED series behind each rate reading. The NAME is part of the evidence: "rates rose" is not
#: checkable, "DGS10 rose 5bp between two named observation dates" is.
_RATE_SERIES = {
    "yield_2y": ("DGS2", "2-year", "US Treasury constant-maturity yield", "pct"),
    "yield_10y": ("DGS10", "10-year", "US Treasury constant-maturity yield", "pct"),
    "yield_curve_2s10s": ("T10Y2Y", "10y minus 2y", "US Treasury term spread", "pct"),
    "hy_spread": ("BAMLH0A0HYM2", "n/a", "ICE BofA US high-yield option-adjusted spread",
                  "pct"),
    "dxy": ("DTWEXBGS", "n/a", "broad trade-weighted US dollar index", "index"),
}


def rates(session, *, cutoff: datetime, lookback_days: int = 30) -> Field:
    """Market-priced rates, each with its instrument, tenor, observation time and change.

    The QUERY lives here; the reading logic lives in `rate_readings`, which touches no ORM and
    can therefore be tested directly. The service conftest stubs `db` as a plain module, so
    anything importing `db.models` cannot be imported from a test at all — a rule this codebase
    has now learned three times.
    """
    from db import CrossAssetReading
    from .rate_readings import build_readings

    rows = list(session.execute(
        select(CrossAssetReading)
        .where(CrossAssetReading.as_of <= cutoff.date(),
               CrossAssetReading.as_of >= (cutoff.date() - timedelta(days=lookback_days)),
               # AVAILABILITY, NOT JUST OBSERVATION. An observation date before the cutoff does
               # not mean the value existed then — FRED publishes a day's yield the following
               # morning. Retrieval time is the only availability evidence stored, so it bounds
               # the query too.
               CrossAssetReading.fetched_at <= cutoff)
        .order_by(CrossAssetReading.as_of.desc())).scalars().all())
    if not rows:
        return unavailable(
            f"no cross-asset reading is stored within {lookback_days} days of this cutoff")
    return calculated(build_readings(rows, lookback_days=lookback_days,
                                     cutoff=cutoff))


def business_performance(session, stock, *, cutoff: datetime, periods: int = 5) -> Field:
    """Multi-period revenue, margins, cash generation and leverage from filed statements.

    AUDITED BEFORE BUILT (docs/audits/2026-10-06-fundamentals-data-audit.md). The platform
    already holds 1,424 statement rows over 156 symbols with 86-98% field completeness, so this
    needs no new ingestion. It also found three limits that are reported rather than smoothed
    over:

      * THE NEWEST ROW IS NOT THE NEWEST REALITY, so the age of the data travels with it. Two
        separate ages, measured against production on 2026-10-06 and NOT the same claim:
        RETRIEVAL is uniformly one month old — `max(fetched_at)` is 2026-09-07 across all 1,424
        rows — while the REPORTED-YEAR gap is narrow: 3 of 156 symbols have no annual within
        400 days (9992.HK, ZS, MU), and all three have non-calendar fiscal years whose latest
        year was reported after that last fetch. CORRECTION TO AN EARLIER FRAMING OF MINE: I
        generalised MU's missing year into "the table is stale". For the 153 calendar-year
        filers the stored series is current; what is uniformly old is when it was fetched.
      * NO SHARE COUNT EXISTS, so dilution is not computed. It is NOT derived from net income
        and EPS, which would silently assume the EPS basis.
      * NO ACCOUNTING BASIS AND NO FILING DATE are stored. These are reported history; they
        cannot support a point-in-time claim, and comparing them with an issuer-release figure
        crosses an unverified basis boundary. THE SAME GAP REACHES INSIDE THE SERIES: a growth
        rate or a margin change is a comparison of two stored rows, and without a basis for
        either row there is no evidence they were prepared on the same one. A basis change
        between two stored years moves both figures with no change in the business. So every
        derived change here is labelled a calculation from the stored provider series with
        comparability unverified — not a reported growth rate.

      * THE PERIOD LABEL IS THE PROVIDER'S, NOT A CONFIRMED FISCAL END. `period_end` is what the
        provider returned. No issuer filing or SEC submission has been read to confirm that the
        issuer's fiscal year ended on that date, so the label travels as a provider label and
        the section says "latest stored year", never "latest reported year" — what the issuer
        has reported is exactly what this table cannot see.
    """
    from db import FinancialStatement
    rows = list(session.execute(
        select(FinancialStatement)
        .where(FinancialStatement.symbol == stock.symbol,
               FinancialStatement.period_type == "annual",
               FinancialStatement.period_end <= cutoff.date())
        .order_by(FinancialStatement.period_end.desc())
        .limit(periods)).scalars().all())
    if len(rows) < 2:
        return unavailable(
            f"fewer than two annual statements are stored for {stock.symbol}, so no change "
            f"over comparable periods can be measured")

    def _pct(now, before):
        if now is None or before is None or not before:
            return None
        return round((now - before) / abs(before) * 100.0, 1)

    def _margin(num, den):
        if num is None or den is None or not den:
            return None
        return round(num / den * 100.0, 1)

    out = []
    for i, r in enumerate(rows):
        prev = rows[i + 1] if i + 1 < len(rows) else None
        fcf = r.free_cashflow
        if fcf is None and r.operating_cashflow is not None and r.capital_expenditure is not None:
            fcf = r.operating_cashflow + r.capital_expenditure   # capex is stored negative
        out.append({
            "period_end": r.period_end.isoformat(),
            "revenue": r.total_revenue,
            "revenue_growth_pct": _pct(r.total_revenue, prev.total_revenue) if prev else None,
            "gross_margin_pct": _margin(r.gross_profit, r.total_revenue),
            "operating_margin_pct": _margin(r.operating_income, r.total_revenue),
            "operating_cashflow": r.operating_cashflow,
            "capital_expenditure": r.capital_expenditure,
            "free_cashflow": fcf,
            "net_debt": (None if r.total_debt is None or r.cash_and_equivalents is None
                         else r.total_debt - r.cash_and_equivalents),
            "total_equity": r.total_equity,
        })

    newest = rows[0]
    age_days = (cutoff.date() - newest.period_end).days
    fetch_age_days = ((cutoff - newest.fetched_at).days
                      if newest.fetched_at is not None else None)
    return calculated(
        {"periods": out,
         "period_type": "annual",
         "newest_period_end": newest.period_end.isoformat(),
         "newest_period_age_days": age_days,
         "retrieved_age_days": fetch_age_days,
         "source": "annual statements as retrieved by the data provider",
         "period_label": "PROVIDER LABEL. period_end is the date the provider returned for this "
                         "annual period. No issuer filing or SEC submission has been read to "
                         "confirm the issuer's fiscal year ended then, so this is the latest "
                         "STORED year, not the latest reported one",
         "derived_figures": "CALCULATED HERE from the stored provider series — growth rates and "
                            "margin changes are not provider-reported figures. Each compares "
                            "two stored rows whose accounting basis is unknown, so their "
                            "comparability is unverified",
         "dilution": "NOT COMPUTED — no share count is stored anywhere in this platform, and "
                     "deriving one from net income and EPS would assume the EPS accounting "
                     "basis, which is also not stored",
         "accounting_basis": "NOT STORED. These figures cannot be shown to be GAAP or adjusted, "
                             "so a comparison against an issuer-release figure crosses an "
                             "unverified basis boundary — and so does a comparison between two "
                             "rows of this same series, because neither row's basis is known. A "
                             "basis change between stored years would move growth and margin "
                             "with no change in the business",
         "availability": "filing dates are not stored, only retrieval. These are reported "
                         "history and cannot support a point-in-time claim"},
        units="mixed")
