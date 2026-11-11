"""Wire format of messages the agent sends to the backend."""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

HEARTBEAT_SCHEMA_VERSION = 1

Percent = Field(ge=0, le=100)


class NetworkStatus(BaseModel):
    model_config = ConfigDict(frozen=True)

    interfaces_up: list[str]
    ip_address: str | None
    # None means "not checked" (no backend configured), not "unreachable".
    backend_reachable: bool | None


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
    timestamp: AwareDatetime
