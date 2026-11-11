from __future__ import annotations

import io
import json
from datetime import datetime

import httpx
import pytest
from fakes import make_heartbeat
from pydantic import SecretStr, ValidationError

from edge_agent.backend_client import BackendClient
from edge_agent.sinks import HttpHeartbeatSink, PublishError, StdoutHeartbeatSink


def test_stdout_sink_writes_one_json_line() -> None:
    stream = io.StringIO()
    StdoutHeartbeatSink(stream).send(make_heartbeat())

    lines = stream.getvalue().splitlines()
    assert len(lines) == 1
    assert json.loads(lines[0])["device_id"] == "device-001"


def test_http_sink_delivers_through_the_backend_client() -> None:
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(202)

    client = BackendClient(
        "http://backend:8000", SecretStr("tok"), transport=httpx.MockTransport(handler)
    )
    HttpHeartbeatSink(client).send(make_heartbeat())

    assert paths == ["/devices/device-001/heartbeat"]


def test_http_sink_converts_backend_errors_to_publish_errors() -> None:
    client = BackendClient(
        "http://backend:8000",
        SecretStr("tok"),
        transport=httpx.MockTransport(lambda _: httpx.Response(401)),
    )
    with pytest.raises(PublishError, match="401"):
        HttpHeartbeatSink(client).send(make_heartbeat())


def test_heartbeat_rejects_out_of_range_and_naive_values() -> None:
    with pytest.raises(ValidationError):
        make_heartbeat(cpu_percent=140.0)
    with pytest.raises(ValidationError):
        make_heartbeat(timestamp=datetime(2026, 1, 2, 3, 4, 5))
