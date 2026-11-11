"""Background heartbeat monitor.

Device status is computed on read, so the API is always right without this
monitor. What the monitor adds is *detection*: noticing the moment a device
goes quiet, so it is logged (alertable) and recorded in the device's event
history instead of only being visible to whoever happens to query it.
It also prunes old heartbeat rows so the table cannot grow without bound.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from device_mgmt.config import Settings
from device_mgmt.db.models import Device, DeviceEvent, HeartbeatRecord
from device_mgmt.schemas import DeviceStatus
from device_mgmt.services.liveness import compute_status, silence_seconds
from device_mgmt.services.registry import Clock

logger = logging.getLogger(__name__)

_PRUNE_EVERY = timedelta(hours=1)


@dataclass(frozen=True, slots=True)
class StatusChange:
    device_id: str
    previous: str
    current: DeviceStatus


class HeartbeatMonitor:
    def __init__(
        self, session_factory: sessionmaker[Session], settings: Settings, clock: Clock
    ) -> None:
        self._session_factory = session_factory
        self._settings = settings
        self._clock = clock
        self._last_prune: datetime | None = None

    def scan(self) -> list[StatusChange]:
        """Compare every device's current status with the last one recorded."""
        now = self._clock()
        changes: list[StatusChange] = []
        with self._session_factory() as session:
            for device in session.scalars(select(Device)):
                current = compute_status(device.last_seen_at, now, self._settings)
                if current.value == device.last_status:
                    continue
                changes.append(StatusChange(device.device_id, device.last_status, current))
                session.add(
                    DeviceEvent(
                        device_id=device.device_id,
                        event_type=current.value,
                        detail=f"{device.last_status} -> {current.value}",
                        created_at=now,
                    )
                )
                device.last_status = current.value
                log = logger.info if current is DeviceStatus.ONLINE else logger.warning
                log(
                    "device status changed",
                    extra={
                        "device_id": device.device_id,
                        "from_status": changes[-1].previous,
                        "to_status": current.value,
                        "silent_seconds": silence_seconds(device.last_seen_at, now),
                    },
                )
            session.commit()
        return changes

    def prune(self) -> int:
        """Delete heartbeat history older than the retention window."""
        cutoff = self._clock() - timedelta(days=self._settings.heartbeat_retention_days)
        with self._session_factory() as session:
            result = session.execute(
                delete(HeartbeatRecord).where(HeartbeatRecord.received_at < cutoff)
            )
            session.commit()
        deleted = int(result.rowcount or 0)  # type: ignore[attr-defined]
        if deleted:
            logger.info("pruned old heartbeats", extra={"deleted": deleted})
        return deleted

    def tick(self) -> None:
        self.scan()
        now = self._clock()
        if self._last_prune is None or now - self._last_prune >= _PRUNE_EVERY:
            self.prune()
            self._last_prune = now

    async def run(self, stop: asyncio.Event) -> None:
        interval = self._settings.monitor_interval_seconds
        logger.info("heartbeat monitor started", extra={"interval_seconds": interval})
        while not stop.is_set():
            try:
                await asyncio.to_thread(self.tick)
            except Exception:
                # Monitoring must never take the API down with it.
                logger.exception("heartbeat monitor tick failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=interval)
        logger.info("heartbeat monitor stopped")
