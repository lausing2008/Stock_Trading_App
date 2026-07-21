"""News endpoint — recent headlines with sentiment.

Strategy:
  1. Fetch from yfinance, discard articles older than 7 days.
  2. For HK stocks (.HK) or when yfinance returns < 3 fresh articles,
     supplement with Google News RSS (no API key needed).
  3. Merge, deduplicate by title prefix, sort newest-first.
  4. Cache result in Redis for 30 minutes.

Sentiment:
  - Per-article: VADER with financial-domain lexicon corrections.
  - Aggregate (GET /stocks/{symbol}/news/sentiment): Claude Haiku if a key is
    configured (admin Settings page's stockai:admin:claude_api_key in Redis,
    or the ANTHROPIC_API_KEY env var as a fallback), else enhanced VADER
    average. Claude result cached 4h in Redis.
"""
from __future__ import annotations

import json
import re
import time
import urllib.parse
from datetime import datetime, timezone

import feedparser
import httpx
import redis as redis_lib
import yfinance as yf
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from common.config import get_settings
from common.logging import get_logger
from db import Stock, get_session

router = APIRouter(prefix="/stocks", tags=["news"])
log = get_logger("news")
_settings = get_settings()

_NEWS_TTL       = 30 * 60       # 30 min — news list cache
_SENTIMENT_TTL  = 4  * 60 * 60  # 4h  — aggregate sentiment cache
_STALE_CUTOFF   = 7  * 86400    # discard yfinance articles older than 7 days

# ── VADER with financial-domain lexicon corrections ───────────────────────────
# Default VADER lexicon is calibrated for social media. Financial headlines use
# domain-specific language that VADER systematically mis-scores: "resistance" is
# a technical chart term (neutral), not a negative; "beat" is very positive, not
# slightly positive; "investigation" far more negative than VADER weights it.
_analyzer = SentimentIntensityAnalyzer()
_analyzer.lexicon.update({
    # Earnings / guidance — VADER heavily under-scores these
    "beat": 2.5, "beats": 2.5, "topped": 2.0, "exceeded": 2.0, "surpassed": 2.0,
    "missed": -2.5, "miss": -2.5, "disappoints": -2.5, "disappointing": -2.0,
    "lowered": -1.0, "slashed": -2.0,
    # Analyst actions
    "upgrade": 2.0, "upgraded": 2.0, "outperform": 1.8, "overweight": 1.2,
    "downgrade": -2.0, "downgraded": -2.0, "underperform": -1.8, "underweight": -1.2,
    # Corporate actions — mostly positive
    "buyback": 1.5, "repurchase": 1.0,
    # Serious negatives
    "investigation": -2.0, "subpoena": -2.5, "probe": -1.5,
    "layoffs": -1.5, "bankruptcy": -3.0, "default": -2.5, "delisting": -2.5,
    # Neutral financial terms VADER over-penalises
    "volatile": -0.2, "volatility": -0.2,   # was ~-1.5; usually just market description
    "headwinds": -0.4,                        # context-dependent, not always bad
    "resistance": 0.0,                        # technical chart level — purely neutral
    "pressure": -0.4,                         # reduce from VADER's ~-1.5
    "risks": -0.2,                            # "risks remain" is boilerplate
})

# ── Claude configuration (optional — falls back to VADER if key absent) ───────
# Primary source is the same Redis key every other LLM feature in this app reads (set via
# the admin Settings page), matching llm_scorer.py/risk_agent.py's established
# _get_api_key() pattern. AUD-REDISAUDIT-CLAUDEKEY-FALLBACK: the last-resort fallback used
# to be a bare os.getenv("ANTHROPIC_API_KEY") — the only site in the repo referencing that
# env var, which nothing ever sets — now matches every sibling service's own fallback
# convention (getattr(_settings, "claude_api_key", "")) instead.
_REDIS_CLAUDE_KEY = "stockai:admin:claude_api_key"

def _get_redis() -> redis_lib.Redis:
    from common.redis_client import get_redis as _get_pool_redis
    return _get_pool_redis()


def _get_claude_key() -> str:
    try:
        key = _get_redis().get(_REDIS_CLAUDE_KEY) or ""
        if key.strip():
            return key.strip()
    except Exception:
        pass
    # AUD-REDISAUDIT-CLAUDEKEY-FALLBACK: this used to be the only site in the repo still
    # referencing a bare os.getenv("ANTHROPIC_API_KEY") fallback — every sibling service
    # (llm_scorer.py, risk_agent.py, macro_reaction.py) falls back to a cfg dict value or
    # getattr(_settings, "claude_api_key", "") instead. Nothing in this app ever sets
    # ANTHROPIC_API_KEY as a real env var, so this fallback was already permanently inert in
    # practice — changed for consistency with the established sibling pattern, not because the
    # old fallback was reachable in production.
    return getattr(_settings, "claude_api_key", "") or ""


