"""Earnings Intelligence — yfinance earnings history + upcoming calendar."""
from __future__ import annotations

import asyncio
import json
import re
from datetime import date, datetime, timezone, timedelta
from concurrent.futures import ThreadPoolExecutor

import httpx
import pandas as pd
import structlog
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from common.config import get_settings
from db import get_session, SessionLocal, EarningsEvent, Stock, Price, TimeFrame

log = structlog.get_logger()
_settings = get_settings()
_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="yf_earnings")

# T249-EARNINGS-LLM-IMPACT: unlike macro_reaction.py's blocking httpx.get()/feedparser calls
# (which needed a dedicated executor — AUD-EI-MACRO-REACTION-BLOCKING), generate_earnings_
# impact() below uses httpx.AsyncClient, which is natively async and does not block the
# shared event loop — no ThreadPoolExecutor needed here.
_REDIS_EARNINGS_LLM_ENABLED = "stockai:admin:feature:earnings_llm_impact_enabled"

# AUD-EARNINGSFORECAST: PRE-report forecast (generate_earnings_impact above is POST-report —
# a genuinely different feature, triggered on-demand by a user click rather than a scheduled
# poll). Cost-minimal by design (the user explicitly asked to keep this cheap): one combined
# Claude call produces the narrative AND the tailored scenario table together (no second call),
# cached per (symbol, report_date) until the report itself happens — a forecast for a report
# 3 days out doesn't need to be regenerated on every page view. Default OFF, matching every
# other opt-in Claude feature added since the CLAUDE-API-COST-AUDIT incident.
_REDIS_EARNINGS_FORECAST_ENABLED = "stockai:admin:feature:earnings_llm_forecast_enabled"
_FORECAST_CACHE_TTL_S = 24 * 3600  # one real regen per symbol per day at most, even if a user
# reopens the modal repeatedly — the underlying consensus data itself only meaningfully shifts
# on this cadence (analyst revisions are tracked in 7/30/90-day windows, never intraday).

_FORECAST_SYSTEM = """You are an equity analyst producing a brief PRE-earnings forecast read
for a retail trading app, shown when a user clicks an upcoming earnings event. You will
receive a company's ticker, sector, days until the report, the real analyst consensus (EPS/
revenue estimate + range, analyst count, growth), a 7/30/90-day revision trend (are analysts
raising or lowering estimates lately), this stock's own history of beating/missing estimates,
its projected growth vs. the broader market index, and — when available — this stock's own
REAL, MEASURED price reaction (1-day and 5-day % move) to its last few reports, alongside each
report's own surprise %. Respond ONLY with valid JSON (no markdown, no explanation outside
JSON) in this exact format:
{"watching_for":"<2-3 sentences>","scenarios":[{"scenario":"Beat + Raise","interpretation":"...","typical_reaction":"..."},{"scenario":"In-Line","interpretation":"...","typical_reaction":"..."},{"scenario":"Miss or Cut","interpretation":"...","typical_reaction":"..."}],"bellwether_note":"<1-2 sentences or empty string>"}
watching_for: 2-3 plain-English sentences on what the market is specifically watching for in
THIS report, grounded in the real revision trend and beat history you were given (e.g. "analysts
have raised estimates 5 times in the last 30 days with zero cuts — the bar is already set high").
Never invent a number you were not given; if the data is thin, say so plainly instead of
padding with generic language.
scenarios: EXACTLY 3 rows, always in this order: "Beat + Raise", "In-Line", "Miss or Cut".
interpretation is one short clause on what that outcome would signal about the business (e.g.
"demand still accelerating" / "growth cooling to expectations" / "guidance concerns validated").
typical_reaction is a SHORT statement of how markets tend to react to that class of outcome.
When this stock's own real past-reaction history is available AND genuinely fits the specific
scenario row (e.g. a real prior beat's real 1-day move, for the "Beat + Raise" row), ground the
language in that real, measured history instead of a generic claim (e.g. "in its last 2 beats
this stock moved +6% and +9% the next day" rather than "often rallies"). If the real history
does NOT support a given scenario (e.g. no real past misses to draw on for "Miss or Cut"), fall
back to general, historically-grounded market-pattern education instead — this is ALWAYS a
description of how this CLASS of outcome has played out before (for this stock specifically
when the data supports it, or the market broadly otherwise), NEVER a prediction of what THIS
upcoming report's own outcome or price move will be. Do not claim certainty or give a
percentage/target price for the report that hasn't happened yet.
bellwether_note: ONLY if this stock's growth estimate is genuinely a notable outlier vs. the
index growth estimate you were given (meaningfully faster or slower), 1-2 sentences on the
read-through risk to its sector/peers if the print confirms or breaks that trend. Empty string
if the comparison isn't notable — never fabricate a read-through that isn't there."""


def _clean_scenarios(raw: object) -> list[dict] | None:
    """A malformed/incomplete scenario list must never take down the whole forecast — this
    feature's entire value is the tailored table, so an invalid one degrades the WHOLE
    forecast to None (unlike _clean_sector_list's partial-degrade convention) rather than
    silently showing a broken or incomplete table."""
    if not isinstance(raw, list) or len(raw) != 3:
        return None
    cleaned = []
    for row in raw:
        if not isinstance(row, dict):
            return None
        scenario = str(row.get("scenario") or "").strip()
        interpretation = str(row.get("interpretation") or "").strip()
        typical_reaction = str(row.get("typical_reaction") or "").strip()
        if not (scenario and interpretation and typical_reaction):
            return None
        cleaned.append({
            "scenario": scenario[:40],
            "interpretation": interpretation[:200],
            "typical_reaction": typical_reaction[:300],
        })
    return cleaned

_IMPACT_SYSTEM = """You are an equity analyst producing a brief post-earnings impact read for a
retail trading app. You will receive a company's ticker, sector, EPS actual vs. estimate,
revenue actual vs. estimate, surprise percentages, and a computed earnings strength score
(0-100). You may ALSO receive a set of real excerpts from the company's own earnings call
transcript (management/analyst statements) — when present, use them to inform your read;
when absent, base your read on the numeric data alone exactly as before. Respond ONLY with
valid JSON (no markdown, no explanation outside JSON) in this exact format:
{"one_paragraph":"<2-3 sentences>","sectors_helped":["Technology"],"sectors_hurt":["Utilities"],"management_tone":"<1-2 sentences or empty string>"}
one_paragraph must be 2-3 plain-English sentences a retail trader can act on, max 400 chars —
cover what the beat/miss means and any read-through risk (e.g. a weak print from a bellwether
can pressure its whole sector/peers, a strong print can lift them).
sectors_helped and sectors_hurt: 0-4 GICS-style sector names each (e.g. "Technology",
"Financials", "Energy", "Utilities", "Consumer Discretionary", "Healthcare", "Industrials",
"Materials", "Real Estate", "Communication Services", "Consumer Staples") that this specific
report plausibly helps or hurts — almost always includes the company's OWN sector, plus any
closely-related peer sector if there's a real read-through (e.g. a major chipmaker's earnings
affecting the broader semiconductor/tech supply chain). Use empty lists if you have no concrete
basis — never pad these lists to look complete.
management_tone: ONLY fill this in when real transcript excerpts were provided — a genuinely
qualitative read the numbers alone cannot give (e.g. did management sound confident about
guidance, defensive about a miss, or notably vague/evasive on a specific topic an analyst
pressed on). Ground it in the ACTUAL WORDS given, never invent a tone the excerpts don't
support. Empty string if no transcript excerpts were provided, or if the excerpts genuinely
don't support a clear read either way — never pad this to look complete."""


