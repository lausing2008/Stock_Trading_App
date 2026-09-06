"""Claude Haiku headline classification — sentiment + materiality + category.

Same call shape as market-data/src/api/news.py's _claude_sentiment() and
event-intelligence/src/services/macro_reaction.py's generate_reaction() — reused rather than
reinvented: get_admin_ai_key("claude") for the credential (Redis-first, matching every other
LLM call site in this repo), a single Haiku call per headline batch, and _strip_markdown_fence()
applied before json.loads() (Claude sometimes wraps JSON in ```json fences despite being told
not to — this bit multiple call sites in this repo before the shared helper existed).

Deliberately batches headlines (up to _BATCH_SIZE per call) rather than one call per headline —
a real-time news poller can see many headlines per cycle during a busy market open, and one
call per headline would both cost more and add per-headline latency that defeats the point of
a "fast reaction" feature.
"""
from __future__ import annotations

import json
import re

import httpx
import structlog

log = structlog.get_logger()

_BATCH_SIZE = 8

_SYSTEM = """You are a financial news analyst classifying real-time headlines for a trading app.
For EACH headline given (numbered), return an entry in a JSON array, in the SAME order, with:
{"sentiment_score": <integer 0-100, 50=neutral>, "sentiment_label": "positive"|"negative"|"neutral",
"is_material": <true if this headline could plausibly move the stock's price today, e.g. earnings,
FDA decision, M&A, guidance change, major contract, executive departure, downgrade/upgrade —
false for routine/promotional/generic news>, "category": "earnings"|"fda"|"ma"|"analyst"|"macro"|"other"}
Respond ONLY with the JSON array, no other text, no markdown fences."""


def _strip_markdown_fence(text: str) -> str:
    """Matches market-data/src/api/news.py's own established stripping pattern."""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.DOTALL).strip()


def classify_headlines(headlines: list[str], api_key: str) -> list[dict | None]:
    """Return one classification dict (or None on a per-item parse failure) per input headline,
    in the same order. Returns an all-None list of the same length if `api_key` is empty or the
    call fails outright — fail-open, matching every other Claude call site in this codebase; a
    classification failure must never block ingestion, only skip the sentiment/materiality
    metadata for that batch."""
    if not headlines:
        return []
    if not api_key:
        return [None] * len(headlines)

    numbered = "\n".join(f"{i + 1}. {h}" for i, h in enumerate(headlines))
    # AUD-LLMUSAGE: this is the exact call site BUG-NEWSCLASSIFY-REPEATCOST's undetected
    # deploy-drift ran unlogged for six weeks (518x reclassification of one filing, confirmed
    # live) — see llm_usage.py's own module docstring for the full incident. Timed and logged
    # on every path (success, http_error, exception) so a repeat is caught by
    # check_llm_usage_spike() within the hour instead of by chance on a billing page.
    import time as _time
    from common.llm_usage import CALL_SITE_NEWS_CLASSIFY, log_llm_call
    _model = "claude-haiku-4-5-20251001"
    _t0 = _time.monotonic()
    try:
        with httpx.Client(timeout=15) as client:
            r = client.post(
                "https://api.anthropic.com/v1/messages",
                json={
                    "model": _model,
                    "max_tokens": 200 * len(headlines),
                    "system": _SYSTEM,
                    "messages": [{"role": "user", "content": numbered}],
                },
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
        _duration_ms = int((_time.monotonic() - _t0) * 1000)
        if r.status_code != 200:
            log.warning("news_classify.http_error", status=r.status_code)
            log_llm_call(
                service="news-intelligence", call_site=CALL_SITE_NEWS_CLASSIFY, model=_model,
                duration_ms=_duration_ms, status="http_error", http_status=r.status_code,
                context={"headline_count": len(headlines)},
            )
            return [None] * len(headlines)
        _resp_json = r.json()
        log_llm_call(
            service="news-intelligence", call_site=CALL_SITE_NEWS_CLASSIFY, model=_model,
            usage=_resp_json.get("usage"), duration_ms=_duration_ms, status="ok",
            context={"headline_count": len(headlines)},
        )
    except Exception as exc:
        log.warning("news_classify.failed", error=str(exc))
        log_llm_call(
            service="news-intelligence", call_site=CALL_SITE_NEWS_CLASSIFY, model=_model,
            duration_ms=int((_time.monotonic() - _t0) * 1000), status="error",
            error=str(exc), context={"headline_count": len(headlines)},
        )
        return [None] * len(headlines)

    # AUD-LLMUSAGE: parsing outside the network-call try — a malformed JSON body here is
    # an already-billed "ok" call, not an API failure; must not double-log as "error".
    try:
        text = _strip_markdown_fence(_resp_json["content"][0]["text"])
        parsed = json.loads(text)
        if not isinstance(parsed, list):
            return [None] * len(headlines)
    except Exception as exc:
        log.warning("news_classify.parse_failed", error=str(exc))
        return [None] * len(headlines)

    out: list[dict | None] = []
    for i in range(len(headlines)):
        if i >= len(parsed) or not isinstance(parsed[i], dict):
            out.append(None)
            continue
        item = parsed[i]
        try:
            score = max(0.0, min(100.0, float(item.get("sentiment_score", 50))))
            label = item.get("sentiment_label") or "neutral"
            if label not in ("positive", "negative", "neutral"):
                label = "neutral"
            category = item.get("category") or "other"
            if category not in ("earnings", "fda", "ma", "analyst", "macro", "other"):
                category = "other"
            out.append({
                "sentiment_score": score,
                "sentiment_label": label,
                "is_material": bool(item.get("is_material", False)),
                "category": category,
            })
        except (TypeError, ValueError):
            out.append(None)
    return out


def classify_in_batches(headlines: list[str], api_key: str) -> list[dict | None]:
    """Chunk `headlines` into _BATCH_SIZE-sized calls to classify_headlines(). One failed batch
    degrades only that batch's items to None, not the whole list — a transient failure on one
    chunk shouldn't discard classifications that another chunk already succeeded at."""
    results: list[dict | None] = []
    for i in range(0, len(headlines), _BATCH_SIZE):
        chunk = headlines[i:i + _BATCH_SIZE]
        results.extend(classify_headlines(chunk, api_key))
    return results