def _strip_markdown_fence(text: str) -> str:
    """Claude sometimes wraps JSON in ```json ... ``` despite being told not to —
    matching risk_agent.py's own established stripping pattern before json.loads()."""
    return re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip(), flags=re.DOTALL).strip()


class NewsItem(BaseModel):
    title: str
    url: str
    source: str
    published_at: int
    sentiment: float
    sentiment_label: str
    thumbnail: str | None = None


class SentimentResponse(BaseModel):
    score: float   # 0-100, 50 = neutral
    label: str     # positive | negative | neutral
    source: str    # claude | vader


def _claude_sentiment(symbol: str, titles: list[str]) -> float | None:
    """Call Claude Haiku to classify financial headlines as a batch.

    Returns 0-100 score (50=neutral), or None if key absent / call fails.
    Result cached in Redis for 4h under stockai:news_sentiment:{symbol}.
    """
    api_key = _get_claude_key()
    if not api_key or not titles:
        return None
    cache_key = f"stockai:news_sentiment:{symbol.upper()}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            return float(cached)
    except Exception:
        pass
    headlines = "\n".join(f"- {t}" for t in titles[:5])
    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(
                "https://api.anthropic.com/v1/messages",
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 64,
                    "system": (
                        "You are a financial news analyst. Given stock news headlines, "
                        "return JSON only: {\"score\": <integer 0-100>} where 0=very negative "
                        "for investors, 50=neutral, 100=very positive. No other text."
                    ),
                    "messages": [{"role": "user", "content": f"Headlines:\n{headlines}"}],
                },
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
        if r.status_code == 200:
            text = _strip_markdown_fence(r.json()["content"][0]["text"])
            score = float(json.loads(text).get("score", 50))
            score = max(0.0, min(100.0, score))
            try:
                _get_redis().setex(cache_key, _SENTIMENT_TTL, str(score))
            except Exception:
                pass
            log.info("news.claude_sentiment", symbol=symbol, score=score)
            return score
        log.warning("news.claude_sentiment_error", symbol=symbol, status=r.status_code)
    except Exception as exc:
        log.warning("news.claude_sentiment_failed", symbol=symbol, error=str(exc))
    return None


def _label(score: float) -> str:
    if score >= 0.05:
        return "bullish"
    if score <= -0.05:
        return "bearish"
    return "neutral"


def _make(title: str, url: str, source: str, ts: int, thumb: str | None = None) -> NewsItem:
    score = _analyzer.polarity_scores(title)["compound"]
    return NewsItem(
        title=title, url=url, source=source,
        published_at=ts, sentiment=round(score, 3),
        sentiment_label=_label(score), thumbnail=thumb,
    )


def _yfinance_news(symbol: str) -> list[NewsItem]:
    """Fetch from yfinance and discard anything older than 7 days."""
    cutoff = int(time.time()) - _STALE_CUTOFF
    try:
        raw = yf.Ticker(symbol).news or []
    except Exception as exc:
        log.warning("news.yfinance_failed", symbol=symbol, error=str(exc))
        return []

    items: list[NewsItem] = []
    for item in raw:
        c = item.get("content") or item
        title = c.get("title") or item.get("title", "")
        if not title:
            continue

        url = (c.get("canonicalUrl") or {}).get("url") or item.get("link", "")
        source = (c.get("provider") or {}).get("displayName") or item.get("publisher", "")

        pub_date = c.get("pubDate") or ""
        if pub_date:
            try:
                ts = int(datetime.fromisoformat(pub_date.replace("Z", "+00:00")).timestamp())
            except Exception:
                ts = int(time.time())
        else:
            ts = item.get("providerPublishTime", int(time.time()))

        if ts < cutoff:
            continue  # stale — skip

        thumb = None
        try:
            resolutions = (c.get("thumbnail") or {}).get("resolutions") or []
            thumb = resolutions[0].get("url") if resolutions else None
            if not thumb:
                thumb = (c.get("thumbnail") or {}).get("originalUrl")
        except Exception:
            pass

        items.append(_make(title, url, source, ts, thumb))

    return items


