"""HTTP client for the device management backend.

The only place in the agent that knows URLs and the auth header.
"""

from __future__ import annotations

from pathlib import Path

import httpx
from pydantic import SecretStr, ValidationError

from edge_agent.models import DeploymentCommand, Heartbeat, ReportStatus


class BackendError(Exception):
    """A backend call failed. ``status_code`` is None for transport errors."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def is_rejection(self) -> bool:
        """True if the backend understood the request and refused it (4xx).

        Retrying a rejection cannot help; retrying a timeout or 5xx can.
        """
        return self.status_code is not None and 400 <= self.status_code < 500


class BackendClient:
    def __init__(
        self,
        backend_url: str,
        device_token: SecretStr,
        timeout: float = 10.0,
        transport: httpx.BaseTransport | None = None,
        ca_bundle: Path | None = None,
    ) -> None:
        self._client = httpx.Client(
            base_url=backend_url,
            timeout=timeout,
            transport=transport,
            verify=str(ca_bundle) if ca_bundle is not None else True,
            headers={"Authorization": f"Bearer {device_token.get_secret_value()}"},
        )

    def post_heartbeat(self, heartbeat: Heartbeat) -> None:
        self._request(
            "POST",
            f"/devices/{heartbeat.device_id}/heartbeat",
            content=heartbeat.model_dump_json(),
            headers={"Content-Type": "application/json"},
        )

    def next_deployment(self, device_id: str) -> DeploymentCommand | None:
        response = self._request("GET", f"/devices/{device_id}/deployments/next")
        if response.status_code == httpx.codes.NO_CONTENT:
            return None
        try:
            return DeploymentCommand.model_validate_json(response.content)
        except ValidationError as exc:
            raise BackendError(f"malformed deployment command: {exc.error_count()} errors") from exc

    def report_status(
        self, deployment_id: str, status: ReportStatus, message: str | None = None
    ) -> None:
        self._request(
            "POST",
            f"/deployments/{deployment_id}/status",
            json={"status": status, "message": message},
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, url: str, **kwargs: object) -> httpx.Response:
        try:
            response = self._client.request(method, url, **kwargs)  # type: ignore[arg-type]
            response.raise_for_status()
        except httpx.HTTPStatusError as exc:
            code = exc.response.status_code
            raise BackendError(f"backend returned HTTP {code}", status_code=code) from exc
        except httpx.HTTPError as exc:
            raise BackendError(f"{type(exc).__name__}: {exc}") from exc
        return response
