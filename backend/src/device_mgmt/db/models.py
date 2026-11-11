"""ORM models."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import JSON, Float, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column

from device_mgmt.db.session import Base, UTCDateTime


class Device(Base):
    __tablename__ = "devices"

    device_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    hostname: Mapped[str | None] = mapped_column(String(255))
    # Version the device last reported running, not the version we want it on.
    current_version: Mapped[str | None] = mapped_column(String(64))
    registered_at: Mapped[datetime] = mapped_column(UTCDateTime())
    # Server receive time of the latest heartbeat. Liveness never trusts the
    # device's own clock, which may be wrong on a board without an RTC.
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


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