def _google_news(query: str, limit: int = 15) -> list[NewsItem]:
    """Fetch from Google News RSS — no API key, always fresh."""
    try:
        q = urllib.parse.quote(f"{query} stock")
        feed_url = f"https://news.google.com/rss/search?q={q}&hl=en&gl=US&ceid=US:en"
        feed = feedparser.parse(feed_url)

        items: list[NewsItem] = []
        for entry in feed.entries[:limit]:
            title = entry.get("title", "").strip()
            if not title:
                continue
            link = entry.get("link", "")
            src = ""
            s = entry.get("source")
            if isinstance(s, dict):
                src = s.get("title", "")
            elif hasattr(s, "title"):
                src = s.title

            published = entry.get("published_parsed")
            if published:
                ts = int(datetime(*published[:6], tzinfo=timezone.utc).timestamp())
            else:
                ts = int(time.time())

            items.append(_make(title, link, src or "Google News", ts))
        return items
    except Exception as exc:
        log.warning("news.google_failed", query=query, error=str(exc))
        return []


def _merge(primary: list[NewsItem], supplement: list[NewsItem], limit: int) -> list[NewsItem]:
    seen: set[str] = set()
    merged: list[NewsItem] = []
    for item in sorted(primary + supplement, key=lambda x: x.published_at, reverse=True):
        key = item.title[:60].lower().strip()
        if key not in seen:
            seen.add(key)
            merged.append(item)
    return merged[:limit]


@router.get("/{symbol}/news", response_model=list[NewsItem])
def get_news(
    symbol: str,
    limit: int = Query(12, le=30),
    sources: str = Query("yfinance,google", description="Comma-separated list: yfinance, google"),
    session: Session = Depends(get_session),
):
    enabled = {s.strip().lower() for s in sources.split(",")}
    use_yf = "yfinance" in enabled
    use_google = "google" in enabled

    # Cache key includes active sources so toggling bypasses stale cache
    cache_key = f"stockai:news:{symbol.upper()}:{sources}"
    try:
        cached = _get_redis().get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    # Resolve company name for better Google News query
    name = session.execute(
        select(Stock.name).where(Stock.symbol == symbol)
    ).scalar_one_or_none() or symbol

    is_hk = symbol.upper().endswith(".HK")
    yf_items = _yfinance_news(symbol) if use_yf else []

    # Supplement with Google News when: user enabled it, HK stock, or yfinance sparse
    if use_google and (is_hk or len(yf_items) < 3 or not use_yf):
        google_items = _google_news(name)
        results = _merge(yf_items, google_items, limit)
        log.info("news.merged", symbol=symbol, yf=len(yf_items), google=len(google_items), total=len(results))
    elif use_yf:
        results = sorted(yf_items, key=lambda x: x.published_at, reverse=True)[:limit]
        log.info("news.yfinance_only", symbol=symbol, count=len(results))
    else:
        results = []

    if not results:
        raise HTTPException(404, f"No recent news found for {symbol} with sources={sources!r}")

    try:
        _get_redis().setex(cache_key, _NEWS_TTL, json.dumps([r.model_dump() for r in results]))
    except Exception:
        pass

    return results


_PULSE_QUERIES = ["stock market", "S&P 500", "Federal Reserve"]
_PULSE_TTL = 30 * 60  # 30 min — same cadence as the per-symbol news cache
_PULSE_CACHE_KEY = "stockai:market_pulse"


class MarketPulseResponse(BaseModel):
    score: float          # 0-100, 50 = neutral — same scale as SentimentResponse
    label: str            # positive | negative | neutral
    source: str            # claude | vader
    themes: list[str]      # top ~3 recurring themes across the sampled headlines
    headlines: list[NewsItem]
    generated_at: int


def _claude_market_themes(titles: list[str]) -> dict | None:
    """One Haiku call: market-mood score (0-100) + up to 3 recurring themes.

    Mirrors _claude_sentiment()'s call shape exactly (same model, same fail-open
    contract) but asks for themes too since this is a market-level digest, not
    a per-symbol score — themes are the part a headline list alone doesn't
    surface at a glance.
    """
    api_key = _get_claude_key()
    if not api_key or not titles:
        return None
    headlines = "\n".join(f"- {t}" for t in titles[:10])
    try:
        with httpx.Client(timeout=10) as client:
            r = client.post(
                "https://api.anthropic.com/v1/messages",
                json={
                    "model": "claude-haiku-4-5-20251001",
                    "max_tokens": 150,
                    "system": (
                        "You are a financial news analyst. Given market-level news headlines, "
                        "return JSON only: {\"score\": <integer 0-100>, \"themes\": [<up to 3 short "
                        "theme strings>]} where score 0=very negative for the overall market, "
                        "50=neutral, 100=very positive. Themes should be short (3-6 words) "
                        "recurring topics across the headlines, e.g. \"Fed rate-cut expectations\" "
                        "or \"AI capex spending\". No other text."
                    ),
                    "messages": [{"role": "user", "content": f"Headlines:\n{headlines}"}],
                },
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
            )
        if r.status_code == 200:
            text = _strip_markdown_fence(r.json()["content"][0]["text"])
            parsed = json.loads(text)
            score = max(0.0, min(100.0, float(parsed.get("score", 50))))
            themes = [str(t).strip() for t in (parsed.get("themes") or []) if str(t).strip()][:3]
            log.info("news.market_pulse_claude", score=score, themes=themes)
            return {"score": score, "themes": themes}
        log.warning("news.market_pulse_claude_error", status=r.status_code)
    except Exception as exc:
        log.warning("news.market_pulse_claude_failed", error=str(exc))
    return None


