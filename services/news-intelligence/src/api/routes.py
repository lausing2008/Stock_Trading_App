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
    )
