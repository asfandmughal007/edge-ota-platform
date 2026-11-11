"""Deployment controller: records desired state and enforces the deployment
state machine. It never contacts a device; agents pull their next deployment
and report progress back.

    pending ──> in_progress ──> succeeded
       │             ├────────> rolled_back
       └─────────────┴────────> failed
"""

from __future__ import annotations

import logging
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from device_mgmt.db.models import Deployment, DeploymentEvent
from device_mgmt.db.repositories import (
    DeploymentRepository,
    DeviceRepository,
    VersionRepository,
)
from device_mgmt.errors import (
    DeploymentConflictError,
    DeploymentNotFoundError,
    DeviceNotFoundError,
    VersionNotFoundError,
)
from device_mgmt.schemas import (
    DeploymentCreate,
    DeploymentEventRead,
    DeploymentRead,
    DeploymentStatus,
    DeploymentStatusUpdate,
)
from device_mgmt.services.registry import Clock, utcnow

logger = logging.getLogger(__name__)

ALLOWED_TRANSITIONS: dict[DeploymentStatus, frozenset[DeploymentStatus]] = {
    DeploymentStatus.PENDING: frozenset({DeploymentStatus.IN_PROGRESS, DeploymentStatus.FAILED}),
    DeploymentStatus.IN_PROGRESS: frozenset(
        {DeploymentStatus.SUCCEEDED, DeploymentStatus.FAILED, DeploymentStatus.ROLLED_BACK}
    ),
    DeploymentStatus.SUCCEEDED: frozenset(),
    DeploymentStatus.FAILED: frozenset(),
    DeploymentStatus.ROLLED_BACK: frozenset(),
}
TERMINAL_STATUSES = frozenset(s for s, nxt in ALLOWED_TRANSITIONS.items() if not nxt)


class DeploymentController:
    def __init__(self, session: Session, clock: Clock = utcnow) -> None:
        self._session = session
        self._deployments = DeploymentRepository(session)
        self._devices = DeviceRepository(session)
        self._versions = VersionRepository(session)
        self._clock = clock

    def request(self, payload: DeploymentCreate) -> DeploymentRead:
        device = self._devices.get(payload.device_id)
        if device is None:
            raise DeviceNotFoundError(payload.device_id)
        version = self._versions.get(payload.target_version)
        if version is None:
            raise VersionNotFoundError(payload.target_version)
        if device.current_version == version.version:
            raise DeploymentConflictError(
                f"device '{device.device_id}' is already running version {version.version}"
            )
        active = self._deployments.active_for_device(device.device_id)
        if active is not None:
            raise DeploymentConflictError(
                f"device '{device.device_id}' already has deployment {active.id} {active.status}"
            )

        now = self._clock()
        deployment = Deployment(
            id=str(uuid.uuid4()),
            device_id=device.device_id,
            target_version=version.version,
            image=version.image,
            previous_version=device.current_version,
            status=DeploymentStatus.PENDING.value,
            created_at=now,
        )
        deployment.events.append(
            DeploymentEvent(status=DeploymentStatus.PENDING.value, created_at=now)
        )
        self._deployments.add(deployment)
        try:
            self._session.commit()
        except IntegrityError as exc:
            # A concurrent request won the partial unique index on active deployments.
            self._session.rollback()
            raise DeploymentConflictError(
                f"device '{payload.device_id}' already has an active deployment"
            ) from exc
        logger.info(
            "deployment requested",
            extra={
                "deployment_id": deployment.id,
                "device_id": deployment.device_id,
                "previous_version": deployment.previous_version,
                "target_version": deployment.target_version,
            },
        )
        return self._to_read(deployment)

    def get(self, deployment_id: str) -> DeploymentRead:
        return self._to_read(self._require(deployment_id))

    def list(
        self,
        device_id: str | None = None,
        status: DeploymentStatus | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> list[DeploymentRead]:
        if device_id is not None and self._devices.get(device_id) is None:
            raise DeviceNotFoundError(device_id)
        rows = self._deployments.list(
            device_id, status.value if status is not None else None, limit, offset
        )
        return [self._to_read(row) for row in rows]

    def next_for_device(self, device_id: str) -> DeploymentRead | None:
        """The deployment a device should be working on, if any.

        An in-progress deployment is returned too, so an agent that restarted
        mid-update can find out what it was doing and report an outcome.
        """
        if self._devices.get(device_id) is None:
            raise DeviceNotFoundError(device_id)
        active = self._deployments.active_for_device(device_id)
        return self._to_read(active) if active is not None else None

    def update_status(
        self, deployment_id: str, update: DeploymentStatusUpdate, device_id: str | None = None
    ) -> DeploymentRead:
        """Apply a status report.

        When ``device_id`` is given (always, for agent calls) the deployment must
        belong to that device; otherwise it is reported as not found.
        """
        deployment = self._require(deployment_id)
        if device_id is not None and deployment.device_id != device_id:
            raise DeploymentNotFoundError(deployment_id)
        current = DeploymentStatus(deployment.status)
        new = DeploymentStatus(update.status)

        if new is current:
            # Agents retry reports over flaky links; a repeat must not fail or
            # write a duplicate history entry.
            return self._to_read(deployment)
        if new not in ALLOWED_TRANSITIONS[current]:
            raise DeploymentConflictError(
                f"deployment {deployment.id} cannot go from {current.value} to {new.value}"
            )

        now = self._clock()
        deployment.status = new.value
        deployment.message = update.message
        if new is DeploymentStatus.IN_PROGRESS:
            deployment.started_at = now
        if new in TERMINAL_STATUSES:
            deployment.finished_at = now
        if new is DeploymentStatus.SUCCEEDED:
            device = self._devices.get(deployment.device_id)
            if device is not None:
                device.current_version = deployment.target_version
        deployment.events.append(
            DeploymentEvent(status=new.value, message=update.message, created_at=now)
        )
        self._session.commit()

        log = logger.info if new is not DeploymentStatus.FAILED else logger.error
        if new is DeploymentStatus.ROLLED_BACK:
            log = logger.warning
        log(
            "deployment status changed",
            extra={
                "deployment_id": deployment.id,
                "device_id": deployment.device_id,
                "from_status": current.value,
                "to_status": new.value,
                "target_version": deployment.target_version,
                "detail": update.message,
            },
        )
        return self._to_read(deployment)

    def _require(self, deployment_id: str) -> Deployment:
        deployment = self._deployments.get(deployment_id)
        if deployment is None:
            raise DeploymentNotFoundError(deployment_id)
        return deployment

    @staticmethod
    def _to_read(deployment: Deployment) -> DeploymentRead:
        return DeploymentRead(
            id=deployment.id,
            device_id=deployment.device_id,
            target_version=deployment.target_version,
            image=deployment.image,
            previous_version=deployment.previous_version,
            status=DeploymentStatus(deployment.status),
            message=deployment.message,
            created_at=deployment.created_at,
            started_at=deployment.started_at,
            finished_at=deployment.finished_at,
            events=[
                DeploymentEventRead(
                    status=DeploymentStatus(event.status),
                    message=event.message,
                    created_at=event.created_at,
                )
                for event in deployment.events
            ],
        )
