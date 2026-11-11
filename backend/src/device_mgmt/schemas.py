"""API request and response models."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StringConstraints

DEVICE_ID_PATTERN = r"^[A-Za-z0-9._-]+$"
DeviceId = Annotated[str, StringConstraints(min_length=1, max_length=64, pattern=DEVICE_ID_PATTERN)]
Version = Annotated[str, StringConstraints(min_length=1, max_length=64)]
Percent = Annotated[float, Field(ge=0, le=100)]


class DeviceStatus(StrEnum):
    NEVER_SEEN = "never_seen"
    ONLINE = "online"
    STALE = "stale"
    OFFLINE = "offline"


class DeviceCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: DeviceId
    hostname: Annotated[str, StringConstraints(max_length=255)] | None = None
    current_version: Version | None = None


class DeviceRead(BaseModel):
    device_id: str
    hostname: str | None
    current_version: str | None
    status: DeviceStatus
    registered_at: datetime
    last_seen_at: datetime | None


class NetworkStatus(BaseModel):
    interfaces_up: list[str]
    ip_address: str | None
    backend_reachable: bool | None


class HeartbeatIn(BaseModel):
    """Heartbeat as sent by the agent (see agent/src/edge_agent/models.py).

    The two services are deployed independently, so the contract is this JSON
    shape plus ``schema_version``, not a shared Python class.
    """

    schema_version: Literal[1] = 1
    device_id: DeviceId
    hostname: Annotated[str, StringConstraints(max_length=255)]
    software_version: Version
    cpu_percent: Percent
    memory_percent: Percent
    disk_percent: Percent
    uptime_seconds: Annotated[float, Field(ge=0)]
    network: NetworkStatus
    timestamp: AwareDatetime


class HeartbeatAck(BaseModel):
    device_id: str
    received_at: datetime


class HealthMetrics(BaseModel):
    cpu_percent: float
    memory_percent: float
    disk_percent: float
    uptime_seconds: float
    network: NetworkStatus
    reported_at: datetime


class DeviceHealth(BaseModel):
    device_id: str
    status: DeviceStatus
    healthy: bool
    current_version: str | None
    last_seen_at: datetime | None
    seconds_since_last_heartbeat: float | None
    metrics: HealthMetrics | None
