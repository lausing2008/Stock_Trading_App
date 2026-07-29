from common.service import create_app
from db import init_db

from .api.ai_proxy import router as ai_router
from .api.routes import router


async def on_startup():
    # CLAUDE-API-COST-AUDIT / research report durability: research-engine had NO DB access
    # until this fix — generate_research()'s reports lived only in an in-memory dict and were
    # lost on every restart. init_db() is idempotent (create_all() + safe migrations, the same
    # call every other service already makes at startup) — safe to add here for the first time.
    init_db()


app = create_app("research-engine", routers=[router, ai_router], on_startup=on_startup)
