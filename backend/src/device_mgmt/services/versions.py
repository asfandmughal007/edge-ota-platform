"""Software version catalog."""

from __future__ import annotations

import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from device_mgmt.db.models import SoftwareVersion
from device_mgmt.db.repositories import VersionRepository
from device_mgmt.errors import VersionAlreadyExistsError, VersionNotFoundError
from device_mgmt.schemas import VersionCreate, VersionRead
from device_mgmt.services.registry import Clock, utcnow

logger = logging.getLogger(__name__)


def semver_key(version: str) -> tuple[int, ...]:
    """Sort key so that 1.10.0 orders after 1.9.0 (plain string sort gets this wrong)."""
    return tuple(int(part) for part in version.split("."))


class VersionCatalog:
    def __init__(self, session: Session, clock: Clock = utcnow) -> None:
        self._session = session
        self._versions = VersionRepository(session)
        self._clock = clock

    def create(self, payload: VersionCreate) -> VersionRead:
        if self._versions.get(payload.version) is not None:
            raise VersionAlreadyExistsError(payload.version)
        version = SoftwareVersion(
            version=payload.version,
            image=payload.image,
            notes=payload.notes,
            created_at=self._clock(),
        )
        self._versions.add(version)
        try:
            self._session.commit()
        except IntegrityError as exc:
            self._session.rollback()
            raise VersionAlreadyExistsError(payload.version) from exc
        logger.info(
            "software version published", extra={"version": version.version, "image": version.image}
        )
        return self._to_read(version)

    def list(self) -> list[VersionRead]:
        ordered = sorted(self._versions.list(), key=lambda v: semver_key(v.version))
        return [self._to_read(version) for version in ordered]

    def get(self, version: str) -> VersionRead:
        found = self._versions.get(version)
        if found is None:
            raise VersionNotFoundError(version)
        return self._to_read(found)

    @staticmethod
    def _to_read(version: SoftwareVersion) -> VersionRead:
        return VersionRead(
            version=version.version,
            image=version.image,
            notes=version.notes,
            created_at=version.created_at,
        )