def _api_key() -> str:
    """AUD-DUPLOGIC: delegates to common.ai_keys.get_admin_ai_key(), matching every other
    Claude call site in this codebase (macro_reaction.py, decision-engine's llm_scorer.py/
    risk_agent.py, market-data's news.py, research-engine)."""
    from common.ai_keys import get_admin_ai_key
    return get_admin_ai_key("claude")


def _clean_sector_list(raw: object) -> list[str]:
    """Same validation as macro_reaction.py's own _clean_sector_list() — a bad/malformed
    sector list must never take down the reaction_text/impact_text it's paired with."""
    if not isinstance(raw, list):
        return []
    cleaned = [str(s).strip() for s in raw if isinstance(s, str) and str(s).strip()]
    return cleaned[:6]


_TRANSCRIPT_EXCERPT_MAX_STATEMENTS = 20  # AUD-TRANSCRIPT: a full call transcript can run to
# hundreds of statements — capping keeps the prompt a bounded, predictable size/cost regardless
# of how long a given call ran, at the cost of only seeing a slice of the full call rather than
# the complete transcript. Prefers the highest-|sentiment| statements (see _select_transcript_
# excerpts() below) since those are the ones most likely to carry a genuine tone signal.
_TRANSCRIPT_EXCERPT_MAX_CHARS = 4000  # a further hard cap on total excerpt text even if 20
# statements happen to be unusually long — bounds real Claude input-token cost per report.


def _select_transcript_excerpts(statements: list[dict]) -> list[dict]:
    """AUD-TRANSCRIPT: picks a bounded, representative slice of a full transcript to actually
    send to the LLM — sorted by |sentiment| descending (UW's own per-statement score) so the
    excerpts sent are the ones most likely to carry a genuine, gradeable tone signal, rather
    than an arbitrary first-N slice that could land entirely on procedural/introductory remarks.
    Statements with no real content or no sentiment score are dropped first (nothing to select
    on, nothing useful to send)."""
    scored = [
        s for s in statements
        if isinstance(s, dict) and s.get("content") and s.get("sentiment") is not None
    ]
    scored.sort(key=lambda s: abs(s["sentiment"]), reverse=True)
    selected = scored[:_TRANSCRIPT_EXCERPT_MAX_STATEMENTS]

    result = []
    total_chars = 0
    for s in selected:
        content = str(s["content"])[:400]
        if total_chars + len(content) > _TRANSCRIPT_EXCERPT_MAX_CHARS:
            break
        result.append({"speaker": s.get("speaker") or "Unknown", "title": s.get("title"), "content": content})
        total_chars += len(content)
    return result


async def generate_earnings_impact(
    symbol: str, sector: str | None, eps_actual: float, eps_estimate: float | None,
    surprise_pct: float | None, revenue_actual: float | None, revenue_estimate: float | None,
    revenue_surprise_pct: float | None, strength_score: float | None,
    transcript_statements: list[dict] | None = None,
) -> dict | None:
    """LLM-generated earnings impact read — mirrors macro_reaction.py's generate_reaction()
    exactly (same model, same fail-open contract, same sector-impact structure), applied to a
    just-reported earnings print instead of a macro release. Fail-open: returns None on any
    error, matching every other LLM call site in this codebase — a missing reaction just means
    no impact text is available that cycle, never a broken page or a blocked write of the
    already-computed eps_actual/surprise_pct fields.

    AUD-TRANSCRIPT: `transcript_statements` is OPTIONAL and defaults to None — every pre-existing
    caller is unaffected, and the numeric-only prompt/response shape is byte-identical to before
    this change when omitted. When real transcript statements ARE provided (from Unusual Whales,
    requires its own Advanced+ tier — see get_earnings_transcript()'s own docstring for why this
    will often be empty), a bounded, sentiment-ranked excerpt (see _select_transcript_excerpts())
    is folded into the SAME prompt/call — no second LLM call, no extra cost when a transcript
    isn't available. Response gains a `management_tone` field, empty string when no excerpts
    were provided or the LLM found no clear tone signal in them.
    """
    api_key = _api_key()
    if not api_key:
        log.info("earnings_impact.no_api_key", symbol=symbol)
        return None

    excerpts = _select_transcript_excerpts(transcript_statements) if transcript_statements else []
    transcript_block = ""
    if excerpts:
        lines = [f'  [{e["title"] or "Unknown role"}] {e["speaker"]}: "{e["content"]}"' for e in excerpts]
        transcript_block = "\nReal earnings call transcript excerpts (sentiment-ranked, may not be in speaking order):\n" + "\n".join(lines) + "\n"

    prompt = (
        f"Ticker: {symbol}\n"
        f"Sector: {sector or 'unknown'}\n"
        f"EPS actual: {eps_actual}\n"
        f"EPS estimate: {eps_estimate if eps_estimate is not None else 'unavailable'}\n"
        f"EPS surprise: {f'{surprise_pct:+.1f}%' if surprise_pct is not None else 'unavailable'}\n"
        f"Revenue actual: {revenue_actual if revenue_actual is not None else 'unavailable'}\n"
        f"Revenue estimate: {revenue_estimate if revenue_estimate is not None else 'unavailable'}\n"
        f"Revenue surprise: {f'{revenue_surprise_pct:+.1f}%' if revenue_surprise_pct is not None else 'unavailable'}\n"
        f"Earnings strength score (0-100): {strength_score if strength_score is not None else 'unavailable'}\n"
        f"{transcript_block}"
    )
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 350,
        "temperature": 0.2,
        "system": _IMPACT_SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
        if r.status_code != 200:
            log.warning("earnings_impact.api_error", symbol=symbol, status=r.status_code, body=r.text[:200])
            return None
        raw = r.json()["content"][0]["text"].strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
        data = json.loads(raw)
        impact_text = (data.get("one_paragraph") or "")[:500] or None
        if impact_text is None:
            return None
        return {
            "impact_text": impact_text,
            "sectors_helped": _clean_sector_list(data.get("sectors_helped")),
            "sectors_hurt": _clean_sector_list(data.get("sectors_hurt")),
            "management_tone": (str(data.get("management_tone") or "").strip()[:400]) or None,
        }
    except Exception as exc:
        log.warning("earnings_impact.call_failed", symbol=symbol, error=str(exc))
        return None


def _nearest_forecast_period(consensus: dict | None) -> tuple[str, dict] | None:
    """The consensus/growth blobs are keyed by relative period ("0q"/"+1q"/"0y"/"+1y") — the
    upcoming report this forecast is FOR is always the current-quarter figure, "0q". A thin-
    coverage symbol missing that specific period has no real basis for a forecast at all."""
    if not consensus or "0q" not in consensus:
        return None
    return "0q", consensus["0q"]


