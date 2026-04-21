"""Market Data Service — entrypoint."""
from common.service import create_app
from db import init_db

from .api.routes import router as data_router
from .api.admin import router as admin_router
from .api.watchlist import router as watchlist_router
from .api.news import router as news_router
from .services.scheduler import start_scheduler


async def on_startup():
    init_db()
    start_scheduler()


app = create_app(
    "market-data-service",
    routers=[data_router, admin_router, watchlist_router, news_router],
    on_startup=on_startup,
)
