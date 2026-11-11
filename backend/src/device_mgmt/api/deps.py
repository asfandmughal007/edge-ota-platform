"""FastAPI dependencies."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Path, Request
from sqlalchemy.orm import Session

from device_mgmt.schemas import DEVICE_ID_PATTERN
from device_mgmt.services.deployments import DeploymentController
from device_mgmt.services.registry import DeviceRegistry
from device_mgmt.services.versions import VersionCatalog

DeviceIdPath = Annotated[str, Path(min_length=1, max_length=64, pattern=DEVICE_ID_PATTERN)]


def get_session(request: Request) -> Iterator[Session]:
    # Services commit explicitly, so a failed write surfaces as an error
    # response instead of being lost in dependency teardown.
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


def get_registry(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> DeviceRegistry:
    return DeviceRegistry(session, request.app.state.settings, clock=request.app.state.clock)


def get_version_catalog(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> VersionCatalog:
    return VersionCatalog(session, clock=request.app.state.clock)


def get_deployment_controller(
    request: Request, session: Annotated[Session, Depends(get_session)]
) -> DeploymentController:
    return DeploymentController(session, clock=request.app.state.clock)


RegistryDep = Annotated[DeviceRegistry, Depends(get_registry)]
VersionCatalogDep = Annotated[VersionCatalog, Depends(get_version_catalog)]
DeploymentControllerDep = Annotated[DeploymentController, Depends(get_deployment_controller)]