def _fetch_fundamentals_sync(symbol: str) -> dict | None:
    """Sync (blocking) fetch of market-data's already-24h-cached fundamentals blob — reuses
    the SAME earnings_consensus/growth_vs_index data the stock detail page already shows,
    rather than a second yfinance fetch. Must run inside _executor (see generate_earnings_
    forecast's own call site) — a bare sync httpx.get() here would block the shared event
    loop for every other concurrent request this service is serving, the exact class of bug
    already fixed once in macro_reaction.py under AUD-EI-MACRO-REACTION-BLOCKING."""
    try:
        r = httpx.get(f"{_settings.market_data_url}/stocks/{symbol}/fundamentals", timeout=15)
        if r.status_code == 200:
            return r.json()
    except Exception as exc:
        log.warning("earnings_forecast.fundamentals_fetch_failed", symbol=symbol, error=str(exc))
    return None


def _fetch_transcript_statements_sync(symbol: str, report_date: date) -> list[dict] | None:
    """AUD-TRANSCRIPT: sync (blocking) fetch of market-data's real earnings-call transcript for
    this specific report — market-data owns the Unusual Whales client (unusual_whales.py) and
    event-intelligence has no direct Python import path to it (separate services/containers),
    so this is a real cross-service HTTP call, matching _fetch_fundamentals_sync()'s own
    established pattern exactly. Must run inside _executor (see check_earnings_reactions()'s
    own call site) for the same blocking-event-loop reason as every other sync call here.
    Returns None (not []) when unavailable/no data — a real, empty transcript is a genuinely
    different state generate_earnings_impact() distinguishes (an empty list still means "no
    excerpts to fold in," but None here specifically means "the fetch itself never got a real
    answer," useful for log/debug clarity even though both currently degrade the same way
    downstream)."""
    try:
        r = httpx.get(
            f"{_settings.market_data_url}/stocks/{symbol}/earnings-transcript",
            params={"report_date": report_date.isoformat()}, timeout=15,
        )
        if r.status_code == 200:
            body = r.json()
            if body.get("available"):
                return body.get("statements") or []
    except Exception as exc:
        log.warning("earnings_impact.transcript_fetch_failed", symbol=symbol, error=str(exc))
    return None


def _fetch_past_reactions_sync(symbol: str, limit: int = 4) -> list[dict]:
    """AUD-EARNINGSFORECAST-EXTEND: this stock's own real, MEASURED past-earnings reactions
    (post_earnings_return_1d/_5d, populated by backfill_post_earnings_returns() below) — a
    direct DB read (event-intelligence has direct access to the shared Price/Stock/
    EarningsEvent models, a genuinely cheaper path than _fetch_fundamentals_sync()'s own HTTP
    round-trip). A sync, blocking SQLAlchemy call — must run inside _executor exactly like
    _fetch_fundamentals_sync() above (see generate_earnings_forecast's own call site)."""
    try:
        with SessionLocal() as s:
            stock_id = s.execute(select(Stock.id).where(Stock.symbol == symbol)).scalar()
            if stock_id is None:
                return []
            rows = s.execute(
                select(EarningsEvent.report_date, EarningsEvent.surprise_pct,
                       EarningsEvent.post_earnings_return_1d, EarningsEvent.post_earnings_return_5d)
                .where(
                    EarningsEvent.stock_id == stock_id,
                    EarningsEvent.post_earnings_return_1d.isnot(None),
                )
                .order_by(EarningsEvent.report_date.desc())
                .limit(limit)
            ).all()
            return [
                {"report_date": rd.isoformat(), "surprise_pct": sp, "return_1d": r1, "return_5d": r5}
                for rd, sp, r1, r5 in rows
            ]
    except Exception as exc:
        log.warning("earnings_forecast.past_reactions_fetch_failed", symbol=symbol, error=str(exc))
        return []


async def generate_earnings_forecast(symbol: str, sector: str | None, days_to_event: int) -> dict | None:
    """PRE-report forecast — the genuinely on-demand (user-clicked, not scheduled-poll) sibling
    of generate_earnings_impact() above. Fail-open: returns None on any error (missing flag, no
    API key, thin data, a failed Claude call) — the frontend modal shows the real consensus
    data it already has either way, this is purely an LLM-generated ADDITION on top of it, never
    a blocking dependency."""
    # AUD-EARNINGSFORECAST: the lazy import itself must live INSIDE the try — matching
    # check_earnings_impact_poll()'s own established shape exactly, not just its "lazy import"
    # comment. A genuinely broken/missing common.redis_client module must fail open the same
    # way a real Redis connection error does, not crash this whole function.
    try:
        from common.redis_client import get_redis
        if get_redis().get(_REDIS_EARNINGS_FORECAST_ENABLED) != "1":
            return None
    except Exception:
        return None

    api_key = _api_key()
    if not api_key:
        return None

    cache_key = f"stockai:earnings_forecast:{symbol.upper()}"
    try:
        cached = get_redis().get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass  # a corrupted/unparseable cache entry must never block a fresh regeneration

    loop = asyncio.get_running_loop()
    fundamentals = await loop.run_in_executor(_executor, _fetch_fundamentals_sync, symbol)
    if not fundamentals:
        return None

    consensus_period = _nearest_forecast_period(fundamentals.get("earnings_consensus"))
    if consensus_period is None:
        return None  # no real basis for a forecast — never fabricate one from nothing
    _, consensus = consensus_period
    growth_period = _nearest_forecast_period(fundamentals.get("growth_vs_index"))
    growth = growth_period[1] if growth_period else {}
    past_reactions = await loop.run_in_executor(_executor, _fetch_past_reactions_sync, symbol)

    def _fmt(v, suffix=""):
        return f"{v}{suffix}" if v is not None else "unavailable"

    def _fmt_pct(v):
        return f"{v * 100:+.1f}%" if v is not None else "unavailable"

    prompt = (
        f"Ticker: {symbol}\n"
        f"Sector: {sector or 'unknown'}\n"
        f"Days until report: {days_to_event}\n"
        f"EPS consensus estimate: {_fmt(consensus.get('eps_avg'))} "
        f"(range {_fmt(consensus.get('eps_low'))}-{_fmt(consensus.get('eps_high'))}, "
        f"{_fmt(consensus.get('number_of_analysts'))} analysts)\n"
        f"Revenue consensus estimate: {_fmt(consensus.get('revenue_avg'))} "
        f"(range {_fmt(consensus.get('revenue_low'))}-{_fmt(consensus.get('revenue_high'))})\n"
        f"EPS revision trend (current vs. 7/30/90 days ago): "
        f"{_fmt(consensus.get('eps_trend_current'))} vs. "
        f"{_fmt(consensus.get('eps_trend_7d_ago'))} / {_fmt(consensus.get('eps_trend_30d_ago'))} / "
        f"{_fmt(consensus.get('eps_trend_90d_ago'))}\n"
        f"Analyst revisions last 30 days: {_fmt(consensus.get('revisions_up_30d'))} up, "
        f"{_fmt(consensus.get('revisions_down_30d'))} down\n"
        f"This stock's own beat-rate history: "
        f"{_fmt_pct(fundamentals.get('eps_beat_rate'))} beat rate, "
        f"avg surprise {_fmt_pct(fundamentals.get('eps_avg_surprise_pct'))}\n"
        f"Projected growth — this stock: {_fmt_pct(growth.get('stock_growth'))} "
        f"vs. broader index: {_fmt_pct(growth.get('index_growth'))}\n"
    )
    if past_reactions:
        reactions_lines = "\n".join(
            f"  {pr['report_date']}: surprise {_fmt_pct(pr['surprise_pct'])}, "
            f"1-day move {_fmt_pct(pr['return_1d'])}, 5-day move {_fmt_pct(pr['return_5d'])}"
            for pr in past_reactions
        )
        reactions_block = f"This stock's own real past-earnings reactions (most recent first):\n{reactions_lines}\n"
    else:
        reactions_block = "This stock's own past-earnings reaction history: unavailable\n"
    prompt += reactions_block
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 500,
        "temperature": 0.2,
        "system": _FORECAST_SYSTEM,
        "messages": [{"role": "user", "content": prompt}],
    }
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    try:
        async with httpx.AsyncClient(timeout=15) as client:
            r = await client.post("https://api.anthropic.com/v1/messages", headers=headers, json=body)
        if r.status_code != 200:
            log.warning("earnings_forecast.api_error", symbol=symbol, status=r.status_code, body=r.text[:200])
            return None
        raw = r.json()["content"][0]["text"].strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
        data = json.loads(raw)
        watching_for = (data.get("watching_for") or "")[:500] or None
        scenarios = _clean_scenarios(data.get("scenarios"))
        if watching_for is None or scenarios is None:
            return None
        result = {
            "watching_for": watching_for,
            "scenarios": scenarios,
            "bellwether_note": (str(data.get("bellwether_note") or "").strip())[:400] or None,
            "past_reactions": past_reactions,
            "generated_at": datetime.now(timezone.utc).isoformat(),
        }
        try:
            get_redis().setex(cache_key, _FORECAST_CACHE_TTL_S, json.dumps(result))
        except Exception:
            pass  # cache-write failure must never block returning the real, already-computed result
        return result
    except Exception as exc:
        log.warning("earnings_forecast.call_failed", symbol=symbol, error=str(exc))
        return None