@router.get("/market/pulse", response_model=MarketPulseResponse)
def get_market_pulse():
    """Market-level (not per-symbol) news sentiment digest.

    Explicitly a passive 30-min-cadence dashboard card, NOT a real-time
    breaking-news alert feature — see T249-MARKETMOVER-P4's tracker note for
    why: free sources (Google News RSS here) are 15-60 min stale with no
    materiality signal, so this is deliberately not wired into any
    notification path.
    """
    try:
        cached = _get_redis().get(_PULSE_CACHE_KEY)
        if cached:
            return json.loads(cached)
    except Exception:
        pass

    all_items: list[NewsItem] = []
    for q in _PULSE_QUERIES:
        all_items.extend(_google_news(q, limit=10))
    headlines = _merge(all_items, [], limit=10)

    titles = [h.title for h in headlines]
    claude_result = _claude_market_themes(titles) if titles else None

    if claude_result is not None:
        score = claude_result["score"]
        themes = claude_result["themes"]
        source = "claude"
    else:
        scores = [max(0.0, min(100.0, h.sentiment * 50 + 50)) for h in headlines]
        score = round(sum(scores) / len(scores), 1) if scores else 50.0
        themes = []
        source = "vader"

    label = "positive" if score >= 60 else ("negative" if score <= 40 else "neutral")

    result = MarketPulseResponse(
        score=score, label=label, source=source, themes=themes,
        headlines=headlines, generated_at=int(time.time()),
    )
    try:
        _get_redis().setex(_PULSE_CACHE_KEY, _PULSE_TTL, result.model_dump_json())
    except Exception:
        pass
    return result


@router.get("/{symbol}/news/sentiment", response_model=SentimentResponse)
def get_news_sentiment(symbol: str, session: Session = Depends(get_session)):
    """Aggregate news sentiment score for a symbol (0-100, 50=neutral).

    Uses Claude Haiku if a key is configured (admin Settings page, or the
    ANTHROPIC_API_KEY env var as a fallback) — 4h cache — otherwise falls back to
    enhanced VADER average (financial lexicon corrections applied — significantly
    more accurate than stock VADER for financial headlines).
    """
    # Try per-symbol Claude cache first (avoids repeat yfinance fetches)
    if _get_claude_key():
        cache_key = f"stockai:news_sentiment:{symbol.upper()}"
        try:
            cached = _get_redis().get(cache_key)
            if cached:
                score = float(cached)
                label = "positive" if score >= 60 else ("negative" if score <= 40 else "neutral")
                return SentimentResponse(score=score, label=label, source="claude")
        except Exception:
            pass

    # Fetch articles — reuse 30-min news cache if warm
    news_cache_key = f"stockai:news:{symbol.upper()}:yfinance"
    articles: list[dict] = []
    try:
        raw = _get_redis().get(news_cache_key)
        if raw:
            articles = json.loads(raw)
    except Exception:
        pass

    if not articles:
        items = _yfinance_news(symbol)
        articles = [i.model_dump() for i in items]

    if not articles:
        return SentimentResponse(score=50.0, label="neutral", source="vader")

    titles = [a["title"] for a in articles if a.get("title")]

    # Attempt Claude (writes to Redis on success)
    claude_score = _claude_sentiment(symbol, titles)
    if claude_score is not None:
        label = "positive" if claude_score >= 60 else ("negative" if claude_score <= 40 else "neutral")
        return SentimentResponse(score=claude_score, label=label, source="claude")

    # Enhanced VADER fallback
    scores = [
        max(0.0, min(100.0, float(a["sentiment"]) * 50 + 50))
        for a in articles
        if isinstance(a.get("sentiment"), (int, float))
    ]
    avg = round(sum(scores) / len(scores), 1) if scores else 50.0
    label = "positive" if avg >= 60 else ("negative" if avg <= 40 else "neutral")
    return SentimentResponse(score=avg, label=label, source="vader")
