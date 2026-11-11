"""Device registry endpoints."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Query, status

from device_mgmt.api.deps import AdminRequired, DeviceIdPath, DevicePathAuth, RegistryDep
from device_mgmt.schemas import (
    DeviceCreate,
    DeviceEventRead,
    DeviceHealth,
    DeviceRead,
    DeviceRegistered,
    DeviceTokenRead,
    HeartbeatAck,
    HeartbeatIn,
)

router = APIRouter(prefix="/devices", tags=["devices"])

_UNAUTHORIZED: dict[int | str, dict[str, Any]] = {
    401: {"description": "Missing or invalid credentials"}
}


@router.post(
    "",
    response_model=DeviceRegistered,
    status_code=status.HTTP_201_CREATED,
    dependencies=[AdminRequired],
    responses={**_UNAUTHORIZED, 409: {"description": "Device ID already registered"}},
)
def register_device(payload: DeviceCreate, registry: RegistryDep) -> DeviceRegistered:
    """Register a device. The response contains its token, shown only once."""
    return registry.register(payload)


@router.get(
    "", response_model=list[DeviceRead], dependencies=[AdminRequired], responses=_UNAUTHORIZED
)
def list_devices(
    registry: RegistryDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DeviceRead]:
    return registry.list(limit=limit, offset=offset)


@router.get(
    "/{device_id}",
    response_model=DeviceRead,
    dependencies=[AdminRequired],
    responses={**_UNAUTHORIZED, 404: {}},
)
def get_device(device_id: DeviceIdPath, registry: RegistryDep) -> DeviceRead:
    return registry.get(device_id)


@router.get(
    "/{device_id}/health",
    response_model=DeviceHealth,
    dependencies=[AdminRequired],
    responses={**_UNAUTHORIZED, 404: {}},
)
def get_device_health(device_id: DeviceIdPath, registry: RegistryDep) -> DeviceHealth:
    return registry.health(device_id)


@router.get(
    "/{device_id}/events",
    response_model=list[DeviceEventRead],
    dependencies=[AdminRequired],
    responses={**_UNAUTHORIZED, 404: {}},
)
def list_device_events(
    device_id: DeviceIdPath,
    registry: RegistryDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DeviceEventRead]:
    """Status changes and application health changes, newest first."""
    return registry.events(device_id, limit=limit, offset=offset)


@router.post(
    "/{device_id}/token",
    response_model=DeviceTokenRead,
    dependencies=[AdminRequired],
    responses={**_UNAUTHORIZED, 404: {}},
)
def rotate_device_token(device_id: DeviceIdPath, registry: RegistryDep) -> DeviceTokenRead:
    """Issue a new token and invalidate the old one immediately."""
    return registry.rotate_token(device_id)


@router.post(
    "/{device_id}/heartbeat",
    response_model=HeartbeatAck,
    status_code=status.HTTP_202_ACCEPTED,
    responses={
        **_UNAUTHORIZED,
        400: {"description": "Body device_id does not match the path"},
        403: {"description": "Token belongs to a different device"},
    },
)
def ingest_heartbeat(
    device_id: DeviceIdPath,
    heartbeat: HeartbeatIn,
    registry: RegistryDep,
    _identity: DevicePathAuth,
) -> HeartbeatAck:
    """Device-authenticated: accepted only with this device's own token."""
    return registry.record_heartbeat(device_id, heartbeat)
