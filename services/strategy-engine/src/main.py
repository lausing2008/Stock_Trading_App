from common.service import create_app
from db import engine
from sqlalchemy import text

from .api.routes import router


async def on_startup():
    # Connection health check only — migrations are owned by market-data to avoid
    # concurrent ACCESS EXCLUSIVE lock contention on startup (AUD19-DB2).
    with engine.connect() as conn:
        conn.execute(text("SELECT 1"))


app = create_app("strategy-engine", routers=[router], on_startup=on_startup)
