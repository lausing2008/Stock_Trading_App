"""News endpoint — fetch recent headlines with sentiment scores."""
from __future__ import annotations

import time

import yfinance as yf
from fastapi import APIRouter, HTTPException, Query
from pydantic import BaseModel
from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from common.logging import get_logger

router = APIRouter(prefix="/stocks", tags=["news"])
log = get_logger("news")
_analyzer = SentimentIntensityAnalyzer()


class NewsItem(BaseModel):
    title: str
    url: str
    source: str
    published_at: int  # unix timestamp
    sentiment: float   # -1 (bearish) to +1 (bullish)
    sentiment_label: str
    thumbnail: str | None = None


def _label(score: float) -> str:
    if score >= 0.05:
        return "bullish"
    if score <= -0.05:
        return "bearish"
    return "neutral"


@router.get("/{symbol}/news", response_model=list[NewsItem])
def get_news(symbol: str, limit: int = Query(12, le=30)):
    try:
        ticker = yf.Ticker(symbol)
        raw = ticker.news or []
    except Exception as exc:
        log.warning("news.fetch_failed", symbol=symbol, error=str(exc))
        raise HTTPException(502, "Failed to fetch news")

    results: list[NewsItem] = []
    for item in raw[:limit]:
        # yfinance 1.x nests data under item["content"]
        c = item.get("content") or item
        title = c.get("title") or item.get("title", "")
        if not title:
            continue
        score = _analyzer.polarity_scores(title)["compound"]
        # URL
        url = (c.get("canonicalUrl") or {}).get("url") or item.get("link", "")
        # Source
        source = (c.get("provider") or {}).get("displayName") or item.get("publisher", "")
        # Published timestamp
        pub_date = c.get("pubDate") or ""
        if pub_date:
            from datetime import datetime, timezone
            try:
                ts = int(datetime.fromisoformat(pub_date.replace("Z", "+00:00")).timestamp())
            except Exception:
                ts = int(time.time())
        else:
            ts = item.get("providerPublishTime", int(time.time()))
        # Thumbnail
        thumb = None
        try:
            resolutions = (c.get("thumbnail") or {}).get("resolutions") or []
            if resolutions:
                thumb = resolutions[0].get("url")
            if not thumb:
                thumb = (c.get("thumbnail") or {}).get("originalUrl")
        except Exception:
            pass
        results.append(NewsItem(
            title=title,
            url=url,
            source=source,
            published_at=ts,
            sentiment=round(score, 3),
            sentiment_label=_label(score),
            thumbnail=thumb,
        ))
    return results
