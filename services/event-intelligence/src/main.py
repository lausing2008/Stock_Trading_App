"""Event Intelligence Platform — FastAPI service entry point (port 8010)."""
from common.service import create_app
from db import init_db

from .api.routes import router
from .scheduler import start_scheduler

# IF-04: found this service had NO init_db() call at all — only market-data and research-engine
# do (confirmed via grep across every services/*/src/main.py). Base.metadata.create_all() is
# idempotent (safe to call from multiple services), so this newly-added CrossAssetReading table
# silently depended on one of THOSE two services happening to restart first to actually get
# created — a real bootstrap gap, not specific to this one table. Closing it here so a future
# new table doesn't depend on which service restarts first.
async def _on_startup():
    init_db()
    await start_scheduler()


app = create_app(
    "event-intelligence",
    routers=[router],
    on_startup=_on_startup,
)
