"""Agent configuration, loaded from ``EDGE_AGENT_*`` environment variables.

Nothing secret has a default: the device token must be injected by the
environment (systemd EnvironmentFile, Docker secret, compose env).
"""

from __future__ import annotations

import socket
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="EDGE_AGENT_", extra="ignore")

    device_id: str = Field(
        default_factory=socket.gethostname,
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9._-]+$",
    )
    software_version: str = Field(default="0.0.0", min_length=1, max_length=64)

    backend_url: str | None = None
    device_token: SecretStr | None = None
    # Alternative to device_token: a file holding it (Docker/Kubernetes secret,
    # systemd credential). Keeps the token out of the process environment and
    # out of `docker inspect`.
    device_token_file: Path | None = None
    # PEM bundle of extra CA certificates, for a backend behind a private CA.
    # Certificate verification is always on; there is deliberately no way to
    # switch it off.
    ca_bundle: Path | None = None
    request_timeout_seconds: float = Field(default=10.0, gt=0)

    heartbeat_interval_seconds: float = Field(default=30.0, gt=0)
    disk_path: str = "/"
    connectivity_timeout_seconds: float = Field(default=3.0, gt=0)

    # --- OTA updates of the managed application container ---
    updates_enabled: bool = True
    deployment_poll_interval_seconds: float = Field(default=10.0, gt=0)
    app_container_name: str = Field(default="edge-app", pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    app_container_port: int = Field(default=8080, ge=1, le=65535)
    # Host port to publish the app on; unset when the agent reaches the app
    # over a Docker network instead.
    app_host_port: int | None = Field(default=8080, ge=1, le=65535)
    app_network: str | None = None
    # Derived from the settings above when unset; see ``resolved_app_health_url``.
    app_health_url: str | None = None
    app_health_timeout_seconds: float = Field(default=30.0, gt=0)
    app_health_interval_seconds: float = Field(default=2.0, gt=0)
    app_stop_timeout_seconds: int = Field(default=10, ge=0)
    image_pull_policy: Literal["always", "if_not_present"] = "always"

    dry_run: bool = False
    log_level: str = "INFO"

    @field_validator("backend_url")
    @classmethod
    def _normalise_backend_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        parts = urlsplit(value)
        if parts.scheme not in {"http", "https"} or not parts.hostname:
            raise ValueError("backend_url must be an http(s) URL")
        return value.rstrip("/")

    @model_validator(mode="after")
    def _load_token_file(self) -> AgentSettings:
        if self.device_token is None and self.device_token_file is not None:
            try:
                token = self.device_token_file.read_text(encoding="utf-8").strip()
            except OSError as exc:
                raise ValueError(f"cannot read device_token_file: {exc}") from exc
            if not token:
                raise ValueError(f"device_token_file {self.device_token_file} is empty")
            self.device_token = SecretStr(token)
        return self

    @model_validator(mode="after")
    def _require_backend_unless_dry_run(self) -> AgentSettings:
        if not self.dry_run and (self.backend_url is None or self.device_token is None):
            raise ValueError(
                "EDGE_AGENT_BACKEND_URL and EDGE_AGENT_DEVICE_TOKEN are required "
                "unless EDGE_AGENT_DRY_RUN is set"
            )
        return self

    @property
    def resolved_app_health_url(self) -> str:
        if self.app_health_url is not None:
            return self.app_health_url
        if self.app_network is not None:
            # On a shared Docker network the container name resolves via Docker DNS.
            return f"http://{self.app_container_name}:{self.app_container_port}/health"
        port = self.app_host_port if self.app_host_port is not None else self.app_container_port
        return f"http://127.0.0.1:{port}/health"

    @property
    def connectivity_target(self) -> tuple[str, int] | None:
        """Host and port probed to decide whether the backend is reachable."""
        if self.backend_url is None:
            return None
        parts = urlsplit(self.backend_url)
        assert parts.hostname is not None  # guaranteed by the validator
        return parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
