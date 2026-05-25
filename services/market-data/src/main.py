"""Market Data Service — entrypoint."""
from common.service import create_app
from db import init_db

from .api.routes import router as data_router
from .api.admin import router as admin_router
from .api.auth import router as auth_router
from .api.watchlist import router as watchlist_router, lists_router as watchlists_router
from .api.news import router as news_router
from .api.alerts import router as alerts_router
from .api.signal_alerts import router as signal_alerts_router
from .api.congress import router as congress_router
from .api.journal import router as journal_router
from .api.board import router as board_router
from .api.positions import router as positions_router
from .api.app_notifications import router as app_notifications_router
from .services.scheduler import start_scheduler


async def on_startup():
    init_db()
    start_scheduler()


app = create_app(
    "market-data-service",
    routers=[data_router, admin_router, auth_router, watchlists_router, watchlist_router, news_router, alerts_router, signal_alerts_router, congress_router, journal_router, board_router, positions_router, app_notifications_router],
    on_startup=on_startup,
)
