"""Factory for building a uniform FastAPI service across the platform."""
from collections.abc import Iterable
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .logging import configure_logging, get_logger


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

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.get("/health", tags=["meta"])
    def health():
        return {"status": "ok", "service": name, "version": version}

    for r in routers:
        app.include_router(r)

    return app
