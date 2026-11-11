from __future__ import annotations

import pytest
from fastapi.testclient import TestClient


def publish(client: TestClient, version: str) -> None:
    response = client.post(
        "/versions", json={"version": version, "image": f"my-edge-app:{version}"}
    )
    assert response.status_code == 201, response.text


def test_publish_and_fetch_version(client: TestClient) -> None:
    response = client.post(
        "/versions",
        json={"version": "1.3.0", "image": "ghcr.io/acme/my-edge-app:1.3.0", "notes": "Fix X"},
    )

    assert response.status_code == 201
    assert response.json() == {
        "version": "1.3.0",
        "image": "ghcr.io/acme/my-edge-app:1.3.0",
        "notes": "Fix X",
        "created_at": "2026-01-01T12:00:00Z",
    }
    assert client.get("/versions/1.3.0").json() == response.json()


def test_versions_are_listed_in_semver_order(client: TestClient) -> None:
    for version in ("1.10.0", "1.2.0", "1.9.0", "1.1.0"):
        publish(client, version)

    listed = [v["version"] for v in client.get("/versions").json()]

    assert listed == ["1.1.0", "1.2.0", "1.9.0", "1.10.0"]


def test_duplicate_version_conflicts(client: TestClient) -> None:
    publish(client, "1.3.0")
    response = client.post("/versions", json={"version": "1.3.0", "image": "other:1.3.0"})
    assert response.status_code == 409
    assert client.get("/versions/1.3.0").json()["image"] == "my-edge-app:1.3.0"


@pytest.mark.parametrize("version", ["1.3", "v1.3.0", "1.3.0-beta", "01.3.0", "latest", ""])
def test_rejects_non_semver_versions(client: TestClient, version: str) -> None:
    response = client.post("/versions", json={"version": version, "image": "my-edge-app:x"})
    assert response.status_code == 422


@pytest.mark.parametrize("image", ["", "has space:1.0.0", "; rm -rf /", "-flag"])
def test_rejects_malformed_image_references(client: TestClient, image: str) -> None:
    response = client.post("/versions", json={"version": "1.3.0", "image": image})
    assert response.status_code == 422


def test_unknown_version_is_404(client: TestClient) -> None:
    assert client.get("/versions/9.9.9").status_code == 404
