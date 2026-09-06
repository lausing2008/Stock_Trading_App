"""AUD-LLMUSAGE: single shared helper that logs every real Anthropic API call this platform
makes, across all 9 call sites in 6 services, to the new `llm_call_log` table.

Built specifically because NONE of the 9 call sites logged real token usage anywhere before
this, and that blind spot let BUG-NEWSCLASSIFY-REPEATCOST run for six weeks undetected — a
`docker cp` deploy-drift (found live 2026-09-05) meant a real fix committed 2026-07-27 never
reached the running news-intelligence container, so its EDGAR poller reclassified the same
filings via Claude Haiku every 2 minutes with no dedup, confirmed on the Claude Console usage
page at 5.44M tokens on 2026-09-05 alone (see LlmCallLog's own docstring in shared/db/models.py
for the full incident writeup). This table and the DQ-check spike alert built alongside it
(services/market-data/src/services/scheduler.py's check_llm_usage_spike()) exist so the NEXT
such incident is caught within the hour, not discovered by chance on an external billing page.

Usage — wrap the existing httpx call, don't replace it:

    from common.llm_usage import log_llm_call, CALL_SITE_NEWS_CLASSIFY
    import time
    _t0 = time.monotonic()
    try:
        r = httpx.post(url, json=body, headers=headers, timeout=15)
        r.raise_for_status()
        data = r.json()
        log_llm_call(
            service="news-intelligence", call_site=CALL_SITE_NEWS_CLASSIFY,
            model=body["model"], usage=data.get("usage"),
            duration_ms=int((time.monotonic() - _t0) * 1000), status="ok",
            context={"headline_count": len(headlines)},
        )
    except httpx.HTTPStatusError as exc:
        log_llm_call(..., status="http_error", http_status=exc.response.status_code, ...)
    except Exception as exc:
        log_llm_call(..., status="error", error=str(exc)[:500], ...)

Deliberately NOT a decorator or context manager: every one of the 9 call sites already has its
own bespoke error handling (some retry, some fail-open silently, some fail-closed) — wrapping
would either fight that existing logic or require rewriting all 9 sites' control flow just to
add logging. A plain function call at each of the (already-existing) success/failure points is
the smallest real change to each site.
"""
from __future__ import annotations

import structlog

log = structlog.get_logger()

# Canonical call-site slugs — stable identifiers for dashboard grouping, independent of
# whatever a given service's own log event happens to be named today. One per real call site;
# add a new constant here (not a bare string at the call site) whenever a 10th site is added,
# so a typo can't silently create an ungrouped slug in the dashboard.
CALL_SITE_RESEARCH_REPORT = "research_report"
CALL_SITE_RESEARCH_CHAT = "research_chat"
CALL_SITE_DECIDE_LLM_SCORER = "decide_llm_scorer"
CALL_SITE_DECIDE_RISK_AGENT = "decide_risk_agent"
CALL_SITE_NEWS_SENTIMENT = "news_sentiment"
CALL_SITE_MARKET_PULSE = "market_pulse"
CALL_SITE_TRADE_COACH = "trade_coach"
CALL_SITE_THEME_SIGNALS = "theme_signals"
CALL_SITE_MACRO_REACTION = "macro_reaction"
CALL_SITE_EARNINGS_IMPACT = "earnings_impact"
CALL_SITE_EARNINGS_FORECAST = "earnings_forecast"
CALL_SITE_NEWS_CLASSIFY = "news_classify"


def log_llm_call(
    *,
    service: str,
    call_site: str,
    model: str,
    usage: dict | None = None,
    duration_ms: int | None = None,
    status: str = "ok",
    http_status: int | None = None,
    error: str | None = None,
    context: dict | None = None,
) -> None:
    """Persist one row to llm_call_log. Fail-open and fail-SILENT by design: a logging failure
    must never be allowed to affect the real LLM call's own success/failure path, and must
    never raise into the caller — this is observability, not business logic. `usage` is
    Anthropic's own response field verbatim ({"input_tokens": N, "output_tokens": N}); passing
    None (e.g. on an http_error/error path where no response body was ever parsed) stores NULL
    token counts rather than a fabricated 0, so a failed-call row is never mistaken for a real
    zero-token success in later aggregation.
    """
    try:
        from db import LlmCallLog, SessionLocal
        with SessionLocal() as session:
            session.add(LlmCallLog(
                service=service,
                call_site=call_site,
                model=model,
                input_tokens=(usage or {}).get("input_tokens"),
                output_tokens=(usage or {}).get("output_tokens"),
                duration_ms=duration_ms,
                status=status,
                http_status=http_status,
                error=error[:2000] if error else None,
                context=context,
            ))
            session.commit()
    except Exception as exc:
        log.warning("llm_usage.log_failed", service=service, call_site=call_site, error=str(exc))
