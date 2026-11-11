"""Stage 6: authentication, authorisation and transport policy."""

from __future__ import annotations

import hashlib
from typing import Any

import pytest
from conftest import ADMIN_KEY, bearer, device_auth, make_settings, register_device
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from device_mgmt.config import Settings
from device_mgmt.db.models import Device
from device_mgmt.main import create_app
from device_mgmt.security import TOKEN_PREFIX, generate_device_token, hash_token, keys_match

DEPLOYMENT_ID = "00000000-0000-0000-0000-000000000000"

HEARTBEAT: dict[str, Any] = {
    "device_id": "device-001",
    "hostname": "edge-box",
    "software_version": "1.2.0",
    "cpu_percent": 1,
    "memory_percent": 1,
    "disk_percent": 1,
    "uptime_seconds": 1,
    "network": {"interfaces_up": [], "ip_address": None, "backend_reachable": None},
    "timestamp": "2026-01-01T12:00:00Z",
}

# Every operator endpoint: (method, path, json body)
ADMIN_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/devices", {"device_id": "x"}),
    ("GET", "/devices", None),
    ("GET", "/devices/device-001", None),
    ("GET", "/devices/device-001/health", None),
    ("POST", "/devices/device-001/token", None),
    ("GET", "/devices/device-001/deployments", None),
    ("POST", "/versions", {"version": "1.0.0", "image": "x:1"}),
    ("GET", "/versions", None),
    ("GET", "/versions/1.0.0", None),
    ("POST", "/deployments", {"device_id": "device-001", "target_version": "1.0.0"}),
    ("GET", "/deployments", None),
    ("GET", f"/deployments/{DEPLOYMENT_ID}", None),
]

# Every device-agent endpoint
DEVICE_ENDPOINTS: list[tuple[str, str, dict[str, Any] | None]] = [
    ("POST", "/devices/device-001/heartbeat", HEARTBEAT),
    ("GET", "/devices/device-001/deployments/next", None),
    ("POST", f"/deployments/{DEPLOYMENT_ID}/status", {"status": "in_progress"}),
]


@pytest.fixture
def registered(client: TestClient) -> dict[str, Any]:
    return register_device(client, "device-001")


# --- operator key ------------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), ADMIN_ENDPOINTS)
def test_operator_endpoints_require_the_api_key(
    anon: TestClient, registered: dict[str, Any], method: str, path: str, body: Any
) -> None:
    missing = anon.request(method, path, json=body)
    wrong = anon.request(method, path, json=body, headers={"X-API-Key": "wrong-key-0123456789"})
    device_token = anon.request(method, path, json=body, headers=bearer(registered["device_token"]))

    assert missing.status_code == wrong.status_code == device_token.status_code == 401


def test_correct_key_reaches_operator_endpoints(anon: TestClient) -> None:
    response = anon.get("/devices", headers={"X-API-Key": ADMIN_KEY})
    assert response.status_code == 200


# --- device tokens -----------------------------------------------------------


@pytest.mark.parametrize(("method", "path", "body"), DEVICE_ENDPOINTS)
def test_device_endpoints_reject_missing_wrong_and_operator_credentials(
    anon: TestClient, registered: dict[str, Any], method: str, path: str, body: Any
) -> None:
    assert anon.request(method, path, json=body).status_code == 401
    assert anon.request(method, path, json=body, headers=bearer("edt_wrong")).status_code == 401
    # The operator key is not a device credential.
    as_operator = anon.request(method, path, json=body, headers={"X-API-Key": ADMIN_KEY})
    assert as_operator.status_code == 401


def test_unknown_device_and_wrong_token_are_indistinguishable(
    anon: TestClient, registered: dict[str, Any]
) -> None:
    ghost = anon.get("/devices/ghost/deployments/next", headers=bearer("edt_whatever"))
    wrong = anon.get("/devices/device-001/deployments/next", headers=bearer("edt_whatever"))

    assert ghost.status_code == wrong.status_code == 401
    assert ghost.json() == wrong.json()


def test_a_device_cannot_act_for_another_device(client: TestClient) -> None:
    register_device(client, "device-001")
    register_device(client, "device-002")
    attacker = device_auth(client, "device-001")

    heartbeat = client.post(
        "/devices/device-002/heartbeat",
        json={**HEARTBEAT, "device_id": "device-002"},
        headers=attacker,
    )
    poll = client.get("/devices/device-002/deployments/next", headers=attacker)

    assert heartbeat.status_code == poll.status_code == 403
    assert client.get("/devices/device-002").json()["status"] == "never_seen"


