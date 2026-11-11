"""Where heartbeats go. The service depends only on the ``HeartbeatSink`` protocol,
so the transport (HTTP today, MQTT later) can change without touching it."""

from __future__ import annotations

import sys
from typing import Protocol, TextIO

from edge_agent.backend_client import BackendClient, BackendError
from edge_agent.models import Heartbeat


class PublishError(Exception):
    """A heartbeat could not be delivered."""


class HeartbeatSink(Protocol):
    def send(self, heartbeat: Heartbeat) -> None:
        """Deliver one heartbeat or raise ``PublishError``."""


class StdoutHeartbeatSink:
    """Dry-run sink: prints each heartbeat as one JSON line."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout

    def send(self, heartbeat: Heartbeat) -> None:
        self._stream.write(heartbeat.model_dump_json() + "\n")
        self._stream.flush()


class HttpHeartbeatSink:
    """POSTs heartbeats to ``{backend_url}/devices/{device_id}/heartbeat``."""

    def __init__(self, client: BackendClient) -> None:
        self._client = client

    def send(self, heartbeat: Heartbeat) -> None:
        try:
            self._client.post_heartbeat(heartbeat)
        except BackendError as exc:
            raise PublishError(str(exc)) from exc
