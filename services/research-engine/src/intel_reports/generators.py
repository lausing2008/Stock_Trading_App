"""The four report generators. Deterministic only — no LLM on this path.

WHY NO LLM HERE. The contract requires deterministic code to calculate every indicator,
comparison, surprise, return and payoff, and allows an LLM only to summarise evidence already
supplied. A report that cannot be produced without a language model is a report that disappears
during a provider outage, so the deterministic tables ARE the report and narration is a later,
optional layer over them.

Each generator returns (fields, evidence, meta). The caller persists; nothing here writes.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select

from db import EarningsEvent, Stock
from . import adapters as A
from . import interpretation as I
from . import verdicts as V
from . import documents as D
from intelligence.report_contract import (
    HORIZONS, EarningsStage, EvidenceBook, Field, FieldState, ReportType, Section,
    StatementClass, TimeFrame,
    calculated, coverage, fields_fingerprint, interpreted, not_applicable, observed,
    status_from_coverage, unavailable, unknown, validate_evidence,
)

#: Bumped with the IR-01..IR-04 corrections: cutoff-bounded inputs, populated evidence, horizon
#: outlooks no longer copied from one daily heuristic, and earnings comparisons bound to the
#: frozen baseline. Reports written under policy 1 meant something different.
POLICY_VERSION = "3"


def _retime(fields: dict[str, Field], mapping: dict[str, tuple]) -> None:
    """Tag fields with WHEN they describe and WHERE they belong in the reading order.

    THE DEFECT THIS ADDRESSES. A post-earnings report listed June's results, October's closing
    price and today's BUY signal in one flat sequence. Each is true; together, undifferentiated,
    today's signal reads as a prediction made before June's results — and nothing on the screen
    said otherwise. Separating "at the earnings event" from "current market context" is the
    whole fix, and it belongs on the field rather than in a renderer that would have to guess.
    """
    for key, (timeframe, section, label) in mapping.items():
        f = fields.get(key)
        if f is None:
            continue
        f.timeframe, f.section = timeframe, section
        if label:
            f.label = label


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


#: The exchange whose local day defines a release date, per market.
_MARKET_TZ = {"US": "America/New_York", "HK": "Asia/Hong_Kong"}


def release_boundary(event, market: str | None = None) -> datetime:
    """The UTC instant a frozen baseline must precede, given what this platform stores.

    ONLY A DATE IS STORED — no release time, no before-open/after-close marker, no timezone. The
    release instant cannot be established, and the rule is explicit: where release timing cannot
    be established, do not certify a baseline as pre-release. The conservative boundary is the
    START of the release date; a report written at any point on that day might have been written
    after the announcement, and nothing on file rules it out.

    THE DATE IS AN EXCHANGE-LOCAL DATE, NOT A UTC ONE. Reading naive midnight as UTC is not
    conservative in both directions: Hong Kong is UTC+8, so a baseline written at 20:00 UTC the
    previous calendar day is already 04:00 on the HK release day — past the real boundary while
    appearing to precede it. The exchange-local start of day is converted to UTC instead, which
    moves the HK boundary 8 hours EARLIER and the US boundary 4-5 hours LATER than naive
    midnight, each in the safe direction for its own market.
    """
    local_start = datetime.combine(event.report_date, time.min)
    tz_name = _MARKET_TZ.get((market or "US").upper())
    if tz_name is None:
        # An unrecognised market cannot be placed on a clock; keep the earliest possible reading
        # rather than guessing a timezone that might move the boundary the wrong way.
        return local_start - timedelta(hours=14)
    tz = ZoneInfo(tz_name)
    return local_start.replace(tzinfo=tz).astimezone(timezone.utc).replace(tzinfo=None)


def release_has_happened(event, now: datetime) -> tuple[bool, str]:
    """Whether the release is known to be behind us, and the evidence for saying so."""
    if event.eps_actual is not None or event.revenue_actual is not None:
        return True, "actual results are on file for this event"
    if event.report_date < now.date():
        return True, f"the release date {event.report_date.isoformat()} has passed"
    return False, ""


def _outlook_by_horizon(trend: Field, price: Field) -> dict[str, Field]:
    """One DAILY structure reading, and an explicit refusal to dress it up as three horizons.

    WHAT THIS USED TO DO, and why it was worse than reporting nothing: it rendered the same
    latest-close-versus-20-bar-average sentence under all three horizons, changing only the
    label and the session range. Three fields that look independently derived but restate one
    daily heuristic are three times the confidence with none of the evidence — and the argument
    it took for the signal was not even read.

    There is no horizon-specific rule or dataset behind a 1-5 session view versus a 1-3 month
    one here, so the honest output is the daily structure plus UNAVAILABLE for each horizon,
    naming what would be needed. Reporting less is the correction, not a regression.
    """
    out: dict[str, Field] = {}
    if trend.state is not FieldState.OK:
        reason = f"no observed daily structure to describe: {trend.reason}"
        for key in HORIZONS:
            out[f"outlook_{key}"] = unavailable(reason)
        out["observed_daily_structure"] = Field(
            value=None, state=trend.state,
            reason=trend.reason or "unavailable",
            statement=StatementClass.OBSERVED_FACT)
        return out

    sma20 = trend.value.get("sma20")
    above = bool(trend.value.get("above_sma20"))
    # CONFIRMATION AND INVALIDATION FOLLOW THE DIRECTION. Previously both were written for the
    # constructive case, so a below-average reading reported "a close below" as what would
    # invalidate it — the very condition that already supported the description.
    out["observed_daily_structure"] = Field(
        value={
            "basis": "latest daily close versus the 20-bar average",
            "reading": ("close is above the 20-bar average" if above
                        else "close is below the 20-bar average"),
            "level": sma20,
            "what_would_change_this_reading": (f"a daily close below {sma20}" if above
                                               else f"a daily close above {sma20}"),
            "scope": "this describes the daily bars only; it is not a forecast and carries no "
                     "horizon",
        },
        state=FieldState.OK, statement=StatementClass.OBSERVED_FACT,
        evidence_ids=list(trend.evidence_ids or []))

    for key, spec in HORIZONS.items():
        out[f"outlook_{key}"] = unavailable(
            f"no horizon-specific rule or dataset exists for {spec['label']} "
            f"({spec['min_sessions']}-{spec['max_sessions']} sessions). The daily structure "
            f"above is one reading and is not re-labelled as three independent outlooks; a "
            f"horizon view needs its own evidence and its own evaluation.")
    return out


def _scenarios(subject: str, trend: Field) -> Field:
    if trend.state is not FieldState.OK:
        return unavailable(f"scenarios need an observed trend: {trend.reason}")
    s = trend.value
    level = s.get("sma20")
    return Field(
        value=[
            {"scenario": "bull", "conditions": f"{subject} holds above {level} and breadth improves",
             "confirmation": "successive closes above the 20-session average",
             "invalidation": f"a daily close below {level}"},
            {"scenario": "base", "conditions": f"{subject} oscillates around {level}",
             "confirmation": "no sustained break of either side",
             "invalidation": "a decisive move beyond the recent range"},
            {"scenario": "bear", "conditions": f"{subject} loses {level} and breadth deteriorates",
             "confirmation": "successive closes below the 20-session average",
             "invalidation": f"recovery above {level}"},
        ],
        state=FieldState.OK, statement=StatementClass.CONDITIONAL_SCENARIO,
        # No probabilities: the templates say not to force them, and nothing here estimates one.
        reason=None)


# ── 1. Market outlook ─────────────────────────────────────────────────────────────────────

def market_outlook(session, *, market: str = "US", now: datetime | None = None):
    now = now or _naive_utc_now()
    today = now.date()
    benchmark = session.execute(
        select(Stock).where(Stock.symbol == ("SPY" if market == "US" else "2800.HK"))
    ).scalars().first()

    fields: dict[str, Field] = {}
    book = EvidenceBook()

    if benchmark is None:
        fields["benchmark"] = unavailable(
            f"the {market} benchmark proxy is not in the universe, so index-level price "
            f"structure cannot be computed")
        trend = unavailable("no benchmark symbol")
        fields["price_as_of"] = unavailable("no benchmark symbol")
    else:
        bars = A.daily_bars(session, benchmark.id, limit=70, cutoff=now)
        fields["benchmark"] = observed(
            {"symbol": benchmark.symbol, "name": benchmark.name,
             "basis": "an ETF proxy for the market, not the index itself"})
        fields["price_as_of"] = A.price_as_of(bars, now, book=book)
        for n in (1, 5, 20, 63):
            fields[f"return_{n}_bars"] = A.session_return(
                bars, n, label=f"{n}-bar return", book=book)
        trend = A.trend_structure(bars, price_field=fields["price_as_of"], book=book)
        fields["trend_structure"] = trend

    fields["breadth"] = A.breadth(session, market, today, cutoff=now)
    fields["sector_leadership"] = A.sector_leadership(session, market, cutoff=now)

    # Dimensions the templates ask for that this platform cannot source TODAY. Named
    # individually rather than omitted, so a reader knows the report looked and came back empty.
    fields["volatility"] = unavailable(
        "no volatility index or realised-volatility series is ingested for this market")
    fields["rates_credit_fx"] = unavailable(
        "no yield, credit-spread or FX series is ingested; a credit ETF proxy would be a proxy, "
        "not a measured spread, and is not substituted here")
    # WORDED AS AN ADAPTER GAP, NOT A PLATFORM INVENTORY. event-intelligence does carry an
    # economic calendar; what is missing is a join into this report with the frozen prior
    # expectations the template requires. Claiming the platform has none would be false.
    fields["macro"] = unavailable(
        "not joined to this report: an economic calendar exists in event-intelligence, but "
        "releases with their frozen prior expectations are not assembled here")
    fields["liquidity"] = unavailable(
        "not joined to this report: no financial-conditions measure is assembled here")
    fields["positioning"] = unavailable(
        "not joined to this report: options/GEX positioning is ingested per symbol and is not "
        "aggregated to a market view here")

    fields.update(_outlook_by_horizon(trend, fields.get("price_as_of", trend)))
    fields["rates"] = A.rates(session, cutoff=now)
    fields["drivers"] = I.drivers_for_market(fields)
    fields["scenarios"] = _scenarios(f"the {market} benchmark", trend)

    # DIRECTION NEEDS A COMPARISON. Participation is a level; "broadening" is a claim about
    # change, so an earlier reading is taken and the assessment says UNKNOWN without one.
    _prior = _prior_covered = _prior_pop = None
    try:
        _earlier = A.breadth(session, market, today, cutoff=now - timedelta(days=7))
        if _earlier.state is FieldState.OK and isinstance(_earlier.value, dict):
            _prior = _earlier.value.get("participation_pct")
            _prior_covered = _earlier.value.get("covered")
            _prior_pop = _earlier.value.get("population_fingerprint")
    except Exception:
        _prior = _prior_covered = _prior_pop = None
    fields["headline_assessment"] = I.outlook_assessment(
        fields, subject=f"The {market} benchmark", report_type="market_outlook",
        prior_participation=_prior, prior_covered=_prior_covered,
        prior_population=_prior_pop)
    # READING ORDER. Without a map every field defaulted to metrics/current, so the page opened
    # on whatever sorted first — the execution disclaimer — and the latest price, the observed
    # structure and the participation a reader came for sat below it among unavailable inputs.
    _retime(fields, {
        "headline_assessment": (TimeFrame.CURRENT, Section.SUMMARY, "Read this first"),
        "drivers":           (TimeFrame.CURRENT, Section.SUMMARY, "What may be driving this"),
        "benchmark":         (TimeFrame.IDENTITY, Section.EVENT, "Benchmark"),
        "rates":             (TimeFrame.CURRENT, Section.METRICS, "Rates and credit"),
        "price_as_of":       (TimeFrame.CURRENT, Section.METRICS, "Latest close"),
        "trend_structure":   (TimeFrame.CURRENT, Section.METRICS, "Observed structure"),
        "observed_daily_structure": (TimeFrame.CURRENT, Section.METRICS,
                                     "Observed daily structure"),
        "breadth":           (TimeFrame.CURRENT, Section.METRICS, "Participation"),
        "sector_leadership": (TimeFrame.CURRENT, Section.METRICS, "Sector leadership"),
        "return_1_bars":     (TimeFrame.CURRENT, Section.METRICS, "Return over 1 daily bar"),
        "return_5_bars":     (TimeFrame.CURRENT, Section.METRICS, "Return over 5 daily bars"),
        "return_20_bars":    (TimeFrame.CURRENT, Section.METRICS, "Return over 20 daily bars"),
        "return_63_bars":    (TimeFrame.CURRENT, Section.METRICS, "Return over 63 daily bars"),
        "scenarios":         (TimeFrame.TIMELESS, Section.SCENARIOS, "Conditional scenarios"),
        "volatility":        (TimeFrame.CURRENT, Section.LIMITATIONS, "Volatility"),
        "macro":             (TimeFrame.CURRENT, Section.LIMITATIONS, "Macro"),
        "liquidity":         (TimeFrame.CURRENT, Section.LIMITATIONS, "Liquidity"),
        "positioning":       (TimeFrame.CURRENT, Section.LIMITATIONS, "Positioning"),
        "rates_credit_fx":   (TimeFrame.CURRENT, Section.LIMITATIONS, "FX and other series"),
        "outlook_short":     (TimeFrame.TIMELESS, Section.LIMITATIONS, "Outlook, short horizon"),
        "outlook_medium":    (TimeFrame.TIMELESS, Section.LIMITATIONS, "Outlook, medium horizon"),
        "outlook_long":      (TimeFrame.TIMELESS, Section.LIMITATIONS, "Outlook, long horizon"),
    })
    validate_evidence(fields, book)
    cov = coverage(fields)
    meta = {
        "report_type": ReportType.MARKET_OUTLOOK.value,
        "subject_key": f"market:{market}",
        "market": market,
        "symbol": None,
        "status": status_from_coverage(cov).value,
        "policy_version": POLICY_VERSION,
        "cutoff_at": now,
        "fingerprint": fields_fingerprint(fields, policy_version=POLICY_VERSION,
                                          extra={"market": market}),
    }
    return fields, book, meta, cov


# ── 2. Stock outlook ──────────────────────────────────────────────────────────────────────

def _latest_release_evidence(session, stock, now: datetime, book) -> dict:
    """The issuer's most recent RELEASED results, joined into a non-earnings report.

    SAME RULES AS THE EARNINGS REPORT, deliberately reused rather than reimplemented: the event
    must be released and visible at this cutoff, the document must be associated by exact fiscal
    identity or an explicit link, and every figure keeps the units, basis and period the issuer
    gave it. A stock outlook that quietly used looser rules than the post-earnings report would
    produce two different answers about the same quarter.
    """
    ev = session.execute(
        select(EarningsEvent)
        .where(EarningsEvent.stock_id == stock.id,
               EarningsEvent.report_date <= now.date(),
               or_(EarningsEvent.eps_actual.is_not(None),
                   EarningsEvent.revenue_actual.is_not(None)))
        .order_by(EarningsEvent.report_date.desc()).limit(1)).scalars().first()
    if ev is None:
        return {"latest_results": unavailable(
            "no released earnings event is on file for this issuer at this cutoff")}

    out: dict[str, Field] = {}
    rel, doc = D.official_release(session, book, stock.id, period_end=None,
                                  report_date=ev.report_date, cutoff=now, event_id=ev.id)
    out["results_event"] = observed(
        {"event_id": f"earnings_event:{ev.id}",
         "announcement_date": ev.report_date.isoformat(),
         "report_date_source": ev.report_date_source,
         "basis": "the most recent RELEASED event on file for this issuer at this cutoff"},
        label="Results joined to this report")
    out["official_release"] = rel.get("official_release", unavailable("no document"))
    out["source_confirmed_fiscal_period"] = rel.get(
        "source_confirmed_fiscal_period", unknown("no document"))

    if doc is not None:
        confirmed = D.confirmed_fiscal_period(out, doc)
        if confirmed is not None:
            out["fiscal_period"] = confirmed
        if getattr(doc, "facts", None):
            # The SAME reconciliation the earnings report uses, so the figures, their units and
            # their bases are identical in both places.
            seed = {"revenue_actual": unavailable("no revenue actual on file"),
                    "eps_actual": unavailable("no eps actual on file"),
                    "accounting_basis": unknown("not established"),
                    "guidance_change": unavailable("not joined")}
            D.reconcile_into_metrics(seed, doc.facts, document_id=doc.id)
            out.update(seed)
    return out


def stock_outlook(session, *, symbol: str, now: datetime | None = None):
    now = now or _naive_utc_now()
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalars().first()
    if stock is None:
        raise LookupError(f"{symbol} is not in the universe")

    book = EvidenceBook()
    bars = A.daily_bars(session, stock.id, limit=70, cutoff=now)
    fields: dict[str, Field] = {
        "issuer": observed({"symbol": stock.symbol, "name": stock.name,
                            "market": stock.market.value if hasattr(stock.market, "value") else str(stock.market),
                            "currency": stock.currency,
                            "sector": stock.sector or None,
                            "industry": stock.industry or None}),
        "price_as_of": A.price_as_of(bars, now, book=book),
        "next_catalyst": A.next_earnings_event(session, stock.id, now.date(), book=book),
    }
    fields["trend_structure"] = A.trend_structure(
        bars, price_field=fields["price_as_of"], book=book)
    # Named for what it actually reads. It is the signals table, not a risk-checked decision.
    fields["signal_engine_assessment"] = A.latest_signal(session, stock.id, now, book=book)
    for n in (1, 5, 20, 63):
        fields[f"return_{n}_bars"] = A.session_return(bars, n, label=f"{n}-bar return", book=book)

    market = stock.market.value if hasattr(stock.market, "value") else str(stock.market)
    fields["sector_context"] = A.sector_leadership(session, market, cutoff=now)

    fields["company_condition"] = unavailable(
        "no fundamentals time series (revenue, margins, cash flow, share count) is stored with "
        "comparable periods and accounting basis")
    fields["estimate_revisions"] = unavailable(
        "only a single current consensus snapshot is stored; revision history cannot be derived "
        "from one snapshot without claiming changes that were never observed")
    fields["valuation"] = unavailable("no multiples with peer or historical context are stored")
    fields["options_positioning"] = unavailable(
        "per-leg option quotes with their own timestamps and quote quality are not assembled "
        "into this report; archived or last-trade prices are research context, not current "
        "executable quotes")
    fields["news"] = unavailable(
        "not joined to this report: headline ingestion exists, but per-item source times and "
        "first-availability are not assembled here")

    fields.update(_outlook_by_horizon(fields["trend_structure"], fields["price_as_of"]))
    fields["scenarios"] = _scenarios(stock.symbol, fields["trend_structure"])
    fields["execution_status"] = Field(
        value={"status": "information_only",
               "note": "this report is read-only research. Position sizing requires a current "
                       "portfolio snapshot and the existing risk checks, which this path does "
                       "not consult; a constructive report is not an order authorisation."},
        state=FieldState.OK, statement=StatementClass.INTERPRETATION)

    # DRIVERS: why the structure may have formed, not just what it looks like.
    fields.update(_latest_release_evidence(session, stock, now, book))
    # The subject's OWN industry, excluding the subject — the planned peer comparison. The
    # sector mean stays as broader context and is labelled as the wider group it is.
    fields["peer_basket"] = A.peer_basket(session, stock, cutoff=now)
    fields["drivers"] = I.drivers_for_stock(fields, subject=stock.symbol)
    fields["headline_assessment"] = I.outlook_assessment(
        fields, subject=stock.symbol, report_type="stock_outlook")
    _retime(fields, {
        "headline_assessment": (TimeFrame.CURRENT, Section.SUMMARY, "Read this first"),
        "drivers":           (TimeFrame.CURRENT, Section.SUMMARY, "What may be driving this"),
        "issuer":            (TimeFrame.IDENTITY, Section.EVENT, "Issuer"),
        "next_catalyst":     (TimeFrame.IDENTITY, Section.EVENT, "Next catalyst"),
        "results_event":     (TimeFrame.AT_EVENT, Section.EVENT, "Results joined to this report"),
        "fiscal_period":     (TimeFrame.AT_EVENT, Section.EVENT, "Fiscal period"),
        "source_confirmed_fiscal_period": (TimeFrame.AT_EVENT, Section.EVENT,
                                           "Fiscal period, confirmed by the issuer"),
        "revenue_actual":    (TimeFrame.AT_EVENT, Section.METRICS, "Revenue reported"),
        "eps_actual":        (TimeFrame.AT_EVENT, Section.METRICS, "EPS reported"),
        "guidance_current":  (TimeFrame.AT_EVENT, Section.METRICS,
                              "Guidance issued with those results"),
        "guidance_change":   (TimeFrame.AT_EVENT, Section.INTERPRETATION, "Guidance change"),
        "accounting_basis":  (TimeFrame.AT_EVENT, Section.LIMITATIONS, "Accounting basis"),
        "official_release":  (TimeFrame.AT_EVENT, Section.SOURCES, "Official release"),
        "latest_results":    (TimeFrame.AT_EVENT, Section.LIMITATIONS, "Latest results"),
        "price_as_of":       (TimeFrame.CURRENT, Section.METRICS, "Latest close"),
        "trend_structure":   (TimeFrame.CURRENT, Section.METRICS, "Observed structure"),
        "observed_daily_structure": (TimeFrame.CURRENT, Section.METRICS,
                                     "Observed daily structure"),
        "peer_basket":       (TimeFrame.CURRENT, Section.METRICS,
                              "Industry peer basket (subject excluded)"),
        "sector_context":    (TimeFrame.CURRENT, Section.METRICS,
                              "Broader sector context (wider group)"),
        "return_1_bars":     (TimeFrame.CURRENT, Section.METRICS, "Return over 1 daily bar"),
        "return_5_bars":     (TimeFrame.CURRENT, Section.METRICS, "Return over 5 daily bars"),
        "return_20_bars":    (TimeFrame.CURRENT, Section.METRICS, "Return over 20 daily bars"),
        "return_63_bars":    (TimeFrame.CURRENT, Section.METRICS, "Return over 63 daily bars"),
        "signal_engine_assessment": (TimeFrame.CURRENT, Section.INTERPRETATION,
                                     "Signal engine, at this snapshot's cutoff"),
        "scenarios":         (TimeFrame.TIMELESS, Section.SCENARIOS, "Conditional scenarios"),
        "company_condition": (TimeFrame.CURRENT, Section.LIMITATIONS, "Company condition"),
        "estimate_revisions": (TimeFrame.CURRENT, Section.LIMITATIONS, "Estimate revisions"),
        "valuation":         (TimeFrame.CURRENT, Section.LIMITATIONS, "Valuation"),
        "options_positioning": (TimeFrame.CURRENT, Section.LIMITATIONS, "Options positioning"),
        "news":              (TimeFrame.CURRENT, Section.LIMITATIONS, "News"),
        "outlook_short":     (TimeFrame.TIMELESS, Section.LIMITATIONS, "Outlook, short horizon"),
        "outlook_medium":    (TimeFrame.TIMELESS, Section.LIMITATIONS, "Outlook, medium horizon"),
        "outlook_long":      (TimeFrame.TIMELESS, Section.LIMITATIONS, "Outlook, long horizon"),
        # The disclaimer is real and it is not the headline. It led the first screen before.
        "execution_status":  (TimeFrame.TIMELESS, Section.SOURCES, "Execution status"),
    })
    validate_evidence(fields, book)
    cov = coverage(fields)
    return fields, book, {
        "report_type": ReportType.STOCK_OUTLOOK.value,
        "subject_key": f"stock:{stock.symbol}",
        "market": market,
        "symbol": stock.symbol,
        "status": status_from_coverage(cov).value,
        "policy_version": POLICY_VERSION,
        "cutoff_at": now,
        "fingerprint": fields_fingerprint(fields, policy_version=POLICY_VERSION,
                                          extra={"symbol": stock.symbol}),
    }, cov


# ── 3. Pre-earnings outlook (the FROZEN baseline) ─────────────────────────────────────────

def pre_earnings(session, *, symbol: str, now: datetime | None = None):
    now = now or _naive_utc_now()
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalars().first()
    if stock is None:
        raise LookupError(f"{symbol} is not in the universe")
    event = session.execute(
        select(EarningsEvent).where(EarningsEvent.stock_id == stock.id,
                                    EarningsEvent.report_date >= now.date())
        .order_by(EarningsEvent.report_date.asc()).limit(1)).scalars().first()
    if event is None:
        raise LookupError(f"no upcoming earnings event on file for {symbol}")

    # A PRE-RELEASE REPORT CANNOT BE WRITTEN ONCE THE RELEASE IS KNOWN. `report_date >= today`
    # alone admits an event that reported earlier the SAME DAY — actuals already on file — and
    # would stamp it "pre_earnings". Generating one afterwards is a reconstruction; it is
    # refused here rather than produced and labelled later.
    happened, why = release_has_happened(event, now)
    if happened:
        raise LookupError(
            f"the {symbol} release for {event.report_date.isoformat()} is already known "
            f"({why}); a pre-release baseline cannot be generated after the fact. Generate a "
            f"post-earnings report instead.")
    _market = stock.market.value if hasattr(stock.market, "value") else str(stock.market)
    if now >= release_boundary(event, _market):
        raise LookupError(
            f"only a release DATE is stored for {symbol} ({event.report_date.isoformat()}), "
            f"not a time, so a report written on the release day cannot be shown to precede "
            f"the announcement and is not certified as a baseline.")

    book = EvidenceBook()
    A.record_event(book, event)
    bars = A.daily_bars(session, stock.id, limit=70, cutoff=now)
    actuals = A.earnings_actuals(event)
    fields: dict[str, Field] = {
        "issuer": observed({"symbol": stock.symbol, "name": stock.name}),
        "event_identity": observed({
            "event_id": f"earnings_event:{event.id}",
            "report_date": event.report_date.isoformat(),
            "scheduled_time_state": "UNKNOWN",
        }),
        "fiscal_period": A.fiscal_period(event),
        "release_time_certainty": unknown(
            "only a report DATE is stored; whether the release is before the open or after the "
            "close is not recorded, and that determines which session reacts"),
        "pre_event_reference_price": A.price_as_of(bars, now, book=book),
        # STORED AS A PLAIN NUMBER ON PURPOSE. This is the value the post-earnings report
        # reads back as the frozen expectation, so its shape is part of the freeze contract and
        # must not drift with the presentation of the comparison table.
        "consensus_eps": (observed(float(event.eps_estimate),
                                   evidence_ids=[f"earnings_event:{event.id}"])
                          if event.eps_estimate is not None
                          else unavailable("no EPS estimate on file to freeze")),
        "consensus_revenue": (observed(float(event.revenue_estimate),
                                       evidence_ids=[f"earnings_event:{event.id}"])
                              if event.revenue_estimate is not None
                              else unavailable("no revenue estimate on file to freeze")),
        "consensus_snapshot": unknown(
            "the stored estimate has no snapshot time, contributor count or dispersion, so it "
            "cannot be shown to be the consensus as of this cutoff"),
        "prior_guidance": unavailable("company guidance is not stored"),
        "accounting_basis": actuals["accounting_basis"],
        "run_up_20_bars": A.session_return(bars, 20, label="20-bar run-up", book=book),
        "run_up_5_bars": A.session_return(bars, 5, label="5-bar run-up", book=book),
        "historical_reactions": _historical_reactions(session, stock.id, event.id, now),
        "options_expected_move": unavailable(
            "an expected move requires an option chain with a post-release expiry and its own "
            "quote timestamps; a straddle premium would be a premium-based proxy, not a "
            "calibrated interval, and none is assembled here"),
        "release_boundary": observed(
            {"baseline_must_precede_utc": release_boundary(event, _market).isoformat(),
             "exchange": _market,
             "basis": "start of the stored release DATE in the exchange's own timezone, "
                      "converted to UTC; no release time is on file"}),
    }
    fields["trend_structure"] = A.trend_structure(
        bars, price_field=fields["pre_event_reference_price"], book=book)
    fields["signal_engine_assessment"] = A.latest_signal(session, stock.id, now, book=book)
    fields["scenarios"] = Field(
        value=[
            {"scenario": "bull", "combination": "results above the stored estimate AND guidance raised",
             "interpretation": "conditional: supports the constructive case",
             "confirmation": "a positive reaction that holds through the first regular session",
             "invalidation": "the reaction fades back below the pre-event reference price"},
            {"scenario": "mixed", "combination": "results above estimate BUT guidance lowered or withheld",
             "interpretation": "conditional: a beat is not good guidance, and the two verdicts differ",
             "confirmation": "direction resolves over the following sessions",
             "invalidation": "n/a until the release"},
            {"scenario": "bear", "combination": "results below the stored estimate AND guidance lowered",
             "interpretation": "conditional: contradicts the constructive case",
             "confirmation": "a negative reaction that persists",
             "invalidation": "the stock recovers the pre-event reference price"},
        ],
        state=FieldState.OK, statement=StatementClass.CONDITIONAL_SCENARIO)
    fields["management_questions"] = interpreted([
        "What changed in demand since the prior period, and is it pricing or volume?",
        "Which cost or margin line is expected to move next period, and why?",
        "What would have to happen for the guidance range's low end to be reached?",
    ])
    fields["execution_status"] = Field(
        value={"status": "information_only",
               "note": "no new position is recommended into an event by this report"},
        state=FieldState.OK, statement=StatementClass.INTERPRETATION)

    _days_out = (event.report_date - now.date()).days
    fields["snapshot_stage"] = Field(
        value={"calendar_days_before_scheduled_release": _days_out,
               "stage": ("early_preparation" if _days_out > 10 else "immediate_pre_release"),
               "note": ("prices and structure here are an EARLY PREPARATION snapshot taken "
                        f"{_days_out} CALENDAR days before the scheduled release — not the "
                        "immediate pre-release "
                        "reference. Later snapshots are captured as their own versions and this "
                        "one is preserved." if _days_out > 10 else
                        f"taken {_days_out} days before the release")},
        state=FieldState.OK, statement=StatementClass.OBSERVED_FACT,
        timeframe=TimeFrame.IDENTITY, section=Section.EVENT, label="Snapshot timing")
    fields["headline_assessment"] = I.pre_earnings_assessment(
        fields, subject=stock.symbol, event_date=event.report_date.isoformat(),
        days_out=_days_out)
    _retime(fields, {
        "headline_assessment": (TimeFrame.IDENTITY, Section.SUMMARY, "Read this first"),
        "issuer":            (TimeFrame.IDENTITY, Section.EVENT, "Company"),
        "event_identity":    (TimeFrame.IDENTITY, Section.EVENT, "Earnings event"),
        "fiscal_period":     (TimeFrame.IDENTITY, Section.EVENT, "Fiscal period"),
        "release_boundary":  (TimeFrame.IDENTITY, Section.EVENT, "Baseline must precede"),
        "release_time_certainty": (TimeFrame.IDENTITY, Section.LIMITATIONS, "Release timing"),
        "consensus_eps":     (TimeFrame.AT_EVENT, Section.METRICS, "EPS expected"),
        "consensus_revenue": (TimeFrame.AT_EVENT, Section.METRICS, "Revenue expected"),
        "consensus_snapshot": (TimeFrame.AT_EVENT, Section.LIMITATIONS, "Consensus provenance"),
        "prior_guidance":    (TimeFrame.AT_EVENT, Section.METRICS, "Prior company guidance"),
        "accounting_basis":  (TimeFrame.AT_EVENT, Section.LIMITATIONS, "Accounting basis"),
        "options_expected_move": (TimeFrame.CURRENT, Section.LIMITATIONS, "Options expected move"),
        "historical_reactions": (TimeFrame.HISTORICAL, Section.METRICS,
                                 "How the shares moved at past releases"),
        "pre_event_reference_price": (TimeFrame.CURRENT, Section.METRICS, "Share price now"),
        "run_up_5_bars":     (TimeFrame.CURRENT, Section.METRICS, "Share price, last 5 bars"),
        "run_up_20_bars":    (TimeFrame.CURRENT, Section.METRICS, "Share price, last 20 bars"),
        "trend_structure":   (TimeFrame.CURRENT, Section.METRICS, "Price structure now"),
        "observed_daily_structure": (TimeFrame.CURRENT, Section.METRICS, "Daily structure now"),
        "signal_engine_assessment": (TimeFrame.CURRENT, Section.INTERPRETATION,
                                     "Signal engine, at this snapshot's cutoff"),
        "scenarios":         (TimeFrame.TIMELESS, Section.SCENARIOS, "Conditional scenarios"),
        "management_questions": (TimeFrame.TIMELESS, Section.INTERPRETATION,
                                 "Questions for management"),
        "execution_status":  (TimeFrame.TIMELESS, Section.LIMITATIONS, "Execution status"),
    })
    validate_evidence(fields, book)
    cov = coverage(fields)
    return fields, book, {
        "report_type": ReportType.PRE_EARNINGS.value,
        "subject_key": f"earnings:{stock.symbol}:{event.report_date.isoformat()}",
        "market": stock.market.value if hasattr(stock.market, "value") else str(stock.market),
        "symbol": stock.symbol,
        "status": status_from_coverage(cov).value,
        "policy_version": POLICY_VERSION,
        "cutoff_at": now,
        "event_id": event.id,
        "release_boundary": release_boundary(event, _market),
        "fingerprint": fields_fingerprint(fields, policy_version=POLICY_VERSION,
                                          extra={"event_id": event.id}),
    }, cov


def _historical_reactions(session, stock_id: int, exclude_event_id: int,
                          cutoff: datetime) -> Field:
    rows = list(session.execute(
        select(EarningsEvent).where(EarningsEvent.stock_id == stock_id,
                                    EarningsEvent.id != exclude_event_id,
                                    EarningsEvent.report_date < cutoff.date(),
                                    EarningsEvent.post_earnings_return_1d.is_not(None))
        .order_by(EarningsEvent.report_date.desc()).limit(12)).scalars().all())
    if not rows:
        return unavailable("no past earnings reactions with a recorded 1-session return")
    # SAME FRACTION->PERCENT CONVERSION as post_event_reaction. These summarised the raw
    # fractions under percentage-named fields, so a median of 0.0690 read as 0.07% when it
    # means 6.90%.
    vals = sorted(A.fraction_to_pct(r.post_earnings_return_1d) for r in rows)
    mid = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
    return calculated(
        {"events": len(vals), "median_1d_pct": round(mid, 2),
         "min_pct": round(vals[0], 2), "max_pct": round(vals[-1], 2),
         "dates": [r.report_date.isoformat() for r in rows],
         "limitation": "each value is the stored return from the last close BEFORE its report "
                       "date to the close 1 trading day after that date's own bar — for a "
                       "trading-day release that spans TWO close-to-close intervals, not one. "
                       "Not normalised to the announcement instant, and n is small."},
        units="pct")


# ── 4. Post-earnings analysis ─────────────────────────────────────────────────────────────

def post_earnings(session, *, symbol: str, event_id: int | None = None,
                  pre_report=None, now: datetime | None = None):
    now = now or _naive_utc_now()
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalars().first()
    if stock is None:
        raise LookupError(f"{symbol} is not in the universe")
    q = select(EarningsEvent).where(EarningsEvent.stock_id == stock.id)
    if event_id is not None:
        q = q.where(EarningsEvent.id == event_id)
    else:
        q = q.where(EarningsEvent.report_date <= now.date())
    event = session.execute(q.order_by(EarningsEvent.report_date.desc()).limit(1)).scalars().first()
    if event is None:
        raise LookupError(f"no released earnings event on file for {symbol}")
    _market = stock.market.value if hasattr(stock.market, "value") else str(stock.market)
    book = EvidenceBook()
    # DOCUMENT ASSOCIATION IS REPORTED SEPARATELY FROM CADENCE, and neither confirms a missing
    # event from date arithmetic. An unassociated release says nobody has mapped it, not that an
    # event is absent — a 55-day announcement lag is ordinary, and treating it as evidence of
    # absence was how a correctly-stored event came to look missing.
    document_association = D.assess_event_association(session, stock.id, now=now, book=book)
    coverage_warning = _event_coverage_warning(session, stock.id, event, now)

    A.record_event(book, event)
    bars = A.daily_bars(session, stock.id, limit=70, cutoff=now)

    # IR-04: THE SURPRISE TABLE AND THE VERDICT MUST USE THE SAME EXPECTATION.
    # `earnings_actuals(event)` reads the CURRENT stored estimate, which the provider revises —
    # so with a frozen baseline present the report showed a -11.79% "miss" in its table beside a
    # verdict of "above", from the same two numbers. Where a frozen baseline exists it is the
    # expectation for both; the later estimate is still shown, separately and dated, because a
    # revision is real information and hiding it is its own distortion.
    frozen_eps, frozen_rev = _frozen_expectations(pre_report)
    actuals = A.earnings_actuals(event, frozen_eps=frozen_eps, frozen_revenue=frozen_rev,
                                 frozen_from=pre_report)
    reaction = A.post_event_reaction(
        event, window_dates=A.reaction_window_dates(session, stock.id, event.report_date))

    # "Reconciled" asserts a reconciliation was performed. Nothing here reconciles sources, so
    # the stage stays FIRST_FLASH no matter how many actuals are present; claiming otherwise
    # would promise a check that never ran.
    stage = EarningsStage.FIRST_FLASH

    fields: dict[str, Field] = {
        "issuer": observed({"symbol": stock.symbol, "name": stock.name}),
        "event_identity": _event_identity(event),
        "event_coverage": coverage_warning,
        "fiscal_period": A.fiscal_period(event),
        "stage": observed({"stage": stage.value,
                           "note": "no cross-source reconciliation is performed by this "
                                   "report; RECONCILED_RESULTS is never claimed here"}),
        **actuals,
        **reaction,
        "guidance_change": unavailable(
            "not joined to this report: company guidance is not assembled here, so "
            "raised/maintained/lowered/withdrawn cannot be classified from evidence"),
        "management_commentary": (
            observed(event.management_tone, evidence_ids=[f"earnings_event:{event.id}"])
            if getattr(event, "management_tone", None)
            else unavailable("no management commentary is stored for this event")),
        "price_as_of": A.price_as_of(bars, now, book=book),
        "options_reaction": unavailable(
            "fresh per-leg option quotes are required to state an option outcome; an unchanged "
            "quote after a move is not current P&L, and earnings IV decline can hurt a long "
            "option even on a favourable stock move"),
    }

    fields["trend_structure"] = A.trend_structure(
        bars, price_field=fields["price_as_of"], book=book)
    fields["signal_engine_assessment"] = A.latest_signal(session, stock.id, now, book=book)

    # The issuer's own release, joined by period rather than by event id.
    _doc_period = None
    _fiscal = fields.get("source_confirmed_fiscal_period")
    _release_fields, _primary_doc = D.official_release(
        session, book, stock.id, period_end=_doc_period,
        report_date=event.report_date, cutoff=now, event_id=event.id)
    fields.update(_release_fields)

    # RECONCILE, DO NOT MERELY ATTACH. Until this ran, the release sat in its own field while
    # the report's primary metrics still said "no revenue actual on file" and "no confirmed
    # fiscal period is stored" — one report, two answers, and a reader (or a narrator) left to
    # choose between them.
    if _primary_doc is not None:
        _confirmed = D.confirmed_fiscal_period(fields, _primary_doc)
        if _confirmed is not None:
            fields["fiscal_period"] = _confirmed
        if getattr(_primary_doc, "facts", None):
            D.reconcile_into_metrics(fields, _primary_doc.facts,
                                     document_id=_primary_doc.id)
    if document_association is not None:
        fields["document_association"] = document_association

    # The accountability join — and the honest answer when there is nothing to join to.
    if pre_report is None:
        fields["pre_report_link"] = unavailable(
            "no frozen pre-earnings report exists for this event. It is NOT reconstructed: a "
            "baseline built now would contain information the original could not have had, and "
            "scoring against it would be hindsight wearing a forecast's clothes.")
        fields["thesis_verdict"] = not_applicable(
            "no pre-release thesis was recorded, so there is nothing to confirm or contradict")
    else:
        fields["pre_report_link"] = observed(
            {"report_id": pre_report.id, "version": pre_report.version,
             "cutoff_at": pre_report.cutoff_at.isoformat(),
             "generated_at": pre_report.generated_at.isoformat()})
        fields["thesis_verdict"] = _verdict(pre_report, actuals, reaction, frozen_eps)

    # THE OPENING READ, before any of the detail. Derived from the fields themselves, so it
    # cannot drift from the body it summarises — the first thing a narrator would get wrong.
    fields["headline_assessment"] = I.post_earnings_assessment(
        fields, subject=stock.symbol)

    fields["three_verdicts"] = interpreted({
        "business_result_vs_expectations": V.result_verdict(fields),
        "forward_outlook": V.forward_verdict(fields),
        # The number alone invites being read as an isolated earnings reaction. It is a
        # close-to-close return over a window that can span more than one session and can
        # include trading BEFORE the announcement, so it travels with its window.
        "market_reaction": (
            {"pct": (reaction["return_1d"].value or {}).get("pct"),
             "window": (reaction["return_1d"].value or {}).get("window"),
             "caveat": "a close-to-close return over the window named, which may span more "
                       "than one interval and include pre-announcement trading; it is not an "
                       "isolated earnings reaction"}
            if reaction["return_1d"].state is FieldState.OK else "not yet matured"),
        "note": "these are three separate verdicts; a beat, good guidance and a positive "
                "reaction routinely disagree and are not collapsed into one score",
    })

    _retime(fields, {
        # Identity — who and what.
        "headline_assessment": (TimeFrame.AT_EVENT, Section.SUMMARY, "Read this first"),
        "issuer":            (TimeFrame.IDENTITY, Section.EVENT, "Company"),
        "event_identity":    (TimeFrame.IDENTITY, Section.EVENT, "Earnings event"),
        "event_coverage":    (TimeFrame.IDENTITY, Section.LIMITATIONS, "Coverage of this event"),
        "document_association": (TimeFrame.IDENTITY, Section.LIMITATIONS,
                                 "Release documents and their event mapping"),
        "fiscal_period":     (TimeFrame.IDENTITY, Section.EVENT, "Fiscal period"),
        "source_confirmed_fiscal_period": (TimeFrame.IDENTITY, Section.EVENT,
                                           "Fiscal period, confirmed by the issuer"),
        "stage":             (TimeFrame.IDENTITY, Section.EVENT, "Reporting stage"),
        # AT THE EVENT — everything measured at or around the release itself.
        "eps_actual":        (TimeFrame.AT_EVENT, Section.METRICS, "EPS reported"),
        "eps_expectation":   (TimeFrame.AT_EVENT, Section.METRICS, "EPS expected"),
        "eps_surprise_pct":  (TimeFrame.AT_EVENT, Section.METRICS,
                              "EPS vs provider estimate — accounting comparability unverified"),
        # ABOUT the event, so it sits with the event's figures — but it was NOT available
        # beforehand, and the field's own value keeps saying so.
        "eps_estimate_revised_since": (TimeFrame.AT_EVENT, Section.SOURCES,
                                       "EPS estimate revised AFTER the freeze"),
        "revenue_estimate_revised_since": (TimeFrame.AT_EVENT, Section.SOURCES,
                                           "Revenue estimate revised AFTER the freeze"),
        "revenue_actual":    (TimeFrame.AT_EVENT, Section.METRICS, "Revenue reported"),
        "revenue_expectation": (TimeFrame.AT_EVENT, Section.METRICS, "Revenue expected"),
        "revenue_surprise_pct": (TimeFrame.AT_EVENT, Section.METRICS,
                                 "Revenue vs provider estimate — accounting comparability "
                                 "unverified"),
        "accounting_basis":  (TimeFrame.AT_EVENT, Section.LIMITATIONS, "Accounting basis"),
        "return_1d":         (TimeFrame.AT_EVENT, Section.METRICS,
                              "Share price, close-to-close across the release"),
        "return_5d":         (TimeFrame.AT_EVENT, Section.METRICS,
                              "Share price, five sessions across the release"),
        "official_release":  (TimeFrame.AT_EVENT, Section.SOURCES, "Official release"),
        "official_figures":  (TimeFrame.AT_EVENT, Section.SOURCES,
                              "All figures from the release (full extract)"),
        "guidance_current":  (TimeFrame.AT_EVENT, Section.METRICS,
                              "Guidance issued with these results"),
        "guidance_change":   (TimeFrame.AT_EVENT, Section.INTERPRETATION, "Guidance change"),
        "management_commentary": (TimeFrame.AT_EVENT, Section.INTERPRETATION,
                                  "Management commentary"),
        "pre_report_link":   (TimeFrame.AT_EVENT, Section.SOURCES, "Frozen pre-earnings report"),
        "thesis_verdict":    (TimeFrame.AT_EVENT, Section.INTERPRETATION, "Thesis evaluation"),
        "three_verdicts":    (TimeFrame.AT_EVENT, Section.INTERPRETATION, "Three separate verdicts"),
        # CURRENT — today's market, which is NOT contemporaneous with the results above.
        "price_as_of":       (TimeFrame.CURRENT, Section.METRICS, "Share price now"),
        "trend_structure":   (TimeFrame.CURRENT, Section.METRICS, "Price structure now"),
        "observed_daily_structure": (TimeFrame.CURRENT, Section.METRICS, "Daily structure now"),
        "signal_engine_assessment": (TimeFrame.CURRENT, Section.INTERPRETATION,
                                     "Signal engine, at this snapshot's cutoff"),
        "options_reaction":  (TimeFrame.CURRENT, Section.LIMITATIONS, "Options evidence"),
    })
    validate_evidence(fields, book)
    cov = coverage(fields)
    return fields, book, {
        "report_type": ReportType.POST_EARNINGS.value,
        "subject_key": f"earnings:{stock.symbol}:{event.report_date.isoformat()}",
        "market": stock.market.value if hasattr(stock.market, "value") else str(stock.market),
        "symbol": stock.symbol,
        "status": status_from_coverage(cov).value,
        "stage": stage.value,
        "policy_version": POLICY_VERSION,
        "cutoff_at": now,
        "event_id": event.id,
        "pre_report_id": getattr(pre_report, "id", None),
        "release_boundary": release_boundary(event, _market),
        "fingerprint": fields_fingerprint(
            fields, policy_version=POLICY_VERSION,
            extra={"event_id": event.id, "pre_report_id": getattr(pre_report, "id", None)}),
    }, cov


#: Fallback cadence when an issuer has too little history to measure its own: a quarter plus a
#: few days of scheduling slack.
_DEFAULT_CADENCE_DAYS = 95

#: How many past events to measure an issuer's own reporting rhythm from.
_CADENCE_SAMPLE = 6


def _issuer_cadence_days(session, stock_id: int, before) -> tuple[int, int]:
    """The issuer's OWN median gap between releases, and how many gaps that was measured from.

    A FIXED THRESHOLD IS THE WRONG INSTRUMENT, and the first version proved it: 115 days was
    picked as "a quarter plus slack", and MU's 24 June event was 101 days old on 3 October, so
    the warning did not fire for precisely the case it was built for. MU's own gaps run 85-115
    days with a median near 95 — by day 101 the next release was already due. Measuring the
    issuer rather than assuming a calendar also handles semi-annual reporters, who would
    otherwise be warned about four times a year for nothing.
    """
    dates = [r.report_date for r in session.execute(
        select(EarningsEvent).where(EarningsEvent.stock_id == stock_id,
                                    EarningsEvent.report_date <= before)
        .order_by(EarningsEvent.report_date.desc()).limit(_CADENCE_SAMPLE)).scalars().all()]
    gaps = sorted((dates[i] - dates[i + 1]).days for i in range(len(dates) - 1))
    if not gaps:
        return _DEFAULT_CADENCE_DAYS, 0
    mid = (gaps[len(gaps) // 2] if len(gaps) % 2
           else (gaps[len(gaps) // 2 - 1] + gaps[len(gaps) // 2]) // 2)
    return mid, len(gaps)


def _event_coverage_warning(session, stock_id: int, event, now: datetime) -> Field:
    """Say so when the newest released event on file is probably not the newest release.

    WHY THIS EXISTS. Asked for MU's post-earnings report on 3 October, the report described the
    24 June event — correctly, by its own rule of "latest released event on file", because the
    30 September release is not in `earnings_events` at all. The rule was right and the answer
    was useless, because nothing told the reader the quarter they wanted was missing rather than
    unremarkable. Silently substituting an older quarter is the failure; naming the gap is not.
    """
    if not A.announcement_date_is_verified(event):
        return Field(
            value={"coverage_state": "coverage_unknown",
                   "analysed_event_period_end": (event.period_end.isoformat()
                                                 if event.period_end else None)},
            state=FieldState.UNKNOWN,
            reason=("the age of this event cannot be measured: its stored date is a substituted "
                    "fiscal period end, not an announcement date, so neither its age nor any "
                    "comparison against this issuer's reporting cadence is meaningful"),
            statement=StatementClass.INTERPRETATION, label="Coverage of this event")
    age_days = (now.date() - event.report_date).days
    cadence, samples = _issuer_cadence_days(session, stock_id, event.report_date)
    upcoming = session.execute(
        select(EarningsEvent).where(EarningsEvent.stock_id == stock_id,
                                    EarningsEvent.report_date > now.date())
        .order_by(EarningsEvent.report_date.asc()).limit(1)).scalars().first()
    detail = {
        "analysed_event_date": event.report_date.isoformat(),
        "age_days": age_days,
        "issuer_median_gap_days": cadence,
        "gaps_measured": samples,
        "next_scheduled": upcoming.report_date.isoformat() if upcoming else None,
    }
    # A MEDIAN IS NOT A DEADLINE, and this field previously said it was: it returned
    # OBSERVED_FACT and asserted the next release "is due or overdue and is not on file". The
    # calculation cannot support that. Normal variation exceeds a median roughly half the time;
    # the gaps are measured from STORED events, so a missing or duplicated row distorts the very
    # number being used to detect missing rows; and with no history the cadence is an outright
    # assumption. What the calculation supports is a SUSPICION worth checking.
    #
    # Three states, because "no warning" is not evidence of completeness either:
    #   suspected_gap          — older than this issuer's own median; verify the release calendar
    #   confirmed_missing_event— only when dated authoritative evidence names an absent release
    #   coverage_unknown       — within the median, which rules nothing out
    if age_days > cadence:
        measured = (f"this issuer's own median gap is {cadence} days (from {samples} past gaps, "
                    f"which are themselves drawn from stored events and so cannot prove what is "
                    f"missing)" if samples else
                    f"there is no issuer history to measure, so a {cadence}-day quarter is an "
                    f"assumption, not a measurement")
        detail["coverage_state"] = "suspected_gap"
        return Field(
            value=detail, state=FieldState.CONFLICTING,
            reason=(f"POSSIBLE COVERAGE GAP, not a confirmed one: the newest stored release is "
                    f"{age_days} days old and {measured}. That is a reason to check the issuer's "
                    f"own release calendar, not proof that a release happened. This report "
                    f"describes the newest event ON FILE and does not substitute one silently."),
            # An inference from cadence, not something observed.
            statement=StatementClass.INTERPRETATION)
    detail["coverage_state"] = "coverage_unknown"
    return Field(
        value=detail, state=FieldState.UNKNOWN,
        reason=(f"the newest stored release is {age_days} days old, within this issuer's "
                f"{cadence}-day median gap. That rules nothing out: coverage is not verified "
                f"against the issuer's own calendar here, so the absence of a warning is not "
                f"evidence that every release is on file."),
        statement=StatementClass.INTERPRETATION)


def _event_identity(event) -> Field:
    """Who and what, with the date presented as what it actually is.

    A SUBSTITUTED PERIOD END IS NOT AN ANNOUNCEMENT DATE and must not be rendered as one, nor
    used to compute how old the event is — "33 days old" measured from a period end is an age
    for the wrong moment entirely.
    """
    verified = A.announcement_date_is_verified(event)
    base = {"event_id": f"earnings_event:{event.id}",
            "fiscal_period_end": event.period_end.isoformat() if event.period_end else None,
            "basis": "the most recent RELEASED event on file for this issuer; see event_coverage"}
    if verified:
        return observed(base | {"announcement_date": event.report_date.isoformat()},
                        label="Earnings event")
    return Field(
        value=base | {
            "announcement_date": None,
            "stored_date": event.report_date.isoformat(),
            "stored_date_is": "the fiscal PERIOD END, standing in for an announcement date that "
                              "is not on file",
        },
        state=FieldState.UNKNOWN,
        reason=("the announcement date is unverified: this row carries a substituted period end, "
                "so the event's date — and anything measured from it, including its age — "
                "cannot be stated"),
        statement=StatementClass.OBSERVED_FACT, label="Earnings event")


def _frozen_expectations(pre_report) -> tuple[float | None, float | None]:
    if pre_report is None:
        return None, None
    frozen = (pre_report.payload or {}).get("fields", {})
    return ((frozen.get("consensus_eps") or {}).get("value"),
            (frozen.get("consensus_revenue") or {}).get("value"))


def _verdict(pre_report, actuals, reaction, frozen_eps) -> Field:
    """Score the frozen report's own CLAIM — not a beat, which is a fact about the company.

    WHAT THIS USED TO DO: call a beat plus a positive first session "confirmed", regardless of
    what the frozen report actually said. A baseline that made no directional claim could be
    awarded a confirmation it never earned. A thesis verdict has to evaluate a thesis.
    """
    actual_eps = actuals["eps_actual"].value if actuals["eps_actual"].state is FieldState.OK else None
    if frozen_eps is None or actual_eps is None:
        return unknown(
            "the frozen report carried no EPS expectation, or no actual is on file, so the "
            "comparison cannot be made")

    frozen_fields = (pre_report.payload or {}).get("fields", {})
    # The pre-earnings report records conditional scenarios, not a directional prediction, so
    # there is no claim to confirm. Report the factual comparison and say so.
    claim = (frozen_fields.get("directional_claim") or {}).get("value")
    beat = actual_eps > frozen_eps
    r1 = (reaction["return_1d"].value or {}).get("pct") \
        if reaction["return_1d"].state is FieldState.OK else None
    return Field(
        value={"frozen_consensus_eps": frozen_eps, "actual_eps": actual_eps,
               "result_vs_frozen": "above" if beat else "at or below",
               "first_session_reaction_pct": r1,
               "thesis_evaluation": ("not_evaluable" if claim is None else
                                     "confirmed" if bool(claim) == beat else "contradicted"),
               "why_not_evaluable": (None if claim is not None else
                                     "the frozen report recorded conditional scenarios rather "
                                     "than a directional prediction, so there is no claim to "
                                     "score; the comparison above is factual"),
               "note": "compared against the expectation FROZEN before the release, which is "
                       "also what the surprise table above uses"},
        state=FieldState.OK, statement=StatementClass.DETERMINISTIC_CALCULATION)
