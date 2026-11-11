"""Persistence queries. No business rules live here."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.orm import Session

from device_mgmt.db.models import Deployment, Device, HeartbeatRecord, SoftwareVersion

ACTIVE_STATUSES = ("pending", "in_progress")


class VersionRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, version: str) -> SoftwareVersion | None:
        return self._session.get(SoftwareVersion, version)

    def list(self) -> Sequence[SoftwareVersion]:
        return self._session.scalars(select(SoftwareVersion)).all()

    def add(self, version: SoftwareVersion) -> None:
        self._session.add(version)


class DeploymentRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def get(self, deployment_id: str) -> Deployment | None:
        return self._session.get(Deployment, deployment_id)

    def add(self, deployment: Deployment) -> None:
        self._session.add(deployment)

    def active_for_device(self, device_id: str) -> Deployment | None:
        statement = select(Deployment).where(
            Deployment.device_id == device_id, Deployment.status.in_(ACTIVE_STATUSES)
        )
        return self._session.scalars(statement).first()

    def list(
        self, device_id: str | None, status: str | None, limit: int, offset: int
    ) -> Sequence[Deployment]:
        statement = select(Deployment)
        if device_id is not None:
            statement = statement.where(Deployment.device_id == device_id)
        if status is not None:
            statement = statement.where(Deployment.status == status)
        statement = (
            statement.order_by(Deployment.created_at.desc(), Deployment.id)
            .limit(limit)
            .offset(offset)
        )
        return self._session.scalars(statement).all()


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