async def check_earnings_impact_poll() -> dict:
    """Detection half of the earnings LLM impact feature (delivery is market-data's
    check_earnings_reactions(), same detect-in-event-intelligence/deliver-in-market-data split
    already established for check_release_day_fast_poll()/check_macro_reaction_alerts()).

    Unlike macro's release-day-armed poll (which knows exactly which minute a release is due),
    earnings land unpredictably per company throughout the trading day/after-hours — so this
    simply scans for EarningsEvent rows where eps_actual has landed (via sync_all_earnings()'s
    existing daily sync) but impact_text hasn't been generated yet. Gated behind the
    earnings_llm_impact_enabled admin flag (default OFF, matching every other opt-in
    Claude-calling feature added since the CLAUDE-API-COST-AUDIT) — checked FIRST, before any
    DB query, so a disabled flag costs nothing.
    """
    try:
        from common.redis_client import get_redis
        if get_redis().get(_REDIS_EARNINGS_LLM_ENABLED) != "1":
            return {"checked": 0, "generated": 0, "skipped": "feature_disabled"}
    except Exception:
        return {"checked": 0, "generated": 0, "skipped": "feature_disabled"}

    cutoff = date.today() - timedelta(days=2)
    generated = 0
    with SessionLocal() as s:
        rows = s.execute(
            select(EarningsEvent, Stock.symbol, Stock.sector)
            .join(Stock, EarningsEvent.stock_id == Stock.id)
            .where(
                EarningsEvent.report_date >= cutoff,
                EarningsEvent.eps_actual.isnot(None),
                EarningsEvent.impact_text.is_(None),
            )
        ).all()
        checked = len(rows)
        loop = asyncio.get_event_loop()
        for ev, sym, sector in rows:
            try:
                # AUD-TRANSCRIPT: best-effort — a fetch failure/no-data (the common case until/
                # unless an Advanced+ UW subscription is active) degrades to None, and
                # generate_earnings_impact() itself treats a None/empty transcript exactly like
                # its own pre-existing numeric-only behavior. Never blocks the impact text.
                transcript_statements = await loop.run_in_executor(
                    _executor, _fetch_transcript_statements_sync, sym, ev.report_date,
                )
                impact = await generate_earnings_impact(
                    sym, sector, ev.eps_actual, ev.eps_estimate, ev.surprise_pct,
                    ev.revenue_actual, ev.revenue_estimate, ev.revenue_surprise_pct,
                    ev.earnings_strength_score, transcript_statements,
                )
                if impact is None:
                    continue
                ev.impact_text = impact["impact_text"]
                ev.sectors_helped = json.dumps(impact["sectors_helped"])
                ev.sectors_hurt = json.dumps(impact["sectors_hurt"])
                ev.management_tone = impact.get("management_tone")
                ev.impact_generated_at = datetime.now(timezone.utc)
                s.commit()
                generated += 1
                log.info("earnings_impact.generated", symbol=sym, had_transcript=bool(transcript_statements))
            except Exception as exc:
                log.warning("earnings_impact.poll_error", symbol=sym, error=str(exc))

    return {"checked": checked, "generated": generated, "skipped": None}


def _compute_post_earnings_returns(bars: list[tuple[date, float]], report_date: date) -> tuple[float | None, float | None]:
    """AUD-EARNINGSFORECAST-EXTEND: pure, dependency-free bar-index math — given a symbol's own
    sorted (date, close) daily bars, returns (return_1d, return_5d), the % change from the last
    close STRICTLY BEFORE report_date to the close 1 and 5 TRADING DAYS after report_date's own
    bar. Bar-index-based, never a calendar-day offset (matching gate_harness.py's own T196
    convention — a fixed number of trading days correctly skips weekends/holidays, a calendar-
    day offset does not). Baseline is the last close BEFORE report_date, not report_date's own
    close, so the measurement is consistent regardless of whether the report was released
    before market open (BMO) or after close (AMC). Returns (None, None) rather than a partial/
    guessed value when there isn't enough real bar history on either side yet — never fabricate
    a reaction from data that doesn't exist."""
    before = [(d, c) for d, c in bars if d < report_date]
    after = [(d, c) for d, c in bars if d >= report_date]
    if not before or not after:
        return None, None
    baseline = before[-1][1]
    if baseline == 0:
        return None, None
    ret_1d = (after[1][1] / baseline - 1) if len(after) >= 2 else None
    ret_5d = (after[5][1] / baseline - 1) if len(after) >= 6 else None
    return ret_1d, ret_5d


