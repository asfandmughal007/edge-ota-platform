from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from conftest import FakeClock
from fastapi.testclient import TestClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from device_mgmt.db.models import Deployment, Device, SoftwareVersion
from device_mgmt.db.session import Base, create_db_engine

DEVICE = "device-001"


@pytest.fixture
def fleet(client: TestClient) -> TestClient:
    """device-001 on 1.2.0, with versions 1.1.0, 1.2.0 and 1.3.0 published."""
    created = client.post("/devices", json={"device_id": DEVICE, "current_version": "1.2.0"})
    assert created.status_code == 201
    for version in ("1.1.0", "1.2.0", "1.3.0"):
        published = client.post(
            "/versions", json={"version": version, "image": f"my-edge-app:{version}"}
        )
        assert published.status_code == 201
    return client


def request_deployment(client: TestClient, target: str = "1.3.0") -> dict[str, Any]:
    response = client.post("/deployments", json={"device_id": DEVICE, "target_version": target})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    return body


def report(client: TestClient, deployment_id: str, status: str, message: str | None = None) -> Any:
    return client.post(
        f"/deployments/{deployment_id}/status", json={"status": status, "message": message}
    )


def current_version(client: TestClient) -> str:
    version: str = client.get(f"/devices/{DEVICE}").json()["current_version"]
    return version


def test_request_creates_pending_deployment(fleet: TestClient) -> None:
    deployment = request_deployment(fleet)

    assert deployment["device_id"] == DEVICE
    assert deployment["target_version"] == "1.3.0"
    assert deployment["image"] == "my-edge-app:1.3.0"
    assert deployment["previous_version"] == "1.2.0"
    assert deployment["status"] == "pending"
    assert deployment["started_at"] is None and deployment["finished_at"] is None
    assert [e["status"] for e in deployment["events"]] == ["pending"]
    assert fleet.get(f"/deployments/{deployment['id']}").json() == deployment
    # Requesting an update does not change what the device is running.
    assert current_version(fleet) == "1.2.0"


@pytest.mark.parametrize(
    ("payload", "missing"),
    [
        ({"device_id": "ghost", "target_version": "1.3.0"}, "device"),
        ({"device_id": DEVICE, "target_version": "9.9.9"}, "version"),
    ],
)
def test_request_for_unknown_device_or_version_is_404(
    fleet: TestClient, payload: dict[str, str], missing: str
) -> None:
    response = fleet.post("/deployments", json=payload)
    assert response.status_code == 404
    assert missing in response.json()["detail"]


def test_request_for_version_already_running_conflicts(fleet: TestClient) -> None:
    response = fleet.post("/deployments", json={"device_id": DEVICE, "target_version": "1.2.0"})
    assert response.status_code == 409
    assert "already running" in response.json()["detail"]


def test_only_one_active_deployment_per_device(fleet: TestClient) -> None:
    first = request_deployment(fleet, "1.3.0")

    response = fleet.post("/deployments", json={"device_id": DEVICE, "target_version": "1.1.0"})

    assert response.status_code == 409
    assert first["id"] in response.json()["detail"]


def test_agent_polls_for_next_deployment(fleet: TestClient) -> None:
    assert fleet.get(f"/devices/{DEVICE}/deployments/next").status_code == 204

    deployment = request_deployment(fleet)
    polled = fleet.get(f"/devices/{DEVICE}/deployments/next")
    assert polled.status_code == 200
    assert polled.json()["id"] == deployment["id"]
    assert polled.json()["image"] == "my-edge-app:1.3.0"

    # Still returned while in progress, so a restarted agent can resume.
    report(fleet, deployment["id"], "in_progress")
    assert fleet.get(f"/devices/{DEVICE}/deployments/next").json()["status"] == "in_progress"

    report(fleet, deployment["id"], "succeeded")
    assert fleet.get(f"/devices/{DEVICE}/deployments/next").status_code == 204
    assert fleet.get("/devices/ghost/deployments/next").status_code == 404


def test_successful_deployment_updates_device_version(fleet: TestClient, clock: FakeClock) -> None:
    deployment = request_deployment(fleet)
    clock.advance(5)
    assert report(fleet, deployment["id"], "in_progress").status_code == 200
    clock.advance(20)
    done = report(fleet, deployment["id"], "succeeded", "health check passed")

    body = done.json()
    assert body["status"] == "succeeded"
    assert body["started_at"] == "2026-01-01T12:00:05Z"
    assert body["finished_at"] == "2026-01-01T12:00:25Z"
    assert [(e["status"], e["message"]) for e in body["events"]] == [
        ("pending", None),
        ("in_progress", None),
        ("succeeded", "health check passed"),
    ]
    assert current_version(fleet) == "1.3.0"


