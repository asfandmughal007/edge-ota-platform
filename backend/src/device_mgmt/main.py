"""Application factory.

Run locally with::

    DEVICE_MGMT_ADMIN_API_KEY=<at least 16 characters> \\
        uvicorn device_mgmt.main:create_app --factory --reload

In production, terminate TLS at a reverse proxy (see docker-compose ``tls``
profile) and set ``DEVICE_MGMT_REQUIRE_HTTPS=true``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response, status
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from device_mgmt import __version__
from device_mgmt.api import deployments, devices, versions
from device_mgmt.config import Settings
from device_mgmt.db import models  # noqa: F401  (registers tables on Base.metadata)
from device_mgmt.db.session import Base, create_db_engine
from device_mgmt.errors import (
    DeploymentConflictError,
    DeploymentNotFoundError,
    DeviceAlreadyExistsError,
    DeviceIdMismatchError,
    DeviceNotFoundError,
    DomainError,
    VersionAlreadyExistsError,
    VersionNotFoundError,
)
from device_mgmt.logging_setup import configure_logging
from device_mgmt.services.registry import Clock, utcnow

_STATUS_BY_ERROR: dict[type[DomainError], int] = {
    DeviceNotFoundError: status.HTTP_404_NOT_FOUND,
    DeviceAlreadyExistsError: status.HTTP_409_CONFLICT,
    DeviceIdMismatchError: status.HTTP_400_BAD_REQUEST,
    VersionNotFoundError: status.HTTP_404_NOT_FOUND,
    VersionAlreadyExistsError: status.HTTP_409_CONFLICT,
    DeploymentNotFoundError: status.HTTP_404_NOT_FOUND,
    DeploymentConflictError: status.HTTP_409_CONFLICT,
}

# Probes run inside the private network, usually over plain HTTP.
_HTTPS_EXEMPT_PATHS = frozenset({"/healthz", "/readyz"})


def create_app(settings: Settings | None = None, clock: Clock = utcnow) -> FastAPI:
    settings = settings or Settings()  # type: ignore[call-arg]  # admin key comes from the env
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
    app.state.engine = engine

    @app.middleware("http")
    async def security_policy(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        if (
            settings.require_https
            and request.url.scheme != "https"
            and request.url.path not in _HTTPS_EXEMPT_PATHS
        ):
            # Refuse rather than redirect: an API client that was sent a token
            # over plain HTTP has already leaked it, so fail loudly instead.
            return JSONResponse(
                status_code=status.HTTP_400_BAD_REQUEST, content={"detail": "HTTPS is required"}
            )
        response = await call_next(request)
        # Responses can contain freshly issued device tokens: never cache them.
        response.headers["Cache-Control"] = "no-store"
        response.headers["X-Content-Type-Options"] = "nosniff"
        if settings.require_https:
            response.headers["Strict-Transport-Security"] = "max-age=63072000; includeSubDomains"
        return response

    @app.exception_handler(DomainError)
    async def handle_domain_error(_request: Request, exc: DomainError) -> JSONResponse:
        code = _STATUS_BY_ERROR.get(type(exc), status.HTTP_400_BAD_REQUEST)
        return JSONResponse(status_code=code, content={"detail": str(exc)})

    @app.get("/healthz", tags=["ops"])
    def liveness() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(devices.router)
    app.include_router(versions.router)
    app.include_router(deployments.router)
    return app
