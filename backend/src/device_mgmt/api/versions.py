"""Software version catalog endpoints."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, status

from device_mgmt.api.deps import VersionCatalogDep
from device_mgmt.schemas import SEMVER_PATTERN, VersionCreate, VersionRead

router = APIRouter(prefix="/versions", tags=["versions"])

VersionPath = Annotated[str, Path(max_length=64, pattern=SEMVER_PATTERN)]


@router.post(
    "",
    response_model=VersionRead,
    status_code=status.HTTP_201_CREATED,
    responses={409: {"description": "Version already exists"}},
)
def publish_version(payload: VersionCreate, catalog: VersionCatalogDep) -> VersionRead:
    return catalog.create(payload)


@router.get("", response_model=list[VersionRead])
def list_versions(catalog: VersionCatalogDep) -> list[VersionRead]:
    return catalog.list()


@router.get("/{version}", response_model=VersionRead, responses={404: {}})
def get_version(version: VersionPath, catalog: VersionCatalogDep) -> VersionRead:
    return catalog.get(version)