async def backfill_post_earnings_returns() -> dict:
    """Populates EarningsEvent.post_earnings_return_1d/_5d — real columns that have been
    DEFINED but never written by any job in this codebase (confirmed via grep before deciding
    to build this; see the CLAUDE.md entry this closes for the earlier, deliberate deferral).
    A genuinely separate job from check_earnings_impact_poll() above: that one fires the moment
    eps_actual lands (same day), but a 5-trading-day-later return can't be measured until 5
    real trading days have actually elapsed — hence its own daily cron, not a tighter interval.

    Reuses Price directly (a shared model this service's own DB connection already has access
    to) rather than an HTTP round-trip to market-data — a genuinely cheaper path than
    generate_earnings_forecast()'s own _fetch_fundamentals_sync(), since this data lives in the
    SAME database this service is already connected to.
    """
    cutoff = date.today() - timedelta(days=45)  # bound the scan — older unbackfilled rows are
    # a genuine, if rare, gap (e.g. this job didn't exist yet when they reported) rather than a
    # target for indefinite reprocessing; a 45-day window comfortably covers the 5-trading-day
    # minimum plus real-world scheduling slack.
    filled = 0
    with SessionLocal() as s:
        rows = s.execute(
            select(EarningsEvent, Stock.id)
            .join(Stock, EarningsEvent.stock_id == Stock.id)
            .where(
                EarningsEvent.report_date >= cutoff,
                EarningsEvent.eps_actual.isnot(None),
                EarningsEvent.post_earnings_return_1d.is_(None),
            )
        ).all()
        checked = len(rows)
        for ev, stock_id in rows:
            try:
                price_rows = s.execute(
                    select(Price.ts, Price.close)
                    .where(
                        Price.stock_id == stock_id,
                        Price.timeframe == TimeFrame.D1,
                        Price.ts >= datetime.combine(ev.report_date - timedelta(days=20), datetime.min.time()),
                    )
                    .order_by(Price.ts)
                ).all()
                bars = [(p.ts.date() if hasattr(p.ts, "date") else p.ts, float(p.close)) for p in price_rows]
                ret_1d, ret_5d = _compute_post_earnings_returns(bars, ev.report_date)
                if ret_1d is None and ret_5d is None:
                    continue  # not enough real bars yet — leave NULL, try again next run
                ev.post_earnings_return_1d = ret_1d
                ev.post_earnings_return_5d = ret_5d
                s.commit()
                filled += 1
            except Exception as exc:
                log.warning("earnings_returns.backfill_error", stock_id=stock_id, error=str(exc))

    return {"checked": checked, "filled": filled}


def _match_report_dates_to_history(
    hist_rows: list[dict], announce_rows: list[dict]
) -> dict[str, date]:
    """AUD264-EARNINGS-FISCAL-QUARTER-FROM-ANNOUNCEMENT-MONTH: ticker.earnings_history's own
    index is the fiscal PERIOD-END date (labeled "quarter" by yfinance itself), NOT the real
    announcement date — confirmed live: AAPL's history shows an index entry of 2025-09-30 for
    the report whose real announcement (per ticker.earnings_dates, a separate property) landed
    2025-10-30, a full month later. The old code stored this period-end date directly into
    report_date, which every consumer in this codebase (is_upcoming, day-of-earnings matching,
    get_days_to_earnings, dedup keys) treats as the real announcement date — silently wrong for
    every historical row, not just future ones.

    earnings_history has no announcement-date field of its own, but earnings_dates does (its
    own index) — the two are joined here by matching on the reported EPS value (rounded to 2dp,
    which both sources report to), since that's the only value genuinely shared between them
    and confirmed to match exactly for the same real event. Returns {period_end_iso:
    real_announcement_date} for every period-end date a match was found for; a period-end with
    no matching announce row (a data gap on one side) is simply absent from the returned dict —
    the caller falls back to the period-end date itself rather than fabricating one.
    """
    by_eps: dict[float, date] = {}
    for row in announce_rows:
        eps_act = row.get("eps_actual")
        announce_date = row.get("announce_date")
        if eps_act is None or announce_date is None:
            continue
        by_eps.setdefault(round(eps_act, 2), announce_date)

    matched: dict[str, date] = {}
    for row in hist_rows:
        period_end = row.get("period_end")
        eps_act = row.get("eps_actual")
        if period_end is None or eps_act is None:
            continue
        real_date = by_eps.get(round(eps_act, 2))
        if real_date is not None:
            matched[period_end.isoformat()] = real_date
    return matched


