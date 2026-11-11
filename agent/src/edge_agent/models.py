"""Wire format of messages the agent sends to the backend."""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

HEARTBEAT_SCHEMA_VERSION = 1

Percent = Field(ge=0, le=100)


class NetworkStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    interfaces_up: list[str]
    ip_address: str | None
    # None means "not checked" (no backend configured), not "unreachable".
    backend_reachable: bool | None


class AppStatus(BaseModel):
    """The managed application container, as observed on this device."""

    model_config = ConfigDict(frozen=True)

    state: Literal["running", "stopped", "absent", "updating", "unknown"]
    # None when health was not checked: not running, mid-update, or Docker unreachable.
    healthy: bool | None = None
    version: str | None = None


class Heartbeat(BaseModel):
    model_config = ConfigDict(frozen=True)

    schema_version: int = HEARTBEAT_SCHEMA_VERSION
    device_id: str
    hostname: str
    software_version: str
    cpu_percent: float = Percent
    memory_percent: float = Percent
    disk_percent: float = Percent
    uptime_seconds: float = Field(ge=0)
    network: NetworkStatus
    app: AppStatus | None = None
    timestamp: AwareDatetime


class DeploymentCommand(BaseModel):
    """A deployment as returned by ``GET /devices/{id}/deployments/next``."""

    # The backend may add fields; an older agent must keep working.
    model_config = ConfigDict(frozen=True, extra="ignore")

    id: str
    target_version: str
    image: str
    previous_version: str | None = None
    status: Literal["pending", "in_progress"]


ReportStatus = Literal["in_progress", "succeeded", "failed", "rolled_back"]
