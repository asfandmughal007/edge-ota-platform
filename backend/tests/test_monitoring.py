"""Stage 7: heartbeat monitoring, application health, history and observability."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Iterator
from typing import Any

import pytest
from conftest import ADMIN_KEY, FakeClock, device_auth, make_settings, register_device
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session

from device_mgmt.api.deps import get_session
from device_mgmt.db.models import Device, HeartbeatRecord
from device_mgmt.main import create_app
from device_mgmt.services.monitor import HeartbeatMonitor

DEVICE = "device-001"


def beat(client: TestClient, device_id: str = DEVICE, **fields: Any) -> Any:
    payload: dict[str, Any] = {
        "device_id": device_id,
        "hostname": "edge-box",
        "software_version": "1.2.0",
        "cpu_percent": 10,
        "memory_percent": 20,
        "disk_percent": 30,
        "uptime_seconds": 100,
        "network": {"interfaces_up": ["eth0"], "ip_address": "10.0.0.5", "backend_reachable": True},
        "timestamp": "2026-01-01T12:00:00Z",
    }
    payload.update(fields)
    return client.post(
        f"/devices/{device_id}/heartbeat", json=payload, headers=device_auth(client, device_id)
    )


def app_status(
    healthy: bool | None, state: str = "running", version: str = "1.2.0"
) -> dict[str, Any]:
    return {"state": state, "healthy": healthy, "version": version}


def events(client: TestClient, device_id: str = DEVICE) -> list[dict[str, Any]]:
    response = client.get(f"/devices/{device_id}/events")
    assert response.status_code == 200
    body: list[dict[str, Any]] = response.json()
    return body


def monitor_of(client: TestClient) -> HeartbeatMonitor:
    monitor: HeartbeatMonitor = client.app.state.monitor  # type: ignore[attr-defined]
    return monitor


@pytest.fixture
def device(client: TestClient) -> TestClient:
    register_device(client, DEVICE)
    return client


# --- heartbeat monitor -------------------------------------------------------


def test_monitor_detects_a_device_going_quiet(device: TestClient, clock: FakeClock) -> None:
    beat(device)
    monitor = monitor_of(device)
    assert monitor.scan() == []  # still online

    clock.advance(100)
    stale = monitor.scan()
    clock.advance(250)
    offline = monitor.scan()

    assert [(c.previous, c.current.value) for c in stale] == [("online", "stale")]
    assert [(c.previous, c.current.value) for c in offline] == [("stale", "offline")]
    assert [e["event_type"] for e in events(device)] == ["offline", "stale", "online"]


def test_scanning_twice_does_not_repeat_events(device: TestClient, clock: FakeClock) -> None:
    beat(device)
    clock.advance(400)
    monitor = monitor_of(device)

    assert len(monitor.scan()) == 1
    assert monitor.scan() == []
    assert [e["event_type"] for e in events(device)] == ["offline", "online"]


def test_recovery_is_recorded_by_the_heartbeat_itself(device: TestClient, clock: FakeClock) -> None:
    beat(device)
    clock.advance(400)
    monitor_of(device).scan()

    beat(device)

    latest = events(device)[0]
    assert latest["event_type"] == "online"
    assert latest["detail"] == "offline -> online"
    assert monitor_of(device).scan() == []  # nothing left for the monitor to report


def test_a_device_that_never_reported_raises_no_events(
    device: TestClient, clock: FakeClock
) -> None:
    clock.advance(10_000)
    assert monitor_of(device).scan() == []
    assert events(device) == []


def test_monitor_logs_a_warning_when_a_device_goes_offline(
    device: TestClient, clock: FakeClock, caplog: pytest.LogCaptureFixture
) -> None:
    beat(device)
    clock.advance(400)

    with caplog.at_level(logging.INFO, logger="device_mgmt.services.monitor"):
        monitor_of(device).scan()

    record = next(
        r
        for r in caplog.records
        if r.name == "device_mgmt.services.monitor" and r.getMessage() == "device status changed"
    )
    assert record.levelno == logging.WARNING
    assert (record.device_id, record.to_status) == (DEVICE, "offline")  # type: ignore[attr-defined]


def test_background_loop_scans_and_stops_cleanly(device: TestClient, clock: FakeClock) -> None:
    beat(device)
    clock.advance(400)
    settings = make_settings(monitor_interval_seconds=0.01)
    monitor = HeartbeatMonitor(device.app.state.session_factory, settings, clock)  # type: ignore[attr-defined]

    async def run_briefly() -> None:
        stop = asyncio.Event()
        task = asyncio.create_task(monitor.run(stop))
        await asyncio.sleep(0.1)
        stop.set()
        await asyncio.wait_for(task, timeout=2)

    asyncio.run(run_briefly())

    assert events(device)[0]["event_type"] == "offline"


# --- retention ---------------------------------------------------------------


def test_old_heartbeats_are_pruned_but_device_state_is_kept(
    device: TestClient, clock: FakeClock
) -> None:
    beat(device)
    clock.advance(8 * 86400)  # retention is 7 days
    beat(device)
    session_factory = device.app.state.session_factory  # type: ignore[attr-defined]

    deleted = monitor_of(device).prune()

    with session_factory() as session:
        remaining = session.scalar(select(func.count()).select_from(HeartbeatRecord))
        known = session.get(Device, DEVICE)
    assert (deleted, remaining) == (1, 1)
    assert known is not None and known.last_seen_at is not None
    assert device.get(f"/devices/{DEVICE}/health").json()["metrics"] is not None


# --- application health ------------------------------------------------------


def test_application_health_is_part_of_device_health(device: TestClient) -> None:
    beat(device, app=app_status(True))

    health = device.get(f"/devices/{DEVICE}/health").json()

    assert health["application"] == app_status(True)
    assert health["healthy"] is True


def test_unhealthy_application_makes_the_device_unhealthy_though_online(
    device: TestClient,
) -> None:
    beat(device, app=app_status(False))

    health = device.get(f"/devices/{DEVICE}/health").json()

    assert health["status"] == "online"
    assert health["healthy"] is False
    assert health["application"]["healthy"] is False


def test_application_health_changes_are_recorded_once(device: TestClient) -> None:
    beat(device, app=app_status(True))
    for _ in range(3):
        beat(device, app=app_status(False, version="1.4.0"))  # repeated reports
    beat(device, app=app_status(True))

    types = [e["event_type"] for e in events(device)]

    assert types == ["app_recovered", "app_unhealthy", "online"]
    assert events(device)[1]["detail"] == "application running, version 1.4.0"


def test_unknown_application_health_is_not_treated_as_a_failure(device: TestClient) -> None:
    beat(device, app=app_status(None, state="unknown"))

    health = device.get(f"/devices/{DEVICE}/health").json()

    assert health["healthy"] is True
    assert [e["event_type"] for e in events(device)] == ["online"]


def test_heartbeats_without_application_data_still_work(device: TestClient) -> None:
    assert beat(device).status_code == 202
    assert device.get(f"/devices/{DEVICE}/health").json()["application"] is None


def test_invalid_application_state_is_rejected(device: TestClient) -> None:
    assert beat(device, app={"state": "on fire"}).status_code == 422


# --- device events API -------------------------------------------------------


def test_events_are_newest_first_and_paginated(device: TestClient, clock: FakeClock) -> None:
    beat(device)
    clock.advance(400)
    monitor_of(device).scan()
    beat(device)

    everything = [e["event_type"] for e in events(device)]
    page = device.get(f"/devices/{DEVICE}/events", params={"limit": 1, "offset": 1}).json()

    assert everything == ["online", "offline", "online"]
    assert [e["event_type"] for e in page] == ["offline"]


def test_events_require_operator_credentials_and_a_known_device(
    device: TestClient, anon: TestClient
) -> None:
    assert anon.get(f"/devices/{DEVICE}/events").status_code == 401
    assert device.get("/devices/ghost/events").status_code == 404


# --- fleet summary and metrics -----------------------------------------------


def make_fleet(client: TestClient, clock: FakeClock) -> None:
    for device_id, version in (
        ("device-001", "1.2.0"),
        ("device-002", "1.2.0"),
        ("device-003", "1.3.0"),
    ):
        register_device(client, device_id)
        beat(client, device_id, software_version=version)
    register_device(client, "device-004")  # never reports
    beat(client, "device-002", app=app_status(False))
    clock.advance(100)
    beat(client, "device-001")  # device-001 fresh, device-002 and -003 now stale


def test_fleet_summary_counts_devices_versions_and_apps(
    client: TestClient, clock: FakeClock
) -> None:
    make_fleet(client, clock)

    summary = client.get("/fleet/summary").json()

    assert summary["total_devices"] == 4
    assert summary["devices_by_status"] == {
        "never_seen": 1,
        "online": 1,
        "stale": 2,
        "offline": 0,
    }
    assert summary["devices_by_version"] == {"1.2.0": 2, "1.3.0": 1, "unknown": 1}
    assert summary["apps_unhealthy"] == 1


def test_fleet_summary_tracks_failed_and_rolled_back_deployments(
    client: TestClient, clock: FakeClock
) -> None:
    register_device(client, DEVICE, current_version="1.2.0")
    client.post("/versions", json={"version": "1.4.0", "image": "app:1.4.0"})

    def deploy_and_finish(outcome: str) -> None:
        deployment = client.post(
            "/deployments", json={"device_id": DEVICE, "target_version": "1.4.0"}
        ).json()
        for status in ("in_progress", outcome):
            client.post(
                f"/deployments/{deployment['id']}/status",
                json={"status": status, "message": "health check failed"},
                headers=device_auth(client, DEVICE),
            )

    deploy_and_finish("rolled_back")
    deploy_and_finish("failed")

    summary = client.get("/fleet/summary").json()
    assert summary["deployments_by_status"]["rolled_back"] == 1
    assert summary["deployments_by_status"]["failed"] == 1
    assert summary["unsuccessful_deployments_24h"] == 2

    clock.advance(25 * 3600)
    assert client.get("/fleet/summary").json()["unsuccessful_deployments_24h"] == 0
    # The history itself is never discarded.
    assert client.get("/deployments", params={"status": "rolled_back"}).json() != []


def test_metrics_are_in_prometheus_text_format(client: TestClient, clock: FakeClock) -> None:
    make_fleet(client, clock)

    response = client.get("/metrics")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain")
    body = response.text
    assert "# TYPE device_mgmt_devices gauge" in body
    assert 'device_mgmt_devices{status="online"} 1' in body
    assert 'device_mgmt_devices_by_version{version="1.2.0"} 2' in body
    assert "device_mgmt_apps_unhealthy 1" in body
    assert 'device_mgmt_deployments{status="failed"} 0' in body


def test_metrics_label_routes_by_template_not_raw_path(client: TestClient) -> None:
    for device_id in ("device-001", "device-002", "device-003"):
        register_device(client, device_id)
        client.get(f"/devices/{device_id}")

    body = client.get("/metrics").text

    assert 'route="/devices/{device_id}",status="200"} 3' in body
    assert "device-002" not in body  # label cardinality stays bounded


def test_metrics_and_summary_require_the_operator_key(client: TestClient, anon: TestClient) -> None:
    register_device(client, DEVICE)
    for path in ("/metrics", "/fleet/summary"):
        assert anon.get(path).status_code == 401
        assert anon.get(path, headers=device_auth(client, DEVICE)).status_code == 401


# --- probes ------------------------------------------------------------------


def test_readiness_checks_the_database(client: TestClient, anon: TestClient) -> None:
    assert anon.get("/readyz").json() == {"status": "ready"}

    class BrokenSession:
        def execute(self, *_args: Any, **_kwargs: Any) -> None:
            raise OperationalError("SELECT 1", {}, Exception("connection refused"))

    def broken() -> Iterator[Session]:
        yield BrokenSession()  # type: ignore[misc]

    client.app.dependency_overrides[get_session] = broken  # type: ignore[attr-defined]
    response = anon.get("/readyz")

    assert response.status_code == 503
    assert response.json() == {"status": "database_down"}
    assert anon.get("/healthz").status_code == 200  # liveness is independent of the database


# --- request logging ---------------------------------------------------------


def access_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == "device_mgmt.access"]


def test_requests_are_logged_with_structured_fields(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="device_mgmt.access"):
        client.get("/devices", headers={"X-Request-ID": "trace-abc.123"})

    (record,) = access_records(caplog)
    assert (record.method, record.path, record.status) == ("GET", "/devices", 200)  # type: ignore[attr-defined]
    assert record.request_id == "trace-abc.123"  # type: ignore[attr-defined]
    assert record.duration_ms >= 0  # type: ignore[attr-defined]


def test_request_id_is_generated_when_missing_or_unsafe(client: TestClient) -> None:
    generated = client.get("/devices").headers["X-Request-ID"]
    replaced = client.get("/devices", headers={"X-Request-ID": "bad id!<script>"}).headers[
        "X-Request-ID"
    ]

    assert len(generated) == 32
    assert replaced != "bad id!<script>" and len(replaced) == 32


def test_credentials_never_appear_in_logs(
    client: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.DEBUG):
        token = register_device(client, DEVICE)["device_token"]
        beat(client)
        client.post(f"/devices/{DEVICE}/token")
        client.get("/devices", headers={"Authorization": "Bearer edt_attacker_guess"})

    for record in caplog.records:
        everything = f"{record.getMessage()} {record.__dict__}"
        assert ADMIN_KEY not in everything
        assert token not in everything
        assert "edt_attacker_guess" not in everything


def test_probe_requests_are_not_logged_at_info(
    anon: TestClient, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.INFO, logger="device_mgmt.access"):
        anon.get("/healthz")
        anon.get("/readyz")
    assert access_records(caplog) == []


def test_https_rejections_are_still_counted_and_logged(caplog: pytest.LogCaptureFixture) -> None:
    app = create_app(make_settings(require_https=True, monitor_interval_seconds=0))
    # create_app() installs the JSON log handler on the root logger, replacing caplog's.
    logging.getLogger().addHandler(caplog.handler)
    with (
        TestClient(app, base_url="http://testserver") as plain,
        caplog.at_level(logging.INFO, logger="device_mgmt.access"),
    ):
        plain.get("/devices", headers={"X-API-Key": ADMIN_KEY})
    assert [r.status for r in access_records(caplog)] == [400]  # type: ignore[attr-defined]
    assert app.state.http_requests[("GET", "unmatched", 400)] == 1