def _fetch_earnings_for_symbol(symbol: str, stock_id: int) -> int:
    """Fetch earnings history + calendar from yfinance and upsert to DB. Returns rows upserted."""
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        upserted = 0

        # Historical earnings (EPS beats)
        try:
            hist = ticker.earnings_history
            if hist is not None and not hist.empty:
                # AUD264-EARNINGS-FISCAL-QUARTER-FROM-ANNOUNCEMENT-MONTH: fetch earnings_dates
                # too so real announcement dates (see _match_report_dates_to_history's own
                # docstring) can be joined in — best-effort, a failure here just means every
                # row falls back to the period-end date (the pre-fix behavior), not a hard stop.
                real_dates_by_period_end: dict[str, date] = {}
                try:
                    ed = ticker.earnings_dates
                    if ed is not None and not ed.empty:
                        hist_rows_for_match = []
                        for idx, row in hist.iterrows():
                            pe = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
                            ea = row.get("epsActual")
                            hist_rows_for_match.append({
                                "period_end": pe,
                                "eps_actual": float(ea) if pd.notna(ea) else None,
                            })
                        announce_rows = []
                        for idx, row in ed.iterrows():
                            ad = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
                            ea = row.get("Reported EPS")
                            announce_rows.append({
                                "announce_date": ad,
                                "eps_actual": float(ea) if pd.notna(ea) else None,
                            })
                        real_dates_by_period_end = _match_report_dates_to_history(hist_rows_for_match, announce_rows)
                except Exception as exc:
                    log.debug("earnings.earnings_dates_join_skip", symbol=symbol, error=str(exc))

                # AUD-EARNINGS-REVENUEACTUAL: revenue_actual/revenue_surprise_pct are real
                # columns on EarningsEvent, read by generate_earnings_impact()'s LLM prompt and
                # returned to the frontend via _row_to_dict()'s actual_revenue field — but until
                # this fix, NOTHING in this file ever wrote either one. ticker.earnings_history
                # (the loop below) only carries EPS fields; ticker.calendar's "Revenue Estimate"
                # (written elsewhere in this function) is a forward-looking, pre-report figure
                # that can never carry an actual by construction. Real historical revenue lives
                # in ticker.quarterly_financials's own "Total Revenue" row, indexed by the SAME
                # period-end dates ticker.earnings_history uses (confirmed directly against a
                # real yfinance response before writing this) — one extra fetch, best-effort,
                # same fail-open convention as the earnings_dates join right above.
                revenue_actual_by_period_end: dict[str, float] = {}
                try:
                    qf = ticker.quarterly_financials
                    if qf is not None and not qf.empty and "Total Revenue" in qf.index:
                        for col, val in qf.loc["Total Revenue"].items():
                            if pd.notna(val):
                                pe = col.date() if hasattr(col, "date") else date.fromisoformat(str(col)[:10])
                                revenue_actual_by_period_end[pe.isoformat()] = float(val)
                except Exception as exc:
                    log.debug("earnings.revenue_actual_join_skip", symbol=symbol, error=str(exc))

                with SessionLocal() as s:
                    for idx, row in hist.iterrows():
                        try:
                            period_end = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
                            eps_est = row.get("epsEstimate") if pd.notna(row.get("epsEstimate")) else None
                            eps_act = row.get("epsActual") if pd.notna(row.get("epsActual")) else None
                            surprise = _compute_surprise_pct(eps_est, eps_act)
                            rev_act = revenue_actual_by_period_end.get(period_end.isoformat())
                            # AUD264: prefer the real, joined announcement date; fall back to the
                            # period-end date (the pre-fix value) only when no match was found —
                            # still better than nothing, and matches this function's own existing
                            # fail-open convention throughout.
                            report_date = real_dates_by_period_end.get(period_end.isoformat(), period_end)
                            # Fiscal quarter/year are still a best-effort calendar-month label
                            # (non-calendar-fiscal-year companies, e.g. AAPL's Sep year-end,
                            # aren't exactly representable this way) — but they're read-only
                            # display fields with zero downstream logic depending on their exact
                            # value (see the AUD264 fix note on the uniqueness constraint below).
                            fq = (period_end.month - 1) // 3 + 1
                            fy = period_end.year
                            strength = _compute_strength(eps_est, eps_act, surprise)
                            # DQ-EARNINGS-FETCHED-AT-FROZEN: fetched_at has server_default=func.now(),
                            # which only fires on a fresh INSERT — once every (stock_id, period) row
                            # already exists from the initial backfill, every subsequent daily sync run
                            # is 100% UPDATEs via the conflict path, so fetched_at could never advance
                            # again no matter how many times the sync ran cleanly. This silently broke
                            # the earnings_events DQ staleness check (AUD232-DQ-MISSING-CHECKS) — it
                            # alerted "stale" every day despite a genuinely healthy daily sync, because
                            # the column it watches was structurally frozen. Set it explicitly on every
                            # upsert so it reflects "last time this row was actually touched," not
                            # "first time this row ever existed."
                            _now = datetime.now(timezone.utc)
                            # AUD264: the calendar path (below) may have already written a
                            # PENDING row (eps_actual IS NULL) for this exact event under its
                            # own earlier, projected report_date — which can genuinely differ
                            # from the real, joined announcement date resolved above. A bare
                            # ON CONFLICT (stock_id, report_date) would miss that row entirely
                            # (different dates) and insert a second, duplicate row for the same
                            # event. Find and update any such pending row in place first, same
                            # reasoning as the calendar path's own existing_pending handling.
                            existing_pending = s.execute(
                                select(EarningsEvent).where(
                                    EarningsEvent.stock_id == stock_id,
                                    EarningsEvent.eps_actual.is_(None),
                                )
                            ).scalars().first()
                            if existing_pending is not None:
                                # revenue_estimate was already set by the earlier calendar-path
                                # write that created this pending row (see the calendar block
                                # below) — reuse it here to compute the surprise, matching the
                                # EPS surprise computation above exactly. A row that was never
                                # seen by the calendar path first (rare — a report syncing before
                                # its own pending calendar entry ever ran) has revenue_estimate
                                # still None, so rev_surprise correctly stays None too, same as
                                # the pre-existing eps `surprise is None` fallback above.
                                rev_surprise = _compute_surprise_pct(existing_pending.revenue_estimate, rev_act)
                                existing_pending.report_date = report_date
                                existing_pending.period = f"Q{fq} {fy}"
                                existing_pending.fiscal_year = fy
                                existing_pending.fiscal_quarter = fq
                                existing_pending.eps_estimate = eps_est
                                existing_pending.eps_actual = eps_act
                                existing_pending.surprise_pct = surprise
                                existing_pending.revenue_actual = rev_act
                                existing_pending.revenue_surprise_pct = rev_surprise
                                existing_pending.earnings_strength_score = strength
                                existing_pending.fetched_at = _now
                                s.flush()
                                upserted += 1
                            else:
                                # No prior pending row (and therefore no revenue_estimate ever
                                # captured for this event) — revenue_surprise_pct correctly
                                # stays unset, matching eps `surprise`'s own identical
                                # both-sides-required convention immediately above.
                                stmt = (
                                    pg_insert(EarningsEvent)
                                    .values(
                                        stock_id=stock_id,
                                        report_date=report_date,
                                        period=f"Q{fq} {fy}",
                                        fiscal_year=fy,
                                        fiscal_quarter=fq,
                                        eps_estimate=eps_est,
                                        eps_actual=eps_act,
                                        surprise_pct=surprise,
                                        revenue_actual=rev_act,
                                        earnings_strength_score=strength,
                                        fetched_at=_now,
                                    )
                                    .on_conflict_do_update(
                                        constraint="uq_earnings_stock_report_date",
                                        set_=dict(
                                            eps_estimate=eps_est,
                                            eps_actual=eps_act,
                                            surprise_pct=surprise,
                                            revenue_actual=rev_act,
                                            earnings_strength_score=strength,
                                            fetched_at=_now,
                                        ),
                                    )
                                )
                                result = s.execute(stmt)
                                upserted += result.rowcount
                        except Exception:
                            continue
                    s.commit()
        except Exception as exc:
            log.debug("earnings.history_skip", symbol=symbol, error=str(exc))

        # Upcoming earnings date (calendar)
        try:
            cal = ticker.calendar
            if cal is not None:
                earnings_dt = cal.get("Earnings Date")
                if earnings_dt is not None:
                    if hasattr(earnings_dt, "__iter__") and not isinstance(earnings_dt, str):
                        earnings_dt = list(earnings_dt)[0]
                    if hasattr(earnings_dt, "date"):
                        upcoming = earnings_dt.date()
                    else:
                        upcoming = date.fromisoformat(str(earnings_dt)[:10])
                    eps_est = cal.get("EPS Estimate")
                    rev_est = cal.get("Revenue Estimate")
                    # AUD264-EARNINGS-FISCAL-QUARTER-FROM-ANNOUNCEMENT-MONTH: fiscal_year/
                    # fiscal_quarter are still only a best-effort calendar-month label (see the
                    # historical-path comment above for why) — the real fix here is switching
                    # the uniqueness key from (fiscal_year, fiscal_quarter) to (stock_id,
                    # report_date), which is what actually stops two genuine reports in the same
                    # calendar quarter from silently overwriting each other.
                    fq = (upcoming.month - 1) // 3 + 1
                    fy = upcoming.year
                    with SessionLocal() as s:
                        # AUD264: report_date is now the uniqueness key (not fiscal_year/
                        # fiscal_quarter), but yfinance's own PROJECTED earnings date for an
                        # unreported quarter routinely shifts by a few days as the real date is
                        # confirmed closer to the event — daily re-syncs (sync_earnings runs
                        # every day at 06:30 UTC) would otherwise insert a brand-new row each
                        # time the estimate moves instead of updating the one real pending
                        # event. There can only ever be one legitimate "next unreported" row per
                        # stock (eps_actual IS NULL, the same predicate already used elsewhere
                        # in this codebase to mean "not yet reported") — find and update it in
                        # place if it exists under a DIFFERENT date than today's estimate,
                        # rather than letting the insert path create a duplicate.
                        existing_pending = s.execute(
                            select(EarningsEvent).where(
                                EarningsEvent.stock_id == stock_id,
                                EarningsEvent.eps_actual.is_(None),
                            )
                        ).scalars().first()
                        # See DQ-EARNINGS-FETCHED-AT-FROZEN comment above — same reasoning applies
                        # to the upcoming-earnings-calendar upsert path.
                        _now = datetime.now(timezone.utc)
                        _est = float(eps_est) if eps_est and pd.notna(eps_est) else None
                        _rev_est = float(rev_est) if rev_est and pd.notna(rev_est) else None
                        if existing_pending is not None:
                            existing_pending.report_date = upcoming
                            existing_pending.period = f"Q{fq} {fy}"
                            existing_pending.fiscal_year = fy
                            existing_pending.fiscal_quarter = fq
                            existing_pending.eps_estimate = _est
                            existing_pending.revenue_estimate = _rev_est
                            existing_pending.fetched_at = _now
                        else:
                            stmt = (
                                pg_insert(EarningsEvent)
                                .values(
                                    stock_id=stock_id,
                                    report_date=upcoming,
                                    period=f"Q{fq} {fy}",
                                    fiscal_year=fy,
                                    fiscal_quarter=fq,
                                    eps_estimate=_est,
                                    revenue_estimate=_rev_est,
                                    fetched_at=_now,
                                )
                                .on_conflict_do_update(
                                    constraint="uq_earnings_stock_report_date",
                                    set_=dict(
                                        eps_estimate=_est,
                                        revenue_estimate=_rev_est,
                                        fetched_at=_now,
                                    ),
                                )
                            )
                            s.execute(stmt)
                        s.commit()
                        upserted += 1
        except Exception as exc:
            log.debug("earnings.calendar_skip", symbol=symbol, error=str(exc))

        return upserted
    except Exception as exc:
        log.warning("earnings.symbol_fail", symbol=symbol, error=str(exc))
        return 0


