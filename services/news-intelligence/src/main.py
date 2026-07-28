"""News Intelligence — FastAPI service entry point (port 8011).

Ingests real-time financial headlines from 4 sources (PR Newswire, Business Wire, SEC EDGAR
real-time filings, Alpaca news WebSocket), classifies them (sentiment/materiality/category via
Claude Haiku), matches them against this app's own tracked stock universe, and exposes a hot-
news Redis flag consumed by signal-engine's BUY gate. See shared/db/models.py's RealtimeNewsItem
docstring and this repo's CLAUDE.md for the full design rationale, including why the original
DESIGN_REALTIME_NEWS_FEED_2026-07-25.md design (built around a dead Stock Titan RSS URL) was
abandoned in favor of this rewrite.
"""
from common.service import create_app

from .api.routes import router
from .scheduler import start_scheduler

app = create_app(
    "news-intelligence",
    routers=[router],
    on_startup=start_scheduler,
)
