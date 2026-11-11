"""ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Boolean, Float, ForeignKey, Index, String, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from device_mgmt.db.session import Base, UTCDateTime


class Device(Base):
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hostname: Mapped[str | None] = mapped_column(String(255))
    # SHA-256 of the device's bearer token. The token itself is never stored.
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    # Version the device last reported running, not the version we want it on.
    current_version: Mapped[str | None] = mapped_column(String(64))
    registered_at: Mapped[datetime] = mapped_column(UTCDateTime())
    # Server receive time of the latest heartbeat. Liveness never trusts the
    # device's own clock, which may be wrong on a board without an RTC.
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    # Status as last recorded by the monitor or a heartbeat, used to detect transitions.
    last_status: Mapped[str] = mapped_column(String(16), default="never_seen")
    # The managed application as last reported by the agent.
    app_state: Mapped[str | None] = mapped_column(String(16))
    app_healthy: Mapped[bool | None] = mapped_column(Boolean)


class HeartbeatRecord(Base):
    __tablename__ = "device_heartbeats"
    __table_args__ = (Index("ix_device_heartbeats_device_received", "device_id", "received_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.device_id", ondelete="CASCADE")
    )
    received_at: Mapped[datetime] = mapped_column(UTCDateTime())
    reported_at: Mapped[datetime] = mapped_column(UTCDateTime())
    software_version: Mapped[str] = mapped_column(String(64))
    cpu_percent: Mapped[float] = mapped_column(Float)
    memory_percent: Mapped[float] = mapped_column(Float)
    disk_percent: Mapped[float] = mapped_column(Float)
    uptime_seconds: Mapped[float] = mapped_column(Float)
    network: Mapped[dict[str, Any]] = mapped_column(JSON)
    app: Mapped[dict[str, Any] | None] = mapped_column(JSON)


class DeviceEvent(Base):
    """Notable changes in a device's life: went offline, came back, app unhealthy."""

    __tablename__ = "device_events"
    __table_args__ = (Index("ix_device_events_device_created", "device_id", "created_at"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.device_id", ondelete="CASCADE")
    )
    event_type: Mapped[str] = mapped_column(String(32))
    detail: Mapped[str | None] = mapped_column(String(500))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


class SoftwareVersion(Base):
    """A release that can be deployed: a version number and the image that ships it."""

    __tablename__ = "software_versions"

    version: Mapped[str] = mapped_column(String(64), primary_key=True)
    image: Mapped[str] = mapped_column(String(255))
    notes: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())


_ACTIVE_DEPLOYMENT = text("status IN ('pending', 'in_progress')")


class Deployment(Base):
    __tablename__ = "deployments"
    __table_args__ = (
        # The database, not just application code, guarantees a device never
        # has two updates in flight, even with concurrent requests.
        Index(
            "uq_deployments_one_active_per_device",
            "device_id",
            unique=True,
            sqlite_where=_ACTIVE_DEPLOYMENT,
            postgresql_where=_ACTIVE_DEPLOYMENT,
        ),
        Index("ix_deployments_device_created", "device_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    device_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("devices.device_id", ondelete="CASCADE")
    )
    target_version: Mapped[str] = mapped_column(String(64), ForeignKey("software_versions.version"))
    # Copied from the version at request time so the record of what was
    # deployed stays accurate even if the catalog entry changes later.
    image: Mapped[str] = mapped_column(String(255))
    previous_version: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(16))
    message: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())

    events: Mapped[list[DeploymentEvent]] = relationship(
        order_by="DeploymentEvent.id", lazy="selectin", cascade="all, delete-orphan"
    )


class DeploymentEvent(Base):
    """Append-only log of status transitions for one deployment."""

    __tablename__ = "deployment_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    deployment_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("deployments.id", ondelete="CASCADE"), index=True
    )
    status: Mapped[str] = mapped_column(String(16))
    message: Mapped[str | None] = mapped_column(String(2000))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime())
