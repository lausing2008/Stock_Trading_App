"""Event Intelligence Platform — FastAPI service entry point (port 8010)."""
from common.service import create_app

from .api.routes import router
from .scheduler import start_scheduler

app = create_app(
    "event-intelligence",
    routers=[router],
    on_startup=start_scheduler,
)
