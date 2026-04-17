from common.service import create_app

from .api.proxy import router as proxy_router
from .api.aggregate import router as aggregate_router

app = create_app(
    "api-gateway",
    routers=[proxy_router, aggregate_router],
)
