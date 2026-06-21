from common.service import create_app

from .api.ai_proxy import router as ai_router
from .api.aggregate import router as aggregate_router
from .api.health import router as health_router
from .api.proxy import router as proxy_router

app = create_app(
    "api-gateway",
    routers=[ai_router, aggregate_router, health_router, proxy_router],
)
