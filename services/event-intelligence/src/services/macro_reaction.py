"""T249-MARKETMOVER-P2 — post-announcement fast reaction for macro releases.

Two independent poll mechanisms, both armed only on days a real release is due (per
economic.py's release-date calendar / _FOMC_DATES), not running continuously:

1. BLS/BEA releases (CPI, PPI, NFP, GDP, PCE) — polls FRED's series/observations endpoint.
   FRED was chosen over BLS's own v2 API after live research found BLS's own documentation
   states a ~1-day lag between a real release and API availability, which would miss same-day
   detection entirely — disqualifying for this use case. FRED itself was confirmed (via a live
   production check) to have June 2026 CPI's realtime_start equal to its actual July 14 release
   date, i.e. same-day availability, matching the existing economic.py sync_fred() usage.

2. FOMC statements — polls the Federal Reserve's own press_monetary.xml RSS feed directly
   (confirmed live: https://www.federalreserve.gov/feeds/press_monetary.xml), since FRED's own
   rate series lag a day and have no "statement just posted" signal at all. Uses feedparser,
   already a dependency in market-data's news.py for the same kind of feed polling.

Both paths write into economic_events (actual_value/reaction_text/reaction_generated_at), NOT
directly to any alert channel — market-data's own scheduler owns email/push/webhook delivery
(see check_earnings_reactions() in market-data/src/services/scheduler.py for the same split:
event-intelligence detects+generates, market-data delivers), and polls this table for rows
with a generated-but-unsent reaction.
"""
from __future__ import annotations

import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import httpx
import structlog
from sqlalchemy import select

from common.config import get_settings
from db import SessionLocal, EconomicEvent

from .economic import _FRED_RELEASES, _FRED_SERIES, _FOMC_DATES

log = structlog.get_logger()
_settings = get_settings()

# AUD-EI-MACRO-REACTION-BLOCKING: this service is a single-process FastAPI app whose
# AsyncIOScheduler jobs run on the SAME event loop that serves real-time HTTP requests (see
# main.py: start_scheduler runs inside the FastAPI app, not a separate process). The FRED/Fed
# HTTP calls and feedparser's own internal fetch below are all blocking I/O — calling them
# directly from an `async def` would stall every concurrent request to this service for up to
# each call's timeout. Same fix pattern as decision-engine/src/api/core/regime.py's
# _regime_executor: a small dedicated thread pool + loop.run_in_executor().
_macro_reaction_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="macro_reaction")

_REDIS_CLAUDE_KEY = "stockai:admin:claude_api_key"

_SYSTEM = """You are a macro analyst producing a brief reaction read for a retail trading app.
You will receive the actual released value, expected/previous values, recent print history,
current market regime, and current VIX for a just-released US economic indicator.
Respond ONLY with valid JSON (no markdown, no explanation outside JSON) in this exact format:
{"surprise_direction":"above","magnitude":"mild","one_paragraph":"<2-3 sentences>","sectors_helped":["Technology"],"sectors_hurt":["Utilities"]}
surprise_direction must be "above", "below", or "in_line" (relative to expected/previous).
magnitude must be "in_line", "mild", or "large".
one_paragraph must be 2-3 plain-English sentences a retail trader can act on, max 400 chars.
sectors_helped and sectors_hurt: 0-4 GICS-style sector names each (e.g. "Technology",
"Financials", "Energy", "Utilities", "Consumer Discretionary", "Healthcare", "Industrials",
"Materials", "Real Estate", "Communication Services", "Consumer Staples") that this specific
release plausibly helps or hurts, based ONLY on the standard macro-to-sector relationship for
this indicator type (e.g. hot CPI hurts rate-sensitive sectors like Real Estate/Utilities;
strong GDP helps cyclicals). Use empty lists if you have no concrete basis — never pad these
lists to look complete."""

# event_type (from _FRED_RELEASES/_FRED_SERIES) -> the reference-period FRED series_id used to
# fetch actual_value fast. Distinct from _FRED_SERIES' own event_type keys (e.g. "cpi") since
# these dict keys are the *_release family the release-date calendar uses.
_RELEASE_TO_FRED_SERIES: dict[str, str] = {
    "cpi_release": "CPIAUCSL",
    "ppi_release": "PPIACO",
    "gdp_release": "GDP",
    "nfp_release": "PAYEMS",
    "pce_release": None,  # PCE price index isn't in _FRED_SERIES today; skipped until added
}


