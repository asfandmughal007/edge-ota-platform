"""Where heartbeats go. The service depends only on the ``HeartbeatSink`` protocol,
so the transport (HTTP today, MQTT later) can change without touching it."""

from __future__ import annotations

import sys
from typing import Protocol, TextIO

import httpx
from pydantic import SecretStr

from edge_agent.models import Heartbeat


class PublishError(Exception):
    """A heartbeat could not be delivered."""


class HeartbeatSink(Protocol):
    def send(self, heartbeat: Heartbeat) -> None:
        """Deliver one heartbeat or raise ``PublishError``."""

    def close(self) -> None: ...


class StdoutHeartbeatSink:
    """Dry-run sink: prints each heartbeat as one JSON line."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self._stream = stream if stream is not None else sys.stdout

    def send(self, heartbeat: Heartbeat) -> None:
        self._stream.write(heartbeat.model_dump_json() + "\n")
        self._stream.flush()

    def close(self) -> None:
        return None


class HttpHeartbeatSink:
    """POSTs heartbeats to ``{backend_url}/devices/{device_id}/heartbeat``."""

    def __init__(
        self,
        backend_url: str,
        device_token: SecretStr,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=backend_url,
            timeout=timeout,
            transport=transport,
            headers={"Authorization": f"Bearer {device_token.get_secret_value()}"},
        )

    def send(self, heartbeat: Heartbeat) -> None:
        try:
            response = self._client.post(
                f"/devices/{heartbeat.device_id}/heartbeat",
                content=heartbeat.model_dump_json(),
                headers={"Content-Type": "application/json"},
            )
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            raise PublishError(f"backend returned HTTP {exc.response.status_code}") from exc
        except httpx.HTTPError as exc:
            raise PublishError(f"{type(exc).__name__}: {exc}") from exc

    def close(self) -> None:
        self._client.close()
