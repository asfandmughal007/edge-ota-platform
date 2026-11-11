from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from device_mgmt.config import Settings
from device_mgmt.main import create_app

ADMIN_KEY = "test-admin-key-0123456789"


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


def make_settings(**overrides: Any) -> Settings:
    fields: dict[str, Any] = {
        "database_url": "sqlite://",
        "admin_api_key": ADMIN_KEY,
        # Tests drive the monitor explicitly (see monitor_of) so results are deterministic.
        "monitor_interval_seconds": 0,
        "heartbeat_stale_after_seconds": 90,
        "heartbeat_offline_after_seconds": 300,
    }
    fields.update(overrides)
    return Settings(**fields)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def client(clock: FakeClock) -> Iterator[TestClient]:
    """An operator client: sends the admin API key on every request.

    Device calls must add ``device_auth(client, device_id)`` explicitly.
    """
    with TestClient(create_app(make_settings(), clock=clock)) as test_client:
        test_client.headers["X-API-Key"] = ADMIN_KEY
        test_client.device_tokens = {}  # type: ignore[attr-defined]
        yield test_client


@pytest.fixture
def anon(client: TestClient) -> TestClient:
    """A client for the same app that presents no credentials at all."""
    return TestClient(client.app)


def register_device(
    client: TestClient, device_id: str = "device-001", **fields: Any
) -> dict[str, Any]:
    """Register a device via the API and remember its token for ``device_auth``."""
    response = client.post("/devices", json={"device_id": device_id, **fields})
    assert response.status_code == 201, response.text
    body: dict[str, Any] = response.json()
    client.device_tokens[device_id] = body["device_token"]  # type: ignore[attr-defined]
    return body


def bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def device_auth(client: TestClient, device_id: str = "device-001") -> dict[str, str]:
    return bearer(client.device_tokens[device_id])  # type: ignore[attr-defined]
