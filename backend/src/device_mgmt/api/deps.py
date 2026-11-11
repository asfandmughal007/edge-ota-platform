"""FastAPI dependencies, including authentication.

Two separate trust levels, deliberately not interchangeable:

* operators present ``X-API-Key`` and may manage the fleet;
* devices present ``Authorization: Bearer <device token>`` and may act only on
  their own device: send heartbeats, fetch their next deployment, report its
  progress.

A device token is rejected on operator endpoints and the operator key is
rejected on device endpoints, so a compromised device cannot read or change
anything beyond itself.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import Depends, HTTPException, Path, Request, status
from fastapi.security import APIKeyHeader, HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from device_mgmt.db.repositories import DeviceRepository
from device_mgmt.schemas import DEVICE_ID_PATTERN
from device_mgmt.security import hash_token, keys_match
from device_mgmt.services.deployments import DeploymentController
from device_mgmt.services.registry import DeviceRegistry
from device_mgmt.services.versions import VersionCatalog

DeviceIdPath = Annotated[str, Path(min_length=1, max_length=64, pattern=DEVICE_ID_PATTERN)]

_api_key_scheme = APIKeyHeader(
    name="X-API-Key", auto_error=False, description="Operator API key (fleet management)."
)
_bearer_scheme = HTTPBearer(
    auto_error=False, description="Per-device token issued at registration (device agents)."
)


def get_session(request: Request) -> Iterator[Session]:
    # Services commit explicitly, so a failed write surfaces as an error
    # response instead of being lost in dependency teardown.
    session: Session = request.app.state.session_factory()
    try:
        yield session
    finally:
        session.close()


SessionDep = Annotated[Session, Depends(get_session)]


# --- authentication ----------------------------------------------------------


def require_admin(
    request: Request, api_key: Annotated[str | None, Depends(_api_key_scheme)]
) -> None:
    expected = request.app.state.settings.admin_api_key.get_secret_value()
    if api_key is None or not keys_match(api_key, expected):
        raise HTTPException(
            status.HTTP_401_UNAUTHORIZED,
            detail="invalid or missing API key",
            headers={"WWW-Authenticate": "ApiKey"},
        )


@dataclass(frozen=True, slots=True)
class DeviceIdentity:
    device_id: str


def authenticate_device(
    session: SessionDep,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
) -> DeviceIdentity:
    # One generic message for "no token", "wrong token" and "unknown device",
    # so the response never reveals which device IDs exist.
    unauthorized = HTTPException(
        status.HTTP_401_UNAUTHORIZED,
        detail="invalid or missing device token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    if credentials is None:
        raise unauthorized
    device = DeviceRepository(session).get_by_token_hash(hash_token(credentials.credentials))
    if device is None:
        raise unauthorized
    return DeviceIdentity(device.device_id)


def authorize_path_device(
    device_id: DeviceIdPath,
    identity: Annotated[DeviceIdentity, Depends(authenticate_device)],
) -> DeviceIdentity:
    """The authenticated device must be the one named in the URL."""
    if identity.device_id != device_id:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN, detail="device token is not valid for this device"
        )
    return identity


AdminRequired = Depends(require_admin)
DeviceAuth = Annotated[DeviceIdentity, Depends(authenticate_device)]
DevicePathAuth = Annotated[DeviceIdentity, Depends(authorize_path_device)]


# --- services ----------------------------------------------------------------


def get_registry(request: Request, session: SessionDep) -> DeviceRegistry:
    return DeviceRegistry(session, request.app.state.settings, clock=request.app.state.clock)


def get_version_catalog(request: Request, session: SessionDep) -> VersionCatalog:
    return VersionCatalog(session, clock=request.app.state.clock)


def get_deployment_controller(request: Request, session: SessionDep) -> DeploymentController:
    return DeploymentController(session, clock=request.app.state.clock)


RegistryDep = Annotated[DeviceRegistry, Depends(get_registry)]
VersionCatalogDep = Annotated[VersionCatalog, Depends(get_version_catalog)]
DeploymentControllerDep = Annotated[DeploymentController, Depends(get_deployment_controller)]
