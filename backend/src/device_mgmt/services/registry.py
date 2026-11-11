"""Device registry: registration, heartbeat ingest and health."""

from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from device_mgmt.config import Settings
from device_mgmt.db.models import Device, HeartbeatRecord
from device_mgmt.db.repositories import DeviceRepository
from device_mgmt.errors import (
    DeviceAlreadyExistsError,
    DeviceIdMismatchError,
    DeviceNotFoundError,
)
from device_mgmt.schemas import (
    DeviceCreate,
    DeviceHealth,
    DeviceRead,
    DeviceRegistered,
    DeviceStatus,
    DeviceTokenRead,
    HealthMetrics,
    HeartbeatAck,
    HeartbeatIn,
    NetworkStatus,
)
from device_mgmt.security import generate_device_token, hash_token

logger = logging.getLogger(__name__)

Clock = Callable[[], datetime]


def utcnow() -> datetime:
    return datetime.now(UTC)


class DeviceRegistry:
    def __init__(self, session: Session, settings: Settings, clock: Clock = utcnow) -> None:
        self._session = session
        self._devices = DeviceRepository(session)
        self._settings = settings
        self._clock = clock

    def register(self, payload: DeviceCreate) -> DeviceRegistered:
        if self._devices.get(payload.device_id) is not None:
            raise DeviceAlreadyExistsError(payload.device_id)
        token = generate_device_token()
        device = Device(
            device_id=payload.device_id,
            token_hash=hash_token(token),
            hostname=payload.hostname,
            current_version=payload.current_version,
            registered_at=self._clock(),
            last_seen_at=None,
        )
        self._devices.add(device)
        try:
            self._session.commit()
        except IntegrityError as exc:
            # Lost a race with a concurrent registration of the same ID.
            self._session.rollback()
            raise DeviceAlreadyExistsError(payload.device_id) from exc
        logger.info("device registered", extra={"device_id": device.device_id})
        return DeviceRegistered(**self._to_read(device).model_dump(), device_token=token)

    def rotate_token(self, device_id: str) -> DeviceTokenRead:
        device = self._require(device_id)
        token = generate_device_token()
        device.token_hash = hash_token(token)
        self._session.commit()
        logger.info("device token rotated", extra={"device_id": device_id})
        return DeviceTokenRead(device_id=device_id, device_token=token)

    def list(self, limit: int, offset: int) -> list[DeviceRead]:
        return [self._to_read(device) for device in self._devices.list(limit, offset)]

    def get(self, device_id: str) -> DeviceRead:
        return self._to_read(self._require(device_id))

    def record_heartbeat(self, device_id: str, heartbeat: HeartbeatIn) -> HeartbeatAck:
        if heartbeat.device_id != device_id:
            raise DeviceIdMismatchError(device_id, heartbeat.device_id)
        device = self._require(device_id)
        received_at = self._clock()

        if device.current_version != heartbeat.software_version:
            logger.info(
                "device version changed",
                extra={
                    "device_id": device_id,
                    "previous_version": device.current_version,
                    "current_version": heartbeat.software_version,
                },
            )
        device.hostname = heartbeat.hostname
        device.current_version = heartbeat.software_version
        device.last_seen_at = received_at
        self._devices.add_heartbeat(
            HeartbeatRecord(
                device_id=device_id,
                received_at=received_at,
                reported_at=heartbeat.timestamp,
                software_version=heartbeat.software_version,
                cpu_percent=heartbeat.cpu_percent,
                memory_percent=heartbeat.memory_percent,
                disk_percent=heartbeat.disk_percent,
                uptime_seconds=heartbeat.uptime_seconds,
                network=heartbeat.network.model_dump(),
            )
        )
        self._session.commit()
        return HeartbeatAck(device_id=device_id, received_at=received_at)

    def health(self, device_id: str) -> DeviceHealth:
        device = self._require(device_id)
        status = self._status(device)
        latest = self._devices.latest_heartbeat(device_id)
        metrics = (
            HealthMetrics(
                cpu_percent=latest.cpu_percent,
                memory_percent=latest.memory_percent,
                disk_percent=latest.disk_percent,
                uptime_seconds=latest.uptime_seconds,
                network=NetworkStatus.model_validate(latest.network),
                reported_at=latest.reported_at,
            )
            if latest is not None
            else None
        )
        return DeviceHealth(
            device_id=device.device_id,
            status=status,
            healthy=status is DeviceStatus.ONLINE,
            current_version=device.current_version,
            last_seen_at=device.last_seen_at,
            seconds_since_last_heartbeat=self._silence_seconds(device),
            metrics=metrics,
        )

    def _require(self, device_id: str) -> Device:
        device = self._devices.get(device_id)
        if device is None:
            raise DeviceNotFoundError(device_id)
        return device

    def _silence_seconds(self, device: Device) -> float | None:
        if device.last_seen_at is None:
            return None
        return max(0.0, (self._clock() - device.last_seen_at).total_seconds())

    def _status(self, device: Device) -> DeviceStatus:
        silence = self._silence_seconds(device)
        if silence is None:
            return DeviceStatus.NEVER_SEEN
        if silence < self._settings.heartbeat_stale_after_seconds:
            return DeviceStatus.ONLINE
        if silence < self._settings.heartbeat_offline_after_seconds:
            return DeviceStatus.STALE
        return DeviceStatus.OFFLINE

    def _to_read(self, device: Device) -> DeviceRead:
        return DeviceRead(
            device_id=device.device_id,
            hostname=device.hostname,
            current_version=device.current_version,
            status=self._status(device),
            registered_at=device.registered_at,
            last_seen_at=device.last_seen_at,
        )
