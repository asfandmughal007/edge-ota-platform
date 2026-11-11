"""Fleet-wide aggregates for dashboards and Prometheus."""

from __future__ import annotations

from collections import Counter
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from device_mgmt.config import Settings
from device_mgmt.db.models import Deployment, Device
from device_mgmt.schemas import DeploymentStatus, DeviceStatus, FleetSummary
from device_mgmt.services.liveness import compute_status
from device_mgmt.services.registry import Clock, utcnow

UNSUCCESSFUL = (DeploymentStatus.FAILED.value, DeploymentStatus.ROLLED_BACK.value)


class FleetReporter:
    def __init__(self, session: Session, settings: Settings, clock: Clock = utcnow) -> None:
        self._session = session
        self._settings = settings
        self._clock = clock

    def summary(self) -> FleetSummary:
        now = self._clock()
        devices = self._session.scalars(select(Device)).all()

        by_status = Counter({status.value: 0 for status in DeviceStatus})
        by_version: Counter[str] = Counter()
        for device in devices:
            by_status[compute_status(device.last_seen_at, now, self._settings).value] += 1
            by_version[device.current_version or "unknown"] += 1

        deployments = Counter({status.value: 0 for status in DeploymentStatus})
        rows = self._session.execute(
            select(Deployment.status, func.count()).group_by(Deployment.status)
        )
        deployments.update({status: count for status, count in rows})

        since = now - timedelta(hours=24)
        unsuccessful_24h = self._session.scalar(
            select(func.count())
            .select_from(Deployment)
            .where(Deployment.status.in_(UNSUCCESSFUL), Deployment.finished_at >= since)
        )
        return FleetSummary(
            total_devices=len(devices),
            devices_by_status=dict(by_status),
            devices_by_version=dict(sorted(by_version.items())),
            apps_unhealthy=sum(1 for device in devices if device.app_healthy is False),
            deployments_by_status=dict(deployments),
            unsuccessful_deployments_24h=int(unsuccessful_24h or 0),
        )