def _backfill_report_dates_for_symbol(symbol: str, stock_id: int) -> int:
    """AUD264-EARNINGS-FISCAL-QUARTER-FROM-ANNOUNCEMENT-MONTH one-time backfill: rows already
    persisted BEFORE this fix shipped have report_date set to the fiscal PERIOD-END date, not
    the real announcement date (see _match_report_dates_to_history()'s own docstring). The
    normal sync path (_fetch_earnings_for_symbol) does NOT self-heal these — its
    existing_pending lookup only matches eps_actual IS NULL rows, so re-running the ordinary
    daily sync against an already-reported stale row would INSERT A DUPLICATE under the
    correct date rather than fix the original. This function instead finds each already-
    reported row (eps_actual IS NOT NULL) for the symbol and updates report_date/period/
    fiscal_year/fiscal_quarter IN PLACE, matched by eps_actual — the exact same reliable join
    key _match_report_dates_to_history() already uses. Returns the count of rows corrected.

    Safe to run repeatedly: a row whose report_date already matches the real announcement
    date needs no update (skipped), and a row with no matching announce-side data is left
    untouched (matching _fetch_earnings_for_symbol's own fail-open fallback).
    """
    try:
        import yfinance as yf
        ticker = yf.Ticker(symbol)
        hist = ticker.earnings_history
        if hist is None or hist.empty:
            return 0
        ed = ticker.earnings_dates
        if ed is None or ed.empty:
            return 0

        hist_rows = []
        for idx, row in hist.iterrows():
            pe = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
            ea = row.get("epsActual")
            hist_rows.append({"period_end": pe, "eps_actual": float(ea) if pd.notna(ea) else None})

        announce_rows = []
        for idx, row in ed.iterrows():
            ad = idx.date() if hasattr(idx, "date") else date.fromisoformat(str(idx)[:10])
            ea = row.get("Reported EPS")
            announce_rows.append({"announce_date": ad, "eps_actual": float(ea) if pd.notna(ea) else None})

        real_dates_by_period_end = _match_report_dates_to_history(hist_rows, announce_rows)
        # Also index the real dates by eps_actual directly, so a stored row can be matched
        # even if its OWN report_date no longer matches any period_end in the current
        # earnings_history window (yfinance's history window rolls forward over time).
        real_date_by_eps: dict[float, date] = {}
        for row in hist_rows:
            ea = row.get("eps_actual")
            pe = row.get("period_end")
            if ea is None or pe is None:
                continue
            matched = real_dates_by_period_end.get(pe.isoformat())
            if matched is not None:
                real_date_by_eps.setdefault(round(ea, 2), matched)

        corrected = 0
        with SessionLocal() as s:
            rows = s.execute(
                select(EarningsEvent).where(
                    EarningsEvent.stock_id == stock_id,
                    EarningsEvent.eps_actual.is_not(None),
                )
            ).scalars().all()
            for ev in rows:
                real_date = real_date_by_eps.get(round(ev.eps_actual, 2))
                if real_date is None or real_date == ev.report_date:
                    continue
                # AUD264-BACKFILL-PENDING-ROW-COLLISION: if the normal daily sync has already
                # run since this fix shipped, it may have ALREADY inserted (or updated a
                # pending row to) the correct real_date for this exact event via its own
                # existing_pending logic — the (stock_id, report_date) uniqueness constraint
                # would otherwise reject this UPDATE outright. Confirmed live: AAPL's calendar
                # path had already written a pending row (eps_actual NULL) at the real
                # announcement date before this backfill ran. Since the ALREADY-REPORTED row
                # (ev, with real eps_actual/surprise_pct/strength) carries the data worth
                # keeping, delete the redundant pending duplicate and move ev onto its date.
                conflicting = s.execute(
                    select(EarningsEvent).where(
                        EarningsEvent.stock_id == stock_id,
                        EarningsEvent.report_date == real_date,
                        EarningsEvent.id != ev.id,
                    )
                ).scalars().first()
                if conflicting is not None:
                    if conflicting.eps_actual is not None:
                        # A genuinely different, already-reported row already sits at this
                        # date — do not silently clobber real, independent data; skip this
                        # one for manual review rather than guessing which is correct.
                        continue
                    s.delete(conflicting)
                    s.flush()
                ev.report_date = real_date
                ev.fiscal_year = real_date.year
                ev.fiscal_quarter = (real_date.month - 1) // 3 + 1
                ev.period = f"Q{ev.fiscal_quarter} {ev.fiscal_year}"
                corrected += 1
            s.commit()
        return corrected
    except Exception as exc:
        log.warning("earnings.backfill_report_dates_failed", symbol=symbol, error=str(exc))
        return 0


async def backfill_report_dates() -> dict:
    """One-time, safe-to-re-run backfill correcting every already-stored, already-reported
    earnings_events row's report_date from the pre-fix period-end date to the real
    announcement date. See _backfill_report_dates_for_symbol()'s own docstring for why this
    is a separate function from the normal daily sync, not something that self-heals."""
    with SessionLocal() as s:
        stocks = s.execute(select(Stock.id, Stock.symbol)).all()

    loop = asyncio.get_running_loop()
    total = 0
    for stock_id, symbol in stocks:
        n = await loop.run_in_executor(_executor, _backfill_report_dates_for_symbol, symbol, stock_id)
        total += n
        await asyncio.sleep(0.2)  # gentle rate limiting, matching sync_all_earnings()

    return {"symbols_processed": len(stocks), "rows_corrected": total}


