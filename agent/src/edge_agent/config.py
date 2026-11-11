"""Agent configuration, loaded from ``EDGE_AGENT_*`` environment variables.

Nothing secret has a default: the device token must be injected by the
environment (systemd EnvironmentFile, Docker secret, compose env).
"""

from __future__ import annotations

import socket
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
    request_timeout_seconds: float = Field(default=10.0, gt=0)

    heartbeat_interval_seconds: float = Field(default=30.0, gt=0)
    disk_path: str = "/"
    connectivity_timeout_seconds: float = Field(default=3.0, gt=0)

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
    def _require_backend_unless_dry_run(self) -> AgentSettings:
        if not self.dry_run and (self.backend_url is None or self.device_token is None):
            raise ValueError(
                "EDGE_AGENT_BACKEND_URL and EDGE_AGENT_DEVICE_TOKEN are required "
                "unless EDGE_AGENT_DRY_RUN is set"
            )
        return self

    @property
    def connectivity_target(self) -> tuple[str, int] | None:
        """Host and port probed to decide whether the backend is reachable."""
        if self.backend_url is None:
            return None
        parts = urlsplit(self.backend_url)
        assert parts.hostname is not None  # guaranteed by the validator
        return parts.hostname, parts.port or (443 if parts.scheme == "https" else 80)
