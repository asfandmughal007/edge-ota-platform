"""Device registry endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Query, status

from device_mgmt.api.deps import DeviceIdPath, RegistryDep
from device_mgmt.schemas import DeviceCreate, DeviceHealth, DeviceRead, HeartbeatAck, HeartbeatIn

router = APIRouter(prefix="/devices", tags=["devices"])


@router.post(
    "",
    response_model=DeviceRead,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Device ID already registered"}},
)
def register_device(payload: DeviceCreate, registry: RegistryDep) -> DeviceRead:
    return registry.register(payload)


@router.get("", response_model=list[DeviceRead])
def list_devices(
    registry: RegistryDep,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> list[DeviceRead]:
    return registry.list(limit=limit, offset=offset)


@router.get("/{device_id}", response_model=DeviceRead, responses={404: {}})
def get_device(device_id: DeviceIdPath, registry: RegistryDep) -> DeviceRead:
    return registry.get(device_id)


@router.get("/{device_id}/health", response_model=DeviceHealth, responses={404: {}})
def get_device_health(device_id: DeviceIdPath, registry: RegistryDep) -> DeviceHealth:
    return registry.health(device_id)


@router.post(
    "/{device_id}/heartbeat",
    response_model=HeartbeatAck,
    status_code=status.HTTP_202_ACCEPTED,
    responses={400: {"description": "Body device_id does not match the path"}, 404: {}},
)
def ingest_heartbeat(
    device_id: DeviceIdPath, heartbeat: HeartbeatIn, registry: RegistryDep
) -> HeartbeatAck:
    return registry.record_heartbeat(device_id, heartbeat)