def _compute_surprise_pct(estimate: float | None, actual: float | None) -> float | None:
    """% beat/miss of `actual` over `estimate`. Shared by both eps_surprise (unchanged formula,
    extracted here under AUD-EARNINGS-REVENUEACTUAL) and revenue_surprise_pct (new) — same
    both-sides-required, divide-by-abs(estimate) convention either way."""
    if estimate is None or actual is None or estimate == 0:
        return None
    return round((actual - estimate) / abs(estimate) * 100, 2)


def _compute_strength(eps_est: float | None, eps_act: float | None, surprise_pct: float | None) -> float | None:
    """0-100 earnings strength score based on beat size."""
    if eps_act is None:
        return None
    score = 50.0
    if surprise_pct is not None:
        if surprise_pct > 20:    score += 30
        elif surprise_pct > 10:  score += 20
        elif surprise_pct > 5:   score += 10
        elif surprise_pct < -10: score -= 20
        elif surprise_pct < -5:  score -= 10
    if eps_act and eps_act > 0:
        score += 10
    return max(0.0, min(100.0, score))


async def sync_all_earnings() -> dict:
    """Sync earnings for all tracked stocks. Runs yfinance calls in thread pool."""
    with SessionLocal() as s:
        stocks = s.execute(select(Stock.id, Stock.symbol)).all()

    loop = asyncio.get_running_loop()
    total = 0
    for stock_id, symbol in stocks:
        n = await loop.run_in_executor(_executor, _fetch_earnings_for_symbol, symbol, stock_id)
        total += n
        await asyncio.sleep(0.2)  # gentle rate limiting

    return {"symbols_processed": len(stocks), "rows_upserted": total}


async def sync_todays_earnings() -> dict:
    """AUD-EARNINGS-INTRADAY-SYNC-GAP: sync_all_earnings() only runs once/day at 06:30 UTC —
    before the US market session — so a company reporting during market hours or after the
    close (the overwhelming majority of real earnings releases) never gets its eps_actual/
    surprise_pct picked up until THE NEXT MORNING's 06:30 sync. check_earnings_reactions()
    (market-data, runs every minute) and check_earnings_impact_poll() (this service, every
    5 min) both correctly fire the instant eps_actual lands — the gap was purely upstream,
    that nothing ever re-synced a stock's row same-day once it actually reported. Confirmed
    live 2026-08-03: PLTR posted real, materially strong Q2 results (EPS $0.41 beat $0.35,
    guidance raised) at ~20:05 UTC, but its earnings_events row still showed eps_actual=NULL
    hours later because nothing had re-checked it since that morning's sync.

    Deliberately NOT a second full-universe rescan (that's what the 06:30 UTC job is for,
    and running it again intraday would repeat the same ~178-symbol yfinance sweep every
    cycle for no reason) — scoped to just the stocks that ACTUALLY need a fresh check:
    report_date in {yesterday, today} (yesterday covers an after-market print Yahoo still
    files against the prior calendar day) with eps_actual still NULL. On a quiet day with
    nobody reporting, or once all of today's reporters have already resolved, this is a
    single cheap indexed query with zero yfinance calls at all.
    """
    cutoff_start = date.today() - timedelta(days=1)
    cutoff_end = date.today()
    with SessionLocal() as s:
        rows = s.execute(
            select(EarningsEvent.stock_id, Stock.symbol)
            .join(Stock, EarningsEvent.stock_id == Stock.id)
            .where(
                EarningsEvent.report_date >= cutoff_start,
                EarningsEvent.report_date <= cutoff_end,
                EarningsEvent.eps_actual.is_(None),
            )
        ).all()

    if not rows:
        return {"symbols_checked": 0, "rows_upserted": 0}

    loop = asyncio.get_running_loop()
    total = 0
    for stock_id, symbol in rows:
        n = await loop.run_in_executor(_executor, _fetch_earnings_for_symbol, symbol, stock_id)
        total += n
        await asyncio.sleep(0.2)  # gentle rate limiting, matching sync_all_earnings()

    return {"symbols_checked": len(rows), "rows_upserted": total}


def get_earnings_for_symbol(stock_id: int, days_back: int = 365) -> list[dict]:
    since = date.today() - timedelta(days=days_back)
    with SessionLocal() as s:
        rows = s.execute(
            select(EarningsEvent)
            .where(EarningsEvent.stock_id == stock_id, EarningsEvent.report_date >= since)
            .order_by(EarningsEvent.report_date.desc())
        ).scalars().all()
        return [_row_to_dict(e) for e in rows]


def get_upcoming_earnings(days: int = 14) -> list[dict]:
    today = date.today()
    cutoff = today + timedelta(days=days)
    with SessionLocal() as s:
        rows = s.execute(
            select(EarningsEvent, Stock.symbol, Stock.name)
            .join(Stock, EarningsEvent.stock_id == Stock.id)
            .where(EarningsEvent.report_date >= today, EarningsEvent.report_date <= cutoff)
            .order_by(EarningsEvent.report_date)
        ).all()
        return [
            {
                **_row_to_dict(e),
                "symbol": symbol,
                "company": name,
            }
            for e, symbol, name in rows
        ]


def get_days_to_earnings(stock_id: int) -> int | None:
    today = date.today()
    with SessionLocal() as s:
        row = s.execute(
            select(EarningsEvent.report_date)
            .where(EarningsEvent.stock_id == stock_id, EarningsEvent.report_date >= today)
            .order_by(EarningsEvent.report_date)
            .limit(1)
        ).scalar_one_or_none()
        if row is None:
            return None
        return (row - today).days


def get_beat_rate(stock_id: int, lookback: int = 8) -> float | None:
    with SessionLocal() as s:
        rows = s.execute(
            select(EarningsEvent.surprise_pct)
            .where(EarningsEvent.stock_id == stock_id, EarningsEvent.surprise_pct.isnot(None))
            .order_by(EarningsEvent.report_date.desc())
            .limit(lookback)
        ).scalars().all()
        if not rows:
            return None
        beats = sum(1 for x in rows if x > 0)
        return round(beats / len(rows), 2)


def _row_to_dict(e: EarningsEvent) -> dict:
    today = date.today()
    return {
        "id": e.id,
        "stock_id": e.stock_id,
        "earnings_date": e.report_date.isoformat(),  # matches TypeScript EarningsEvent.earnings_date
        "estimated_eps": e.eps_estimate,              # matches TypeScript EarningsEvent.estimated_eps
        "actual_eps": e.eps_actual,                   # matches TypeScript EarningsEvent.actual_eps
        "estimated_revenue": e.revenue_estimate,      # matches TypeScript EarningsEvent.estimated_revenue
        "actual_revenue": e.revenue_actual,           # matches TypeScript EarningsEvent.actual_revenue
        "surprise_pct": e.surprise_pct,
        "beat_rate": None,    # per-row historical beat rate requires extra query; shows '—' in UI
        "avg_beat_pct": None,
        "is_upcoming": e.report_date >= today,        # matches TypeScript EarningsEvent.is_upcoming
        "period": e.period,
        "fiscal_year": e.fiscal_year,
        "fiscal_quarter": e.fiscal_quarter,
        "earnings_strength_score": e.earnings_strength_score,
    }
