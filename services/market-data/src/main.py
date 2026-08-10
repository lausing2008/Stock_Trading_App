"""Market Data Service — entrypoint."""
import asyncio

from common.service import create_app
from db import init_db

from .api.routes import router as data_router
from .api.admin import router as admin_router
from .api.auth import router as auth_router
from .api.watchlist import router as watchlist_router, lists_router as watchlists_router
from .api.news import router as news_router
from .api.alerts import router as alerts_router
from .api.signal_alerts import router as signal_alerts_router
from .api.journal import router as journal_router
from .api.board import router as board_router
from .api.positions import router as positions_router
from .api.app_notifications import router as app_notifications_router
from .api.paper_portfolio import router as paper_portfolio_router
from .api.broker import router as broker_router
from .api.rl import router as rl_router
from .api.push import router as push_router
from .services.scheduler import start_scheduler
from .services.alpaca_quote_stream import run_quote_stream

_quote_stream_stop = asyncio.Event()


async def on_startup():
    init_db()
    start_scheduler()
    # T230-DATA-STREAMING-QUOTES: long-lived real-time quote WebSocket task, started once at
    # startup on FastAPI's own event loop (this is safe here specifically because on_startup is
    # itself `async def`, so a real running loop already exists by the time this executes —
    # unlike start_scheduler()'s BackgroundScheduler, which runs on its own separate thread and
    # has no event loop of its own). Matches news-intelligence/src/scheduler.py's own
    # asyncio.create_task(run_alpaca_stream(...)) wiring exactly. Fails open on its own — a
    # missing/invalid Alpaca credential just means this task idles, never affecting any other
    # startup step or endpoint.
    asyncio.create_task(run_quote_stream(_quote_stream_stop))


app = create_app(
    "market-data-service",
    routers=[data_router, admin_router, auth_router, watchlists_router, watchlist_router, news_router, alerts_router, signal_alerts_router, journal_router, board_router, positions_router, app_notifications_router, paper_portfolio_router, broker_router, rl_router, push_router],
    on_startup=on_startup,
)
