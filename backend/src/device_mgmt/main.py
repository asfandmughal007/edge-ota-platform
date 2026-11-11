"""Application factory.

Run locally with::

    uvicorn device_mgmt.main:create_app --factory --reload
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from device_mgmt import __version__
from device_mgmt.api import devices
from device_mgmt.config import Settings
from device_mgmt.db import models  # noqa: F401  (registers tables on Base.metadata)
from device_mgmt.db.session import Base, create_db_engine
from device_mgmt.errors import (
    DeviceAlreadyExistsError,
    DeviceIdMismatchError,
    DeviceNotFoundError,
    DomainError,
)
from device_mgmt.logging_setup import configure_logging
from device_mgmt.services.registry import Clock, utcnow

_STATUS_BY_ERROR: dict[type[DomainError], int] = {
    DeviceNotFoundError: status.HTTP_404_NOT_FOUND,
    DeviceAlreadyExistsError: status.HTTP_409_CONFLICT,
    DeviceIdMismatchError: status.HTTP_400_BAD_REQUEST,
}


def create_app(settings: Settings | None = None, clock: Clock = utcnow) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)
    engine = create_db_engine(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Fine for a single-instance dev setup; a real fleet backend would run
        # Alembic migrations as a separate deploy step instead.
        Base.metadata.create_all(engine)
        yield
        engine.dispose()

    app = FastAPI(
        title="Edge Device Management Service",
        version=__version__,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.clock = clock
    app.state.session_factory = sessionmaker(engine, expire_on_commit=False)

    @app.exception_handler(DomainError)
    async def handle_domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        code = _STATUS_BY_ERROR.get(type(exc), status.HTTP_400_BAD_REQUEST)
        return JSONResponse(status_code=code, content={"detail": str(exc)})

    @app.get("/healthz", tags=["ops"])
    def liveness() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(devices.router)
    return app