def test_a_device_cannot_report_on_another_devices_deployment(client: TestClient) -> None:
    register_device(client, "device-001", current_version="1.0.0")
    register_device(client, "device-002")
    client.post("/versions", json={"version": "1.1.0", "image": "app:1.1.0"})
    deployment = client.post(
        "/deployments", json={"device_id": "device-001", "target_version": "1.1.0"}
    ).json()

    stolen = client.post(
        f"/deployments/{deployment['id']}/status",
        json={"status": "succeeded"},
        headers=device_auth(client, "device-002"),
    )

    assert stolen.status_code == 404  # not 403: deployment IDs cannot be probed
    assert client.get(f"/deployments/{deployment['id']}").json()["status"] == "pending"


# --- token lifecycle ---------------------------------------------------------


def test_registration_returns_the_token_once(client: TestClient) -> None:
    created = register_device(client, "device-001")

    assert created["device_token"].startswith(TOKEN_PREFIX)
    assert "device_token" not in client.get("/devices/device-001").json()
    assert all("device_token" not in d for d in client.get("/devices").json())


def test_only_a_hash_of_the_token_is_stored(client: TestClient) -> None:
    token = register_device(client, "device-001")["device_token"]

    with Session(client.app.state.engine) as session:  # type: ignore[attr-defined]
        stored = session.scalars(select(Device.token_hash)).one()

    assert stored != token
    assert stored == hashlib.sha256(token.encode()).hexdigest()


def test_rotation_invalidates_the_old_token_immediately(client: TestClient) -> None:
    old = register_device(client, "device-001")["device_token"]
    path = "/devices/device-001/deployments/next"
    assert client.get(path, headers=bearer(old)).status_code == 204

    rotated = client.post("/devices/device-001/token")
    new = rotated.json()["device_token"]

    assert rotated.status_code == 200
    assert new != old
    assert client.get(path, headers=bearer(old)).status_code == 401
    assert client.get(path, headers=bearer(new)).status_code == 204


def test_rotating_an_unknown_device_is_404(client: TestClient) -> None:
    assert client.post("/devices/ghost/token").status_code == 404


def test_each_registration_gets_a_distinct_token(client: TestClient) -> None:
    tokens = {register_device(client, f"device-{i:03d}")["device_token"] for i in range(5)}
    assert len(tokens) == 5


# --- liveness and docs -------------------------------------------------------


def test_liveness_probe_needs_no_credentials(anon: TestClient) -> None:
    assert anon.get("/healthz").status_code == 200


def test_openapi_documents_both_security_schemes(anon: TestClient) -> None:
    schemes = anon.get("/openapi.json").json()["components"]["securitySchemes"]
    assert {s["type"] for s in schemes.values()} == {"apiKey", "http"}


# --- response hardening ------------------------------------------------------


def test_responses_are_not_cacheable(client: TestClient) -> None:
    response = client.post("/devices", json={"device_id": "device-001"})  # carries a token
    assert response.headers["Cache-Control"] == "no-store"
    assert response.headers["X-Content-Type-Options"] == "nosniff"


# --- HTTPS -------------------------------------------------------------------


def test_https_policy_rejects_plain_http_and_adds_hsts() -> None:
    app = create_app(make_settings(require_https=True))
    headers = {"X-API-Key": ADMIN_KEY}

    with TestClient(app, base_url="http://testserver") as plain:
        assert plain.get("/devices", headers=headers).status_code == 400
        assert plain.get("/healthz").status_code == 200  # probes stay reachable
    with TestClient(app, base_url="https://testserver") as secure:
        response = secure.get("/devices", headers=headers)
        assert response.status_code == 200
        assert "max-age" in response.headers["Strict-Transport-Security"]


def test_https_is_not_enforced_by_default(client: TestClient) -> None:
    response = client.get("/devices")
    assert response.status_code == 200
    assert "Strict-Transport-Security" not in response.headers


# --- configuration and primitives --------------------------------------------


def test_service_refuses_to_start_without_an_admin_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DEVICE_MGMT_ADMIN_API_KEY", raising=False)
    with pytest.raises(ValidationError, match="admin_api_key"):
        Settings()  # type: ignore[call-arg]


def test_weak_admin_keys_are_rejected() -> None:
    with pytest.raises(ValidationError, match="admin_api_key"):
        make_settings(admin_api_key="short")


def test_admin_key_is_not_leaked_by_repr() -> None:
    assert ADMIN_KEY not in repr(make_settings())


def test_token_primitives() -> None:
    token = generate_device_token()
    assert token.startswith(TOKEN_PREFIX) and len(token) > 40
    assert token != generate_device_token()
    assert hash_token(token) == hash_token(token) != hash_token(token + "x")
    assert keys_match(token, token)
    assert not keys_match(token, token[:-1])
    assert not keys_match("", token)
