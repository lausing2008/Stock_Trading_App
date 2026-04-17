from common.service import create_app
from db import init_db

from .api.routes import router


async def on_startup():
    init_db()


app = create_app("strategy-engine", routers=[router], on_startup=on_startup)
