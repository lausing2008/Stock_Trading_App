"""The four report generators. Deterministic only — no LLM on this path.

WHY NO LLM HERE. The contract requires deterministic code to calculate every indicator,
comparison, surprise, return and payoff, and allows an LLM only to summarise evidence already
supplied. A report that cannot be produced without a language model is a report that disappears
during a provider outage, so the deterministic tables ARE the report and narration is a later,
optional layer over them.

Each generator returns (fields, evidence, meta). The caller persists; nothing here writes.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select

from db import EarningsEvent, Stock
from . import adapters as A
from intelligence.report_contract import (
    HORIZONS, EarningsStage, Field, FieldState, ReportType, StatementClass,
    calculated, coverage, input_fingerprint, interpreted, not_applicable, observed,
    status_from_coverage, unavailable, unknown,
)

POLICY_VERSION = "1"


def _naive_utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _outlook_by_horizon(trend: Field, signal: Field) -> dict[str, Field]:
    """A CONDITIONAL outlook per horizon, with its condition stated.

    Deliberately not a direction with a probability attached. The platform has no model whose
    label horizon matches these definitions and whose calibration has been demonstrated — the
    2026-09-30 checkpoint measured confidence bands as flat — so asserting a probability here
    would be inventing one. What IS available is the observed structure and the condition under
    which it would stop holding, which is what the templates ask for.
    """
    out: dict[str, Field] = {}
    for key, spec in HORIZONS.items():
        if trend.state is not FieldState.OK:
            out[f"outlook_{key}"] = unavailable(
                f"no observed trend to condition on: {trend.reason}")
            continue
        s = trend.value
        direction = ("constructive while it holds above the 20-session average"
                     if s.get("above_sma20") else
                     "unconstructive while it remains below the 20-session average")
        out[f"outlook_{key}"] = Field(
            value={"horizon": spec["label"],
                   "sessions": [spec["min_sessions"], spec["max_sessions"]],
                   "conditional_outlook": direction,
                   "key_condition": f"close holds above {s.get('sma20')}",
                   "invalidation": f"a close below {s.get('sma20')} on the daily bars",
                   "forecast_probability": None,
                   "probability_note": "omitted: no model on this platform has a demonstrated "
                                       "calibration for this horizon definition"},
            state=FieldState.OK, statement=StatementClass.CONDITIONAL_SCENARIO)
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
    evidence: list[dict] = []

    if benchmark is None:
        fields["benchmark"] = unavailable(
            f"the {market} benchmark proxy is not in the universe, so index-level price "
            f"structure cannot be computed")
        trend = unavailable("no benchmark symbol")
        fields["price_as_of"] = unavailable("no benchmark symbol")
    else:
        bars = A.daily_bars(session, benchmark.id, limit=70)
        fields["benchmark"] = observed(
            {"symbol": benchmark.symbol, "name": benchmark.name,
             "basis": "an ETF proxy for the market, not the index itself"})
        fields["price_as_of"] = A.price_as_of(bars, now)
        for n in (1, 5, 20, 63):
            fields[f"return_{n}_sessions"] = A.session_return(bars, n, label=f"{n}-session return")
        trend = A.trend_structure(bars)
        fields["trend_structure"] = trend

    fields["breadth"] = A.breadth(session, market, today)
    fields["sector_leadership"] = A.sector_leadership(session, market)

    # Dimensions the templates ask for that this platform cannot source TODAY. Named
    # individually rather than omitted, so a reader knows the report looked and came back empty.
    fields["volatility"] = unavailable(
        "no volatility index or realised-volatility series is ingested for this market")
    fields["rates_credit_fx"] = unavailable(
        "no yield, credit-spread or FX series is ingested; a credit ETF proxy would be a proxy, "
        "not a measured spread, and is not substituted here")
    fields["macro"] = unavailable(
        "no macro release calendar with frozen prior expectations is ingested")
    fields["liquidity"] = unavailable("no financial-conditions measure is ingested")
    fields["positioning"] = unavailable(
        "options/GEX positioning is ingested per symbol, not aggregated to a market view")

    fields.update(_outlook_by_horizon(trend, unavailable("n/a")))
    fields["scenarios"] = _scenarios(f"the {market} benchmark", trend)

    cov = coverage(fields)
    meta = {
        "report_type": ReportType.MARKET_OUTLOOK.value,
        "subject_key": f"market:{market}",
        "market": market,
        "symbol": None,
        "status": status_from_coverage(cov).value,
        "policy_version": POLICY_VERSION,
        "cutoff_at": now,
        "fingerprint": input_fingerprint(
            {k: v.value for k, v in fields.items() if v.state is FieldState.OK}),
    }
    return fields, evidence, meta, cov


# ── 2. Stock outlook ──────────────────────────────────────────────────────────────────────

def stock_outlook(session, *, symbol: str, now: datetime | None = None):
    now = now or _naive_utc_now()
    stock = session.execute(select(Stock).where(Stock.symbol == symbol)).scalars().first()
    if stock is None:
        raise LookupError(f"{symbol} is not in the universe")

    bars = A.daily_bars(session, stock.id, limit=70)
    fields: dict[str, Field] = {
        "issuer": observed({"symbol": stock.symbol, "name": stock.name,
                            "market": stock.market.value if hasattr(stock.market, "value") else str(stock.market),
                            "currency": stock.currency,
                            "sector": stock.sector or None,
                            "industry": stock.industry or None}),
        "price_as_of": A.price_as_of(bars, now),
        "trend_structure": A.trend_structure(bars),
        "decision_engine_assessment": A.latest_signal(session, stock.id, now),
        "next_catalyst": A.next_earnings_event(session, stock.id, now.date()),
    }
    for n in (1, 5, 20, 63):
        fields[f"return_{n}_sessions"] = A.session_return(bars, n, label=f"{n}-session return")

    market = stock.market.value if hasattr(stock.market, "value") else str(stock.market)
    fields["sector_context"] = A.sector_leadership(session, market)

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
        "headline ingestion exists but is not yet joined to this report with per-item source "
        "times and first-availability")

    fields.update(_outlook_by_horizon(fields["trend_structure"], fields["decision_engine_assessment"]))
    fields["scenarios"] = _scenarios(stock.symbol, fields["trend_structure"])
    fields["execution_status"] = Field(
        value={"status": "information_only",
               "note": "this report is read-only research. Position sizing requires a current "
                       "portfolio snapshot and the existing risk checks, which this path does "
                       "not consult; a constructive report is not an order authorisation."},
        state=FieldState.OK, statement=StatementClass.INTERPRETATION)

    cov = coverage(fields)
    return fields, [], {
        "report_type": ReportType.STOCK_OUTLOOK.value,
        "subject_key": f"stock:{stock.symbol}",
        "market": market,
        "symbol": stock.symbol,
        "status": status_from_coverage(cov).value,
        "policy_version": POLICY_VERSION,
        "cutoff_at": now,
        "fingerprint": input_fingerprint(
            {k: v.value for k, v in fields.items() if v.state is FieldState.OK}),
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

    bars = A.daily_bars(session, stock.id, limit=70)
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
        "pre_event_reference_price": A.price_as_of(bars, now),
        "consensus_eps": actuals["eps_estimate"],
        "consensus_revenue": actuals["revenue_estimate"],
        "consensus_snapshot": unknown(
            "the stored estimate has no snapshot time, contributor count or dispersion, so it "
            "cannot be shown to be the consensus as of this cutoff"),
        "prior_guidance": unavailable("company guidance is not stored"),
        "accounting_basis": actuals["accounting_basis"],
        "run_up_20_sessions": A.session_return(bars, 20, label="20-session run-up"),
        "run_up_5_sessions": A.session_return(bars, 5, label="5-session run-up"),
        "historical_reactions": _historical_reactions(session, stock.id, event.id),
        "options_expected_move": unavailable(
            "an expected move requires an option chain with a post-release expiry and its own "
            "quote timestamps; a straddle premium would be a premium-based proxy, not a "
            "calibrated interval, and none is assembled here"),
        "trend_structure": A.trend_structure(bars),
        "decision_engine_assessment": A.latest_signal(session, stock.id, now),
    }
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

    cov = coverage(fields)
    return fields, [], {
        "report_type": ReportType.PRE_EARNINGS.value,
        "subject_key": f"earnings:{stock.symbol}:{event.report_date.isoformat()}",
        "market": stock.market.value if hasattr(stock.market, "value") else str(stock.market),
        "symbol": stock.symbol,
        "status": status_from_coverage(cov).value,
        "policy_version": POLICY_VERSION,
        "cutoff_at": now,
        "event_id": event.id,
        "fingerprint": input_fingerprint(
            {k: v.value for k, v in fields.items() if v.state is FieldState.OK}),
    }, cov


def _historical_reactions(session, stock_id: int, exclude_event_id: int) -> Field:
    rows = list(session.execute(
        select(EarningsEvent).where(EarningsEvent.stock_id == stock_id,
                                    EarningsEvent.id != exclude_event_id,
                                    EarningsEvent.post_earnings_return_1d.is_not(None))
        .order_by(EarningsEvent.report_date.desc()).limit(12)).scalars().all())
    if not rows:
        return unavailable("no past earnings reactions with a recorded 1-session return")
    vals = sorted(float(r.post_earnings_return_1d) for r in rows)
    mid = vals[len(vals) // 2] if len(vals) % 2 else (vals[len(vals) // 2 - 1] + vals[len(vals) // 2]) / 2
    return calculated(
        {"events": len(vals), "median_1d_pct": round(mid, 2),
         "min_pct": round(vals[0], 2), "max_pct": round(vals[-1], 2),
         "dates": [r.report_date.isoformat() for r in rows],
         "limitation": "same 1-session window for every event; premarket, after-hours and "
                       "intraday baselines are not normalised, and n is small"},
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

    bars = A.daily_bars(session, stock.id, limit=70)
    actuals = A.earnings_actuals(event)
    reaction = A.post_event_reaction(event)

    has_results = any(actuals[k].state is FieldState.OK
                      for k in ("eps_actual", "revenue_actual"))
    stage = EarningsStage.RECONCILED_RESULTS if has_results else EarningsStage.FIRST_FLASH

    fields: dict[str, Field] = {
        "issuer": observed({"symbol": stock.symbol, "name": stock.name}),
        "event_identity": observed({"event_id": f"earnings_event:{event.id}",
                                    "report_date": event.report_date.isoformat()}),
        "fiscal_period": A.fiscal_period(event),
        "stage": observed(stage.value),
        **actuals,
        **reaction,
        "guidance_change": unavailable(
            "company guidance is not stored, so raised/maintained/lowered/withdrawn cannot be "
            "classified from evidence"),
        "management_commentary": (
            observed(event.management_tone, evidence_ids=[f"earnings_event:{event.id}"])
            if getattr(event, "management_tone", None)
            else unavailable("no management commentary is stored for this event")),
        "price_as_of": A.price_as_of(bars, now),
        "trend_structure": A.trend_structure(bars),
        "decision_engine_assessment": A.latest_signal(session, stock.id, now),
        "options_reaction": unavailable(
            "fresh per-leg option quotes are required to state an option outcome; an unchanged "
            "quote after a move is not current P&L, and earnings IV decline can hurt a long "
            "option even on a favourable stock move"),
    }

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
        fields["thesis_verdict"] = _verdict(pre_report, actuals, reaction)

    fields["three_verdicts"] = interpreted({
        "business_result_vs_expectations": "see the surprise table; basis is unverified",
        "forward_outlook": "guidance unavailable, so the forward verdict cannot be formed",
        "market_reaction": (reaction["return_1d"].value
                            if reaction["return_1d"].state is FieldState.OK
                            else "not yet matured"),
        "note": "these are three separate verdicts; a beat, good guidance and a positive "
                "reaction routinely disagree and are not collapsed into one score",
    })

    cov = coverage(fields)
    return fields, [], {
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
        "fingerprint": input_fingerprint(
            {k: v.value for k, v in fields.items() if v.state is FieldState.OK}),
    }, cov


def _verdict(pre_report, actuals, reaction) -> Field:
    """Compare the FROZEN expectations against what happened — never the other way round."""
    frozen = (pre_report.payload or {}).get("fields", {})
    frozen_eps = (frozen.get("consensus_eps") or {}).get("value")
    actual_eps = actuals["eps_actual"].value if actuals["eps_actual"].state is FieldState.OK else None
    if frozen_eps is None or actual_eps is None:
        return unknown(
            "the frozen report carried no EPS expectation, or no actual is on file, so the "
            "thesis cannot be scored")
    beat = actual_eps > frozen_eps
    r1 = reaction["return_1d"].value if reaction["return_1d"].state is FieldState.OK else None
    return Field(
        value={"frozen_consensus_eps": frozen_eps, "actual_eps": actual_eps,
               "result_vs_frozen": "above" if beat else "at or below",
               "first_session_reaction_pct": r1,
               "verdict": ("confirmed" if beat and (r1 or 0) > 0 else
                           "contradicted" if not beat and (r1 or 0) < 0 else
                           "mixed" if r1 is not None else "not_evaluable"),
               "note": "scored against the expectation FROZEN before the release, not against "
                       "a consensus that may have been revised since"},
        state=FieldState.OK, statement=StatementClass.DETERMINISTIC_CALCULATION)
