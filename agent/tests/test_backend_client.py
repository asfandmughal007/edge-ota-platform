from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest
from fakes import make_heartbeat
from pydantic import SecretStr

from edge_agent.backend_client import BackendClient, BackendError

Handler = Callable[[httpx.Request], httpx.Response]


def make_client(handler: Handler) -> BackendClient:
    return BackendClient(
        "http://backend:8000", SecretStr("tok-123"), transport=httpx.MockTransport(handler)
    )


def test_heartbeat_is_posted_as_json_with_bearer_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202)

    make_client(handler).post_heartbeat(make_heartbeat())

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "http://backend:8000/devices/device-001/heartbeat"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content)["cpu_percent"] == 12.5


def test_next_deployment_returns_none_on_204() -> None:
    client = make_client(lambda _: httpx.Response(204))
    assert client.next_deployment("device-001") is None


def test_next_deployment_parses_command_and_ignores_unknown_fields() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/devices/device-001/deployments/next"
        return httpx.Response(
            200,
            json={
                "id": "dep-1",
                "device_id": "device-001",
                "target_version": "1.3.0",
                "image": "registry.test/my-edge-app:1.3.0",
                "previous_version": "1.2.0",
                "status": "pending",
                "events": [],
                "field_added_in_a_future_backend": True,
            },
        )

    command = make_client(handler).next_deployment("device-001")

    assert command is not None
    assert (command.id, command.target_version, command.previous_version) == (
        "dep-1",
        "1.3.0",
        "1.2.0",
    )
    assert command.image == "registry.test/my-edge-app:1.3.0"


def test_malformed_command_is_a_backend_error() -> None:
    client = make_client(lambda _: httpx.Response(200, json={"id": "dep-1"}))
    with pytest.raises(BackendError, match="malformed deployment command"):
        client.next_deployment("device-001")


def test_report_status_posts_status_and_message() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    make_client(handler).report_status("dep-1", "rolled_back", "health check failed")

    assert seen[0].url.path == "/deployments/dep-1/status"
    assert json.loads(seen[0].content) == {
        "status": "rolled_back",
        "message": "health check failed",
    }


@pytest.mark.parametrize(("code", "rejection"), [(401, True), (409, True), (500, False)])
def test_http_errors_carry_status_and_retry_hint(code: int, rejection: bool) -> None:
    client = make_client(lambda _: httpx.Response(code))

    with pytest.raises(BackendError, match=str(code)) as excinfo:
        client.report_status("dep-1", "succeeded")

    assert excinfo.value.status_code == code
    assert excinfo.value.is_rejection is rejection


def test_transport_errors_are_retryable_and_do_not_leak_the_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    with pytest.raises(BackendError) as excinfo:
        make_client(handler).post_heartbeat(make_heartbeat())

    assert excinfo.value.status_code is None
    assert excinfo.value.is_rejection is False
    assert "tok-123" not in str(excinfo.value)
