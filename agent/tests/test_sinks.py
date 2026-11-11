from __future__ import annotations

import io
import json
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from edge_agent.models import Heartbeat, NetworkStatus
from edge_agent.sinks import HttpHeartbeatSink, PublishError, StdoutHeartbeatSink


def make_heartbeat(**overrides: object) -> Heartbeat:
    fields: dict[str, object] = {
        "device_id": "device-001",
        "hostname": "edge-box",
        "software_version": "1.2.0",
        "cpu_percent": 12.5,
        "memory_percent": 40.0,
        "disk_percent": 71.3,
        "uptime_seconds": 3600.0,
        "network": NetworkStatus(
            interfaces_up=["eth0"], ip_address="10.0.0.5", backend_reachable=True
        ),
        "timestamp": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    }
    fields.update(overrides)
    return Heartbeat.model_validate(fields)


def test_stdout_sink_writes_one_json_line() -> None:
    stream = io.StringIO()
    StdoutHeartbeatSink(stream).send(make_heartbeat())

    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["device_id"] == "device-001"


def test_http_sink_posts_json_with_bearer_token() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(202)

    sink = HttpHeartbeatSink(
        "http://backend:8000", SecretStr("tok-123"), transport=httpx.MockTransport(handler)
    )
    sink.send(make_heartbeat())
    sink.close()

    request = seen[0]
    assert request.method == "POST"
    assert str(request.url) == "http://backend:8000/devices/device-001/heartbeat"
    assert request.headers["Authorization"] == "Bearer tok-123"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content)["cpu_percent"] == 12.5


def test_http_sink_wraps_error_status() -> None:
    sink = HttpHeartbeatSink(
        "http://backend:8000",
        SecretStr("tok-123"),
        transport=httpx.MockTransport(lambda _: httpx.Response(401)),
    )
    with pytest.raises(PublishError, match="401"):
        sink.send(make_heartbeat())


def test_http_sink_wraps_connection_errors_without_leaking_token() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused", request=request)

    sink = HttpHeartbeatSink(
        "http://backend:8000", SecretStr("tok-123"), transport=httpx.MockTransport(handler)
    )
    with pytest.raises(PublishError) as excinfo:
        sink.send(make_heartbeat())
    assert "tok-123" not in str(excinfo.value)


def test_heartbeat_rejects_out_of_range_and_naive_values() -> None:
    with pytest.raises(ValidationError):
        make_heartbeat(cpu_percent=140.0)
    with pytest.raises(ValidationError):
        make_heartbeat(timestamp=datetime(2026, 1, 2, 3, 4, 5))
