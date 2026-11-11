"""Service configuration, loaded from ``DEVICE_MGMT_*`` environment variables."""

from __future__ import annotations

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="DEVICE_MGMT_", extra="ignore")

    database_url: str = "sqlite:///./device_mgmt.db"

    # Operator credential. Required, with no default: the service refuses to
    # start rather than run open or with a well-known key.
    admin_api_key: SecretStr = Field(min_length=16)

    # Reject plain-HTTP requests and send HSTS. Enable whenever a TLS-terminating
    # proxy sits in front (it must set X-Forwarded-Proto).
    require_https: bool = False

    # A device is "online" until it has been silent for stale_after, then
    # "stale" until offline_after, then "offline". Defaults assume the agent's
    # 30 s interval: stale after 3 missed beats, offline after 10.
    heartbeat_stale_after_seconds: float = Field(default=90.0, gt=0)
    heartbeat_offline_after_seconds: float = Field(default=300.0, gt=0)

    log_level: str = "INFO"

    @model_validator(mode="after")
    def _offline_after_stale(self) -> Settings:
        if self.heartbeat_offline_after_seconds <= self.heartbeat_stale_after_seconds:
            raise ValueError(
                "heartbeat_offline_after_seconds must exceed heartbeat_stale_after_seconds"
            )
        return self
