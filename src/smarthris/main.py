"""FastAPI application entrypoint."""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from smarthris.api.routers import employees, health
from smarthris.config import Settings, get_settings
from smarthris.core.handlers import register_exception_handlers
from smarthris.db.session import create_db_engine, create_session_factory


def create_app(settings: Settings | None = None) -> FastAPI:
    """Application factory used by uvicorn and tests."""
    cfg = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        """Create shared resources at startup and dispose them on shutdown."""
        engine = create_db_engine(cfg)
        app.state.db_engine = engine
        app.state.db_session_factory = create_session_factory(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title=cfg.app_name,
        debug=cfg.debug,
        lifespan=lifespan,
    )
    register_exception_handlers(app)
    app.include_router(health.router)
    app.include_router(employees.router)
    return app


app = create_app()


def run() -> None:
    """Console script: run uvicorn with settings from the environment."""
    cfg = get_settings()
    uvicorn.run(
        "smarthris.main:app",
        host=cfg.api_host,
        port=cfg.api_port,
        reload=cfg.debug and cfg.app_env == "development",
    )


if __name__ == "__main__":
    run()
