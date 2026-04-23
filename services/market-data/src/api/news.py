"""News endpoint — recent headlines with sentiment.

Strategy:
  1. Fetch from yfinance, discard articles older than 7 days.
  2. For HK stocks (.HK) or when yfinance returns < 3 fresh articles,
     supplement with Google News RSS (no API key needed).
  3. Merge, deduplicate by title prefix, sort newest-first.
  4. Cache result in Redis for 30 minutes.
"""
from __future__ import annotations

import json
import time
import urllib.parse
from datetime import datetime, timezone

import feedparser
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
_analyzer = SentimentIntensityAnalyzer()
_settings = get_settings()

_NEWS_TTL = 30 * 60        # 30 minutes
_STALE_CUTOFF = 7 * 86400  # discard yfinance articles older than 7 days

_redis: redis_lib.Redis | None = None


def _get_redis() -> redis_lib.Redis:
    global _redis
    if _redis is None:
        _redis = redis_lib.Redis.from_url(_settings.redis_url, decode_responses=True)
    return _redis


class NewsItem(BaseModel):
    title: str
    url: str
    source: str
    published_at: int
    sentiment: float
    sentiment_label: str
    thumbnail: str | None = None


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
