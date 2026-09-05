"""News-intelligence read API — the ingestion pipeline (scheduler.py) writes; this only reads."""
from __future__ import annotations

from fastapi import APIRouter, Query
from pydantic import BaseModel

from ..services.storage import is_hot, recent_items

router = APIRouter(prefix="/news", tags=["news"])


class NewsItemResponse(BaseModel):
    id: int
    symbol: str | None
    headline: str
    source: str
    url: str | None
    sentiment_score: float | None
    sentiment_label: str | None
    is_material: bool
    category: str | None
    published_at: str


class HotNewsResponse(BaseModel):
    symbol: str
    hot: bool
    headline: str | None = None
    sentiment_label: str | None = None
    # AUD-HOTNEWS-TS-STRIPPED: _mark_hot() writes a "ts" into the Redis flag specifically so
    # consumers can DECAY the hot-news penalty by age, but this response_model omitted it and
    # FastAPI silently strips any field not declared here. signal-engine's consumer
    # (generators/signals.py, `if hot_news and hot_news.get("ts")`) therefore always saw None,
    # so _hot_news_age_hours was ALWAYS None and the age-decay branch could never fire —
    # every hot-flagged signal took the full first-hour 0.70 compression forever, including a
    # 119-minute-old headline that was supposed to get the softer 0.85 second-hour value.
    # Confirmed against live production: GET /news/hot/AAPL returned no "ts" key at all.
    ts: str | None = None


@router.get("", response_model=list[NewsItemResponse])
def list_news(
    symbol: str | None = Query(None, description="Filter to one tracked symbol; omit for market-wide feed"),
    limit: int = Query(50, ge=1, le=200),
    since_hours: int = Query(48, ge=1, le=24 * 14),
):
    rows = recent_items(symbol, limit, since_hours)
    return [
        NewsItemResponse(
            id=r.id, symbol=r.symbol, headline=r.headline, source=r.source, url=r.url,
            sentiment_score=r.sentiment_score, sentiment_label=r.sentiment_label,
            is_material=r.is_material, category=r.category,
            published_at=r.published_at.isoformat(),
        )
        for r in rows
    ]


@router.get("/hot/{symbol}", response_model=HotNewsResponse)
def hot_news(symbol: str):
    """Read-side for signal-engine's BUY gate (called over HTTP, not a direct import — a
    separate service) and for the frontend to show a "hot news" badge on a stock page."""
    flag = is_hot(symbol)
    if not flag:
        return HotNewsResponse(symbol=symbol.upper(), hot=False)
    return HotNewsResponse(
        symbol=symbol.upper(), hot=True,
        headline=flag.get("headline"), sentiment_label=flag.get("sentiment_label"),
        # AUD-HOTNEWS-TS-STRIPPED: the field the age-decay consumer actually gates on.
        ts=flag.get("ts"),
    )
