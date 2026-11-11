from __future__ import annotations

import socket

import pytest
from pydantic import ValidationError

from edge_agent.config import AgentSettings


def test_dry_run_needs_no_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDGE_AGENT_DRY_RUN", "true")
    settings = AgentSettings()
    assert settings.device_id == socket.gethostname()
    assert settings.connectivity_target is None


def test_backend_and_token_required_when_not_dry_run() -> None:
    with pytest.raises(ValidationError, match="EDGE_AGENT_DEVICE_TOKEN"):
        AgentSettings()


def test_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDGE_AGENT_DEVICE_ID", "device-001")
    monkeypatch.setenv("EDGE_AGENT_BACKEND_URL", "https://api.example.test/")
    monkeypatch.setenv("EDGE_AGENT_DEVICE_TOKEN", "s3cret")
    monkeypatch.setenv("EDGE_AGENT_HEARTBEAT_INTERVAL_SECONDS", "5")

    settings = AgentSettings()

    assert settings.device_id == "device-001"
    assert settings.backend_url == "https://api.example.test"
    assert settings.heartbeat_interval_seconds == 5
    assert settings.connectivity_target == ("api.example.test", 443)


def test_token_is_not_leaked_by_repr(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDGE_AGENT_BACKEND_URL", "http://backend:8000")
    monkeypatch.setenv("EDGE_AGENT_DEVICE_TOKEN", "s3cret")
    assert "s3cret" not in repr(AgentSettings())


@pytest.mark.parametrize("url", ["ftp://host", "not-a-url", "http://"])
def test_rejects_bad_backend_url(monkeypatch: pytest.MonkeyPatch, url: str) -> None:
    monkeypatch.setenv("EDGE_AGENT_BACKEND_URL", url)
    monkeypatch.setenv("EDGE_AGENT_DEVICE_TOKEN", "s3cret")
    with pytest.raises(ValidationError, match="backend_url"):
        AgentSettings()


def test_rejects_device_id_with_path_characters(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("EDGE_AGENT_DRY_RUN", "true")
    monkeypatch.setenv("EDGE_AGENT_DEVICE_ID", "../admin")
    with pytest.raises(ValidationError):
        AgentSettings()
