"""Factory for building a uniform FastAPI service across the platform."""
import uuid
from collections.abc import Iterable
from contextlib import asynccontextmanager

import structlog
from fastapi import APIRouter, FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from .config import get_settings
from .logging import configure_logging, get_logger


class CorrelationIdMiddleware(BaseHTTPMiddleware):
    """Generate X-Request-ID if absent; bind to structlog context for every request."""

    async def dispatch(self, request: Request, call_next) -> Response:
        req_id = request.headers.get("X-Request-ID") or str(uuid.uuid4())
        with structlog.contextvars.bound_contextvars(request_id=req_id):
            response = await call_next(request)
        response.headers["X-Request-ID"] = req_id
        return response


def create_app(
    name: str,
    routers: Iterable[APIRouter] = (),
    on_startup=None,
    version: str = "0.1.0",
) -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    logger = get_logger(name)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("service.start", name=name, env=settings.env, version=version)
        if on_startup is not None:
            await on_startup() if callable(on_startup) else None
        yield
        logger.info("service.stop", name=name)

    app = FastAPI(title=name, version=version, lifespan=lifespan)

    # Correlation ID middleware — must be added before CORS so the ID is
    # bound for the full request lifecycle including CORS pre-flight handling.
    app.add_middleware(CorrelationIdMiddleware)

    if not settings.cors_origins and settings.env != "development":
        import warnings
        warnings.warn(
            "CORS_ORIGINS is not set — defaulting to '*' in non-development env. "
            "Set CORS_ORIGINS=https://yourdomain.com in your .env to lock down CORS.",
            stacklevel=2,
        )
    allowed_origins = (
        [o.strip() for o in settings.cors_origins.split(",") if o.strip()]
        if settings.cors_origins
        else ["*"]
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok", "service": name, "version": version}

    for r in routers:
        app.include_router(r)

    return app
