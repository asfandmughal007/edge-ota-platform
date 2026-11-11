"""Software version catalog endpoints (operator only)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, status

from device_mgmt.api.deps import AdminRequired, VersionCatalogDep
from device_mgmt.schemas import SEMVER_PATTERN, VersionCreate, VersionRead

router = APIRouter(prefix="/versions", tags=["versions"], dependencies=[AdminRequired])

VersionPath = Annotated[str, Path(max_length=64, pattern=SEMVER_PATTERN)]


@router.post(
    "",
    response_model=VersionRead,
    status_code=status.HTTP_201_CREATED,
    responses={401: {}, 409: {"description": "Version already exists"}},
)
def publish_version(payload: VersionCreate, catalog: VersionCatalogDep) -> VersionRead:
    return catalog.create(payload)


@router.get("", response_model=list[VersionRead], responses={401: {}})
def list_versions(catalog: VersionCatalogDep) -> list[VersionRead]:
    return catalog.list()


@router.get("/{version}", response_model=VersionRead, responses={401: {}, 404: {}})
def get_version(version: VersionPath, catalog: VersionCatalogDep) -> VersionRead:
    return catalog.get(version)
