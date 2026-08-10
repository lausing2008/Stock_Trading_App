from common.service import create_app

from .api.aggregate import router as aggregate_router
from .api.health import router as health_router
from .api.quote_ws import router as quote_ws_router
from .api.proxy import router as proxy_router

app = create_app(
    "api-gateway",
    # T230-DATA-STREAMING-QUOTES: quote_ws_router registered before proxy_router as a defensive
    # convention (matching this repo's own BUG233-ROUTERORDER lesson) — in practice this does
    # NOT matter here, since a WebSocket upgrade request and an HTTP request are genuinely
    # distinct ASGI scope types (confirmed directly: Starlette routes them through separate
    # Route/WebSocketRoute matching, so proxy_router's HTTP-only catch-all can never shadow a
    # WebSocket path regardless of registration order) — but costs nothing to keep first anyway.
    routers=[aggregate_router, health_router, quote_ws_router, proxy_router],
)
