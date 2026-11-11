"""Persistence queries. No business rules live here."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from device_mgmt.db.models import Device, HeartbeatRecord


class DeviceRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, device_id: str) -> Device | None:
        return self._session.get(Device, device_id)

    def list(self, limit: int, offset: int) -> Sequence[Device]:
        statement = select(Device).order_by(Device.device_id).limit(limit).offset(offset)
        return self._session.scalars(statement).all()

    def add(self, device: Device) -> None:
        self._session.add(device)

    def add_heartbeat(self, record: HeartbeatRecord) -> None:
        self._session.add(record)

    def latest_heartbeat(self, device_id: str) -> HeartbeatRecord | None:
        statement = (
            select(HeartbeatRecord)
            .where(HeartbeatRecord.device_id == device_id)
            .order_by(HeartbeatRecord.received_at.desc(), HeartbeatRecord.id.desc())
            .limit(1)
        )
        return self._session.scalars(statement).first()
