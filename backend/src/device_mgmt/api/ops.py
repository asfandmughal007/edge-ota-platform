"""Operational endpoints: readiness, fleet summary and Prometheus metrics."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request, Response, status
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from device_mgmt.api.deps import AdminRequired, SessionDep
from device_mgmt.schemas import FleetSummary
from device_mgmt.services.fleet import FleetReporter

router = APIRouter(tags=["ops"])


def get_fleet_reporter(request: Request, session: SessionDep) -> FleetReporter:
    return FleetReporter(session, request.app.state.settings, clock=request.app.state.clock)


FleetDep = Annotated[FleetReporter, Depends(get_fleet_reporter)]


@router.get("/readyz", responses={503: {"description": "Database unavailable"}})
def readiness(session: SessionDep) -> Response:
    """Ready to serve traffic: unlike /healthz this also checks the database."""
    try:
        session.execute(text("SELECT 1"))
    except SQLAlchemyError:
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, content={"status": "database_down"}
        )
    return JSONResponse(content={"status": "ready"})


@router.get(
    "/fleet/summary",
    response_model=FleetSummary,
    dependencies=[AdminRequired],
    responses={401: {}},
)
def fleet_summary(reporter: FleetDep) -> FleetSummary:
    return reporter.summary()


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def render_metrics(summary: FleetSummary, http_requests: dict[tuple[str, str, int], int]) -> str:
    """Prometheus text exposition format."""
    lines: list[str] = []

    def family(name: str, help_text: str, kind: str) -> None:
        lines.append(f"# HELP {name} {help_text}")
        lines.append(f"# TYPE {name} {kind}")

    family("device_mgmt_devices", "Registered devices by liveness status.", "gauge")
    for state, count in sorted(summary.devices_by_status.items()):
        lines.append(f'device_mgmt_devices{{status="{_escape(state)}"}} {count}')

    family("device_mgmt_devices_by_version", "Registered devices by reported version.", "gauge")
    for version, count in summary.devices_by_version.items():
        lines.append(f'device_mgmt_devices_by_version{{version="{_escape(version)}"}} {count}')

    family(
        "device_mgmt_apps_unhealthy",
        "Devices whose managed application last reported unhealthy.",
        "gauge",
    )
    lines.append(f"device_mgmt_apps_unhealthy {summary.apps_unhealthy}")

    family("device_mgmt_deployments", "Deployments by status.", "gauge")
    for state, count in sorted(summary.deployments_by_status.items()):
        lines.append(f'device_mgmt_deployments{{status="{_escape(state)}"}} {count}')

    family(
        "device_mgmt_unsuccessful_deployments_24h",
        "Deployments that failed or were rolled back in the last 24 hours.",
        "gauge",
    )
    lines.append(f"device_mgmt_unsuccessful_deployments_24h {summary.unsuccessful_deployments_24h}")

    family("device_mgmt_http_requests_total", "HTTP requests handled.", "counter")
    for (method, route, code), count in sorted(http_requests.items()):
        labels = f'method="{method}",route="{_escape(route)}",status="{code}"'
        lines.append(f"device_mgmt_http_requests_total{{{labels}}} {count}")
    return "\n".join(lines) + "\n"


@router.get(
    "/metrics",
    response_class=PlainTextResponse,
    dependencies=[AdminRequired],
    responses={401: {}},
)
def metrics(request: Request, reporter: FleetDep) -> PlainTextResponse:
    body = render_metrics(reporter.summary(), dict(request.app.state.http_requests))
    return PlainTextResponse(body, media_type="text/plain; version=0.0.4")