def _api_key() -> str:
    """AUD-DUPLOGIC: delegates to common.ai_keys.get_admin_ai_key() — this was one of 6
    independent copies of the same Redis-key lookup across decision-engine/event-intelligence/
    market-data/research-engine. The getattr(_settings, "claude_api_key", "") fallback below
    was already permanently dead (Settings never had that field) — kept only for the
    theoretical case a future Settings field is added, matching the historical convention
    documented at this file's original fix site."""
    from common.ai_keys import get_admin_ai_key
    key = get_admin_ai_key("claude")
    if key:
        return key
    return getattr(_settings, "claude_api_key", "") or ""


def _get_market_regime() -> dict:
    try:
        r = httpx.get(f"{_settings.market_data_url}/stocks/regime", params={"market": "US"}, timeout=8)
        if r.status_code == 200:
            return r.json()
    except Exception as exc:
        log.warning("macro_reaction.regime_fetch_failed", error=str(exc))
    return {}


async def generate_reaction(event_type: str, actual_value: float, expected_value: float | None,
                             previous_value: float | None, title: str) -> dict | None:
    """Calls Claude for a structured reaction read. Fail-open: returns None on any error —
    the caller stores None as "no reaction available" rather than blocking the actual_value
    write, matching decision-engine's llm_scorer.py fail-open discipline (advisory, not
    gate-blocking).

    T258-MACRO-SECTOR-IMPACT: returns a dict {"reaction_text": str, "sectors_helped": list[str],
    "sectors_hurt": list[str]} — was previously a bare `str | None` (just the paragraph); the
    surprise_direction/magnitude fields the LLM already returned were silently discarded before
    this change and remain so (only reaction_text and the two new sector lists are persisted).
    Callers must be updated for the new shape — see check_release_day_fast_poll() and
    check_fomc_statement_poll() below, both already updated in this same commit.
    """
    api_key = _api_key()
    if not api_key:
        log.info("macro_reaction.no_api_key", event_type=event_type)
        return None

    loop = asyncio.get_running_loop()
    regime = await loop.run_in_executor(_macro_reaction_executor, _get_market_regime)
    prompt = (
        f"Indicator: {title} ({event_type})\n"
        f"Actual: {actual_value}\n"
        f"Expected/prior: {expected_value if expected_value is not None else 'unavailable'}\n"
        f"Previous print: {previous_value if previous_value is not None else 'unavailable'}\n"
        f"Current market regime: {regime.get('state', 'unknown')}\n"
        f"Current VIX: {regime.get('vix', 'unavailable')}\n"
    )
    body = {
        "model": "claude-haiku-4-5-20251001",
        "max_tokens": 300,
        "temperature": 0.2,
        "system": _SYSTEM,
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
            log.warning("macro_reaction.api_error", status=r.status_code, body=r.text[:200])
            return None
        raw = r.json()["content"][0]["text"].strip()
        raw = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw, flags=re.DOTALL).strip()
        data = json.loads(raw)
        reaction_text = (data.get("one_paragraph") or "")[:500] or None
        if reaction_text is None:
            return None
        sectors_helped = _clean_sector_list(data.get("sectors_helped"))
        sectors_hurt = _clean_sector_list(data.get("sectors_hurt"))
        return {
            "reaction_text": reaction_text,
            "sectors_helped": sectors_helped,
            "sectors_hurt": sectors_hurt,
        }
    except Exception as exc:
        log.warning("macro_reaction.call_failed", event_type=event_type, error=str(exc))
        return None


def _clean_sector_list(raw: object) -> list[str]:
    """Validates the LLM's sectors_helped/sectors_hurt output — must be a list of non-empty
    strings, capped at a sane length. Malformed input (wrong type, non-string entries) degrades
    to an empty list rather than raising, matching this function's own fail-open contract for
    the whole reaction (a bad sector list must never take down the reaction_text it's paired
    with)."""
    if not isinstance(raw, list):
        return []
    cleaned = [str(s).strip() for s in raw if isinstance(s, str) and str(s).strip()]
    return cleaned[:6]


