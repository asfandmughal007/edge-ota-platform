from __future__ import annotations

from typing import Any

import pytest
from conftest import FakeClock, device_auth, register_device
from fastapi.testclient import TestClient


def heartbeat(device_id: str = "device-001", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_version": 1,
        "device_id": device_id,
        "hostname": "edge-box",
        "software_version": "1.2.0",
        "cpu_percent": 12.5,
        "memory_percent": 40.0,
        "disk_percent": 71.3,
        "uptime_seconds": 3600.0,
        "network": {
            "interfaces_up": ["eth0"],
            "ip_address": "10.0.0.5",
            "backend_reachable": True,
        },
        # Deliberately not the server's time: liveness must not depend on it.
        "timestamp": "2020-06-01T00:00:00Z",
    }
    payload.update(overrides)
    return payload


register = register_device


def test_liveness(client: TestClient) -> None:
    assert client.get("/healthz").json() == {"status": "ok"}


def test_register_and_fetch_device(client: TestClient) -> None:
    created = register(client, hostname="edge-box", current_version="1.1.0")
    token = created.pop("device_token")
    assert token.startswith("edt_")

    assert created == {
        "device_id": "device-001",
        "hostname": "edge-box",
        "current_version": "1.1.0",
        "status": "never_seen",
        "registered_at": "2026-01-01T12:00:00Z",
        "last_seen_at": None,
    }
    assert client.get("/devices/device-001").json() == created


def test_duplicate_registration_conflicts(client: TestClient) -> None:
    register(client)
    response = client.post("/devices", json={"device_id": "device-001"})
    assert response.status_code == 409
    assert "already registered" in response.json()["detail"]


@pytest.mark.parametrize("device_id", ["", "has space", "../etc", "x" * 65])
def test_register_rejects_invalid_device_id(client: TestClient, device_id: str) -> None:
    assert client.post("/devices", json={"device_id": device_id}).status_code == 422


def test_register_rejects_unknown_fields(client: TestClient) -> None:
    response = client.post("/devices", json={"device_id": "device-001", "is_admin": True})
    assert response.status_code == 422


def test_list_is_ordered_and_paginated(client: TestClient) -> None:
    for device_id in ("device-003", "device-001", "device-002"):
        register(client, device_id)

    everything = [d["device_id"] for d in client.get("/devices").json()]
    page = [d["device_id"] for d in client.get("/devices", params={"limit": 1, "offset": 1}).json()]

    assert everything == ["device-001", "device-002", "device-003"]
    assert page == ["device-002"]
    assert client.get("/devices", params={"limit": 0}).status_code == 422


@pytest.mark.parametrize("path", ["/devices/ghost", "/devices/ghost/health"])
def test_unknown_device_is_404(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


def test_heartbeat_updates_device(client: TestClient, clock: FakeClock) -> None:
    register(client, current_version="1.1.0")
    clock.advance(10)

    response = client.post(
        "/devices/device-001/heartbeat", json=heartbeat(), headers=device_auth(client, "device-001")
    )

    assert response.status_code == 202
    assert response.json() == {"device_id": "device-001", "received_at": "2026-01-01T12:00:10Z"}
    device = client.get("/devices/device-001").json()
    assert device["status"] == "online"
    assert device["hostname"] == "edge-box"
    assert device["current_version"] == "1.2.0"
    assert device["last_seen_at"] == "2026-01-01T12:00:10Z"


def test_heartbeat_for_unregistered_device_is_rejected(client: TestClient) -> None:
    response = client.post(
        "/devices/ghost/heartbeat", json=heartbeat("ghost"), headers={"Authorization": "Bearer x"}
    )
    assert response.status_code == 401


def test_heartbeat_device_id_must_match_path(client: TestClient) -> None:
    register(client, "device-001")
    register(client, "device-002")

    response = client.post(
        "/devices/device-001/heartbeat",
        json=heartbeat("device-002"),
        headers=device_auth(client, "device-001"),
    )

    assert response.status_code == 400
    assert client.get("/devices/device-002").json()["status"] == "never_seen"


@pytest.mark.parametrize(
    "overrides",
    [
        {"cpu_percent": 140},
        {"uptime_seconds": -1},
        {"timestamp": "2026-01-01T12:00:00"},  # naive
        {"schema_version": 2},
        {"network": {"interfaces_up": "eth0"}},
    ],
)
def test_heartbeat_validation(client: TestClient, overrides: dict[str, Any]) -> None:
    register(client)
    response = client.post(
        "/devices/device-001/heartbeat",
        json=heartbeat(**overrides),
        headers=device_auth(client, "device-001"),
    )
    assert response.status_code == 422
    assert client.get("/devices/device-001").json()["status"] == "never_seen"


def test_health_before_first_heartbeat(client: TestClient) -> None:
    register(client)
    assert client.get("/devices/device-001/health").json() == {
        "device_id": "device-001",
        "status": "never_seen",
        "healthy": False,
        "current_version": None,
        "last_seen_at": None,
        "seconds_since_last_heartbeat": None,
        "metrics": None,
    }


def test_health_reports_latest_metrics(client: TestClient, clock: FakeClock) -> None:
    register(client)
    client.post(
        "/devices/device-001/heartbeat",
        json=heartbeat(cpu_percent=10),
        headers=device_auth(client, "device-001"),
    )
    clock.advance(30)
    client.post(
        "/devices/device-001/heartbeat",
        json=heartbeat(cpu_percent=55.5),
        headers=device_auth(client, "device-001"),
    )
    clock.advance(5)

    health = client.get("/devices/device-001/health").json()

    assert health["status"] == "online"
    assert health["healthy"] is True
    assert health["seconds_since_last_heartbeat"] == 5
    assert health["metrics"]["cpu_percent"] == 55.5
    assert health["metrics"]["network"]["ip_address"] == "10.0.0.5"
    assert health["metrics"]["reported_at"] == "2020-06-01T00:00:00Z"


def test_status_degrades_as_heartbeats_stop(client: TestClient, clock: FakeClock) -> None:
    register(client)
    client.post(
        "/devices/device-001/heartbeat", json=heartbeat(), headers=device_auth(client, "device-001")
    )

    def status_after(seconds: float) -> tuple[str, bool]:
        clock.advance(seconds)
        health = client.get("/devices/device-001/health").json()
        return health["status"], health["healthy"]

    assert status_after(89) == ("online", True)
    assert status_after(1) == ("stale", False)  # 90 s
    assert status_after(209) == ("stale", False)  # 299 s
    assert status_after(1) == ("offline", False)  # 300 s

    client.post(
        "/devices/device-001/heartbeat", json=heartbeat(), headers=device_auth(client, "device-001")
    )
    assert status_after(0) == ("online", True)
