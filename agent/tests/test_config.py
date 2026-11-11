from __future__ import annotations

import socket
from pathlib import Path

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


def test_app_health_url_is_derived_from_how_the_app_is_exposed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDGE_AGENT_DRY_RUN", "true")
    assert AgentSettings().resolved_app_health_url == "http://127.0.0.1:8080/health"

    monkeypatch.setenv("EDGE_AGENT_APP_HOST_PORT", "9001")
    assert AgentSettings().resolved_app_health_url == "http://127.0.0.1:9001/health"

    monkeypatch.setenv("EDGE_AGENT_APP_NETWORK", "edge-net")
    monkeypatch.setenv("EDGE_AGENT_APP_CONTAINER_NAME", "edge-app-device-001")
    assert AgentSettings().resolved_app_health_url == "http://edge-app-device-001:8080/health"

    monkeypatch.setenv("EDGE_AGENT_APP_HEALTH_URL", "http://10.0.0.9/ready")
    assert AgentSettings().resolved_app_health_url == "http://10.0.0.9/ready"


def test_token_can_be_read_from_a_file(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("edt_from_file\n", encoding="utf-8")
    monkeypatch.setenv("EDGE_AGENT_BACKEND_URL", "https://api.example.test")
    monkeypatch.setenv("EDGE_AGENT_DEVICE_TOKEN_FILE", str(token_file))

    settings = AgentSettings()

    assert settings.device_token is not None
    assert settings.device_token.get_secret_value() == "edt_from_file"
    assert "edt_from_file" not in repr(settings)


def test_explicit_token_wins_over_token_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    token_file = tmp_path / "token"
    token_file.write_text("from_file", encoding="utf-8")
    monkeypatch.setenv("EDGE_AGENT_BACKEND_URL", "https://api.example.test")
    monkeypatch.setenv("EDGE_AGENT_DEVICE_TOKEN", "from_env")
    monkeypatch.setenv("EDGE_AGENT_DEVICE_TOKEN_FILE", str(token_file))

    assert AgentSettings().device_token.get_secret_value() == "from_env"  # type: ignore[union-attr]


@pytest.mark.parametrize("content", [None, "   \n"])
def test_missing_or_empty_token_file_is_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, content: str | None
) -> None:
    token_file = tmp_path / "token"
    if content is not None:
        token_file.write_text(content, encoding="utf-8")
    monkeypatch.setenv("EDGE_AGENT_BACKEND_URL", "https://api.example.test")
    monkeypatch.setenv("EDGE_AGENT_DEVICE_TOKEN_FILE", str(token_file))

    with pytest.raises(ValidationError, match="device_token_file"):
        AgentSettings()


def test_host_port_is_published_by_default_but_not_on_a_shared_network(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EDGE_AGENT_DRY_RUN", "true")
    assert AgentSettings().published_host_port == 8080

    monkeypatch.setenv("EDGE_AGENT_APP_NETWORK", "edge-ota")
    assert AgentSettings().published_host_port is None  # many devices can share one host

    monkeypatch.setenv("EDGE_AGENT_APP_HOST_PORT", "9100")
    assert AgentSettings().published_host_port == 9100