async def check_release_day_fast_poll() -> dict:
    """Release-day-armed fast poll for BLS/BEA releases via FRED. Only does any work on days
    the release-date calendar (economic_events' *_release rows) says a release is due today —
    cheap no-op the other ~360 days/year. For each due release without actual_value yet, polls
    FRED's series/observations for a new value; once found, writes it plus an LLM reaction.
    """
    api_key = getattr(_settings, "fred_api_key", "")
    if not api_key:
        return {"checked": 0, "found": 0, "skipped": "no_api_key"}

    today = datetime.now(timezone.utc).date()
    checked = 0
    found = 0
    with SessionLocal() as s:
        due_today = s.execute(
            select(EconomicEvent).where(
                EconomicEvent.event_type.in_(list(_RELEASE_TO_FRED_SERIES.keys())),
                EconomicEvent.event_date >= datetime(today.year, today.month, today.day, tzinfo=timezone.utc),
                EconomicEvent.event_date < datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc),
                EconomicEvent.actual_value.is_(None),
            )
        ).scalars().all()

        loop = asyncio.get_running_loop()
        for ev in due_today:
            series_id = _RELEASE_TO_FRED_SERIES.get(ev.event_type)
            if not series_id:
                continue
            checked += 1
            try:
                r = await loop.run_in_executor(
                    _macro_reaction_executor,
                    lambda sid=series_id: httpx.get(
                        "https://api.stlouisfed.org/fred/series/observations",
                        params={
                            "series_id": sid, "api_key": api_key, "file_type": "json",
                            "sort_order": "desc", "limit": 2,
                        },
                        timeout=10,
                    ),
                )
                if r.status_code != 200:
                    continue
                obs = r.json().get("observations", [])
                if not obs or obs[0]["value"] in (".", ""):
                    continue
                actual = float(obs[0]["value"])
                previous = float(obs[1]["value"]) if len(obs) > 1 and obs[1]["value"] not in (".", "") else None

                ev.actual_value = actual
                ev.previous_value = previous
                reaction = await generate_reaction(ev.event_type, actual, ev.expected_value, previous, ev.title)
                ev.reaction_text = reaction["reaction_text"] if reaction else None
                ev.sectors_helped = json.dumps(reaction["sectors_helped"]) if reaction else None
                ev.sectors_hurt = json.dumps(reaction["sectors_hurt"]) if reaction else None
                ev.reaction_generated_at = datetime.now(timezone.utc)
                s.commit()
                found += 1
                log.info("macro_reaction.release_detected", event_type=ev.event_type, actual=actual)
            except Exception as exc:
                log.warning("macro_reaction.poll_error", event_type=ev.event_type, error=str(exc))

    return {"checked": checked, "found": found, "skipped": None}


def _is_fomc_day(today) -> bool:
    return any(d == today.isoformat() for d, _, _ in _FOMC_DATES)


async def check_fomc_statement_poll() -> dict:
    """Release-day-armed poll of the Fed's own press_monetary.xml RSS feed for a fresh FOMC
    statement — only runs any work on days _FOMC_DATES says a meeting concludes today.
    """
    today = datetime.now(timezone.utc).date()
    if not _is_fomc_day(today):
        return {"checked": 0, "found": 0, "skipped": "not_fomc_day"}

    import feedparser
    try:
        loop = asyncio.get_running_loop()
        feed = await loop.run_in_executor(
            _macro_reaction_executor,
            feedparser.parse, "https://www.federalreserve.gov/feeds/press_monetary.xml",
        )
    except Exception as exc:
        log.warning("macro_reaction.fomc_feed_failed", error=str(exc))
        return {"checked": 1, "found": 0, "skipped": "feed_error"}

    with SessionLocal() as s:
        ev = s.execute(
            select(EconomicEvent).where(
                EconomicEvent.event_type == "fomc_meeting",
                EconomicEvent.event_date >= datetime(today.year, today.month, today.day, tzinfo=timezone.utc),
                EconomicEvent.event_date < datetime(today.year, today.month, today.day, 23, 59, 59, tzinfo=timezone.utc),
                EconomicEvent.actual_value.is_(None),
            )
        ).scalar_one_or_none()
        if ev is None:
            return {"checked": 1, "found": 0, "skipped": "no_pending_row"}

        for entry in feed.entries[:5]:
            published = entry.get("published_parsed")
            if not published:
                continue
            pub_date = datetime(*published[:6], tzinfo=timezone.utc).date()
            title = entry.get("title", "")
            if pub_date == today and "FOMC statement" in title:
                ev.actual_value = 1.0  # sentinel: statement has posted (no single numeric value)
                reaction = await generate_reaction(
                    "fomc_meeting", 1.0, None, None, title,
                )
                ev.reaction_text = reaction["reaction_text"] if reaction else f"FOMC statement posted: {title}"
                ev.sectors_helped = json.dumps(reaction["sectors_helped"]) if reaction else None
                ev.sectors_hurt = json.dumps(reaction["sectors_hurt"]) if reaction else None
                ev.reaction_generated_at = datetime.now(timezone.utc)
                s.commit()
                log.info("macro_reaction.fomc_detected", title=title)
                return {"checked": 1, "found": 1, "skipped": None}

    return {"checked": 1, "found": 0, "skipped": None}