def test_rolled_back_deployment_keeps_previous_version(fleet: TestClient) -> None:
    deployment = request_deployment(fleet)
    report(fleet, deployment["id"], "in_progress")
    response = report(fleet, deployment["id"], "rolled_back", "health check failed: HTTP 500")

    assert response.json()["status"] == "rolled_back"
    assert response.json()["message"] == "health check failed: HTTP 500"
    assert response.json()["finished_at"] is not None
    assert current_version(fleet) == "1.2.0"
    # The device is free for another attempt.
    assert request_deployment(fleet, "1.3.0")["status"] == "pending"


@pytest.mark.parametrize(
    ("path", "then"),
    [
        ([], "succeeded"),  # pending -> succeeded skips in_progress
        ([], "rolled_back"),
        (["in_progress", "succeeded"], "failed"),  # terminal states are final
        (["in_progress", "succeeded"], "in_progress"),
        (["in_progress", "rolled_back"], "succeeded"),
        (["failed"], "in_progress"),
    ],
)
def test_invalid_transitions_are_rejected(fleet: TestClient, path: list[str], then: str) -> None:
    deployment = request_deployment(fleet)
    for status in path:
        assert report(fleet, deployment["id"], status).status_code == 200
    before = fleet.get(f"/deployments/{deployment['id']}").json()

    response = report(fleet, deployment["id"], then)

    assert response.status_code == 409
    assert "cannot go from" in response.json()["detail"]
    assert fleet.get(f"/deployments/{deployment['id']}").json() == before


def test_repeated_status_report_is_idempotent(fleet: TestClient) -> None:
    deployment = request_deployment(fleet)
    report(fleet, deployment["id"], "in_progress")
    first = report(fleet, deployment["id"], "succeeded", "ok").json()

    retry = report(fleet, deployment["id"], "succeeded", "ok")

    assert retry.status_code == 200
    assert retry.json() == first
    assert len(retry.json()["events"]) == 3


def test_status_report_validation(fleet: TestClient) -> None:
    deployment = request_deployment(fleet)
    assert report(fleet, deployment["id"], "pending").status_code == 422
    assert report(fleet, deployment["id"], "exploded").status_code == 422
    missing = "00000000-0000-0000-0000-000000000000"
    assert report(fleet, missing, "in_progress").status_code == 404
    assert fleet.get(f"/deployments/{missing}").status_code == 404


def test_history_and_filters(fleet: TestClient, clock: FakeClock) -> None:
    failed = request_deployment(fleet, "1.3.0")
    report(fleet, failed["id"], "in_progress")
    report(fleet, failed["id"], "rolled_back", "bad image")
    clock.advance(60)
    retry = request_deployment(fleet, "1.3.0")

    history = fleet.get(f"/devices/{DEVICE}/deployments").json()
    rolled_back = fleet.get("/deployments", params={"status": "rolled_back"}).json()
    by_device = fleet.get("/deployments", params={"device_id": DEVICE, "status": "pending"}).json()

    assert [d["id"] for d in history] == [retry["id"], failed["id"]]  # newest first
    assert [d["id"] for d in rolled_back] == [failed["id"]]
    assert [d["id"] for d in by_device] == [retry["id"]]
    assert fleet.get("/devices/ghost/deployments").status_code == 404
    assert fleet.get("/deployments", params={"status": "bogus"}).status_code == 422


def test_database_enforces_one_active_deployment_per_device() -> None:
    """The partial unique index holds even if application checks are bypassed."""
    engine = create_db_engine("sqlite://")
    Base.metadata.create_all(engine)
    now = datetime(2026, 1, 1, tzinfo=UTC)

    def deployment(deployment_id: str, status: str) -> Deployment:
        return Deployment(
            id=deployment_id,
            device_id="d1",
            target_version="1.3.0",
            image="my-edge-app:1.3.0",
            status=status,
            created_at=now,
        )

    with Session(engine) as session:
        session.add(Device(device_id="d1", registered_at=now))
        session.add(SoftwareVersion(version="1.3.0", image="my-edge-app:1.3.0", created_at=now))
        session.commit()

        session.add(deployment("a", "succeeded"))
        session.add(deployment("b", "rolled_back"))
        session.add(deployment("c", "pending"))
        session.commit()  # any number of finished deployments plus one active is fine

        session.add(deployment("d", "in_progress"))
        with pytest.raises(IntegrityError):
            session.commit()
    engine.dispose()
