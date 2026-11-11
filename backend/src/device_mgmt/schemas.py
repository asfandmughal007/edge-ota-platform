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


class DeviceRegistered(DeviceRead):
    """Registration response. ``device_token`` is returned this once and cannot be retrieved."""

    device_token: str


class DeviceTokenRead(BaseModel):
    device_id: str
    device_token: str


class NetworkStatus(BaseModel):
    interfaces_up: list[str]
    ip_address: str | None
    backend_reachable: bool | None


class AppStatus(BaseModel):
    """The managed application on the device, as observed by its agent."""

    # "updating": an OTA update is in progress, so health is deliberately not judged.
    state: Literal["running", "stopped", "absent", "updating", "unknown"]
    # None when it could not be checked (not running, or Docker unreachable).
    healthy: bool | None = None
    version: Version | None = None


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
    # Optional so agents without a managed application (or older agents) still work.
    app: AppStatus | None = None
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
    application: AppStatus | None


class DeviceEventRead(BaseModel):
    event_type: str
    detail: str | None
    created_at: datetime


class FleetSummary(BaseModel):
    total_devices: int
    devices_by_status: dict[str, int]
    devices_by_version: dict[str, int]
    apps_unhealthy: int
    deployments_by_status: dict[str, int]
    # Deployments that failed or were rolled back in the last 24 hours.
    unsuccessful_deployments_24h: int


# --- Software versions -------------------------------------------------------

SEMVER_PATTERN = r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$"
SemVer = Annotated[str, StringConstraints(max_length=64, pattern=SEMVER_PATTERN)]
# Loose check for a Docker image reference: registry/repo:tag or repo@sha256:...
ImageRef = Annotated[
    str, StringConstraints(min_length=1, max_length=255, pattern=r"^[a-z0-9][\w.\-/:@]*$")
]
Message = Annotated[str, StringConstraints(max_length=2000)]


class VersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: SemVer
    image: ImageRef
    notes: Message | None = None


class VersionRead(BaseModel):
    version: str
    image: str
    notes: str | None
    created_at: datetime


# --- Deployments -------------------------------------------------------------


class DeploymentStatus(StrEnum):
    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    SUCCEEDED = "succeeded"
    # The update did not complete and no rollback restored the previous version:
    # either it failed before anything was switched (bad image, pull error) and
    # the old version never stopped, or the rollback itself failed. See `message`.
    FAILED = "failed"
    # The update failed and the device restored the previous version.
    ROLLED_BACK = "rolled_back"


class DeploymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    device_id: DeviceId
    target_version: SemVer


class DeploymentStatusUpdate(BaseModel):
    """Progress report from the device agent."""

    model_config = ConfigDict(extra="forbid")

    status: Literal[
        DeploymentStatus.IN_PROGRESS,
        DeploymentStatus.SUCCEEDED,
        DeploymentStatus.FAILED,
        DeploymentStatus.ROLLED_BACK,
    ]
    message: Message | None = None


class DeploymentEventRead(BaseModel):
    status: DeploymentStatus
    message: str | None
    created_at: datetime


class DeploymentRead(BaseModel):
    id: str
    device_id: str
    target_version: str
    image: str
    previous_version: str | None
    status: DeploymentStatus
    message: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None
    events: list[DeploymentEventRead]
