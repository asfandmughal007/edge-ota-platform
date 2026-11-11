"""Deployment endpoints.

Operator-facing (``X-API-Key``): request and inspect deployments.
Agent-facing (device token): fetch the next deployment and report progress.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Path, Query, Response, status

from device_mgmt.api.deps import (
    AdminRequired,
    DeploymentControllerDep,
    DeviceAuth,
    DeviceIdPath,
    DevicePathAuth,
)
from device_mgmt.schemas import (
    DEVICE_ID_PATTERN,
    DeploymentCreate,
    DeploymentRead,
    DeploymentStatus,
    DeploymentStatusUpdate,
)

router = APIRouter(tags=["deployments"])

DeploymentIdPath = Annotated[str, Path(min_length=36, max_length=36)]
Limit = Annotated[int, Query(ge=1, le=500)]
Offset = Annotated[int, Query(ge=0)]


# --- operator ----------------------------------------------------------------


@router.post(
    "/deployments",
    response_model=DeploymentRead,
    status_code=status.HTTP_201_CREATED,
    dependencies=[AdminRequired],
    responses={
        401: {},
        404: {"description": "Unknown device or version"},
        409: {"description": "Device already on that version or has an active deployment"},
    },
)
def request_deployment(
    payload: DeploymentCreate, controller: DeploymentControllerDep
) -> DeploymentRead:
    return controller.request(payload)


@router.get(
    "/deployments",
    response_model=list[DeploymentRead],
    dependencies=[AdminRequired],
    responses={401: {}},
)
def list_deployments(
    controller: DeploymentControllerDep,
    device_id: Annotated[str | None, Query(max_length=64, pattern=DEVICE_ID_PATTERN)] = None,
    status: Annotated[DeploymentStatus | None, Query()] = None,
    limit: Limit = 100,
    offset: Offset = 0,
) -> list[DeploymentRead]:
    return controller.list(device_id=device_id, status=status, limit=limit, offset=offset)


@router.get(
    "/deployments/{deployment_id}",
    response_model=DeploymentRead,
    dependencies=[AdminRequired],
    responses={401: {}, 404: {}},
)
def get_deployment(
    deployment_id: DeploymentIdPath, controller: DeploymentControllerDep
) -> DeploymentRead:
    return controller.get(deployment_id)


@router.get(
    "/devices/{device_id}/deployments",
    response_model=list[DeploymentRead],
    dependencies=[AdminRequired],
    responses={401: {}, 404: {}},
)
def list_device_deployments(
    device_id: DeviceIdPath,
    controller: DeploymentControllerDep,
    limit: Limit = 100,
    offset: Offset = 0,
) -> list[DeploymentRead]:
    return controller.list(device_id=device_id, limit=limit, offset=offset)


# --- device agent ------------------------------------------------------------


@router.get(
    "/devices/{device_id}/deployments/next",
    response_model=DeploymentRead,
    responses={
        204: {"description": "Nothing to deploy"},
        401: {},
        403: {"description": "Token belongs to a different device"},
    },
)
def next_deployment(
    device_id: DeviceIdPath, controller: DeploymentControllerDep, _identity: DevicePathAuth
) -> DeploymentRead | Response:
    deployment = controller.next_for_device(device_id)
    if deployment is None:
        return Response(status_code=status.HTTP_204_NO_CONTENT)
    return deployment


@router.post(
    "/deployments/{deployment_id}/status",
    response_model=DeploymentRead,
    responses={
        401: {},
        404: {"description": "No such deployment for the authenticated device"},
        409: {"description": "Transition not allowed from the current status"},
    },
)
def report_deployment_status(
    deployment_id: DeploymentIdPath,
    update: DeploymentStatusUpdate,
    controller: DeploymentControllerDep,
    identity: DeviceAuth,
) -> DeploymentRead:
    # Another device's deployment is reported as 404, not 403, so deployment
    # IDs cannot be probed across devices.
    return controller.update_status(deployment_id, update, device_id=identity.device_id)
