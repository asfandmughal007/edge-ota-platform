from __future__ import annotations

import json
from typing import Any

import httpx
import pytest
from fakes import FakeHealth, FakeRuntime, image
from pydantic import SecretStr

from edge_agent.backend_client import BackendClient
from edge_agent.updater import AppConfig, Updater
from edge_agent.worker import DeploymentWorker

DEVICE = "device-001"


class FakeBackend:
    """Just enough of the deployment API, with switchable failures."""

    def __init__(self) -> None:
        self.deployment: dict[str, Any] | None = None
        self.reports: list[tuple[str, str | None]] = []
        self.requests: list[str] = []
        # status value -> HTTP code to answer with, or "down" for a transport error
        self.fail_reports: dict[str, int | str] = {}

    def offer(self, version: str, deployment_id: str = "dep-1") -> None:
        self.deployment = {
            "id": deployment_id,
            "device_id": DEVICE,
            "target_version": version,
            "image": image(version),
            "previous_version": "1.2.0",
            "status": "pending",
            "events": [],
        }

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(f"{request.method} {request.url.path}")
        if request.url.path == f"/devices/{DEVICE}/deployments/next":
            if self.deployment is None:
                return httpx.Response(204)
            return httpx.Response(200, json=self.deployment)

        assert self.deployment is not None
        assert request.url.path == f"/deployments/{self.deployment['id']}/status"
        body = json.loads(request.content)
        failure = self.fail_reports.get(body["status"])
        if failure == "down":
            raise httpx.ConnectError("connection refused", request=request)
        if isinstance(failure, int):
            return httpx.Response(failure)
        self.reports.append((body["status"], body["message"]))
        if body["status"] == "in_progress":
            self.deployment["status"] = "in_progress"
        else:
            self.deployment = None
        return httpx.Response(200, json={})


@pytest.fixture
def backend() -> FakeBackend:
    return FakeBackend()


@pytest.fixture
def runtime() -> FakeRuntime:
    fake = FakeRuntime()
    fake.seed_running("1.2.0")
    return fake


@pytest.fixture
def worker(backend: FakeBackend, runtime: FakeRuntime) -> DeploymentWorker:
    client = BackendClient(
        "http://backend:8000", SecretStr("tok"), transport=httpx.MockTransport(backend.handle)
    )
    updater = Updater(runtime, FakeHealth(), AppConfig())
    return DeploymentWorker(DEVICE, client, updater, poll_interval_seconds=0.001)


def test_idle_poll_does_nothing(
    worker: DeploymentWorker, backend: FakeBackend, runtime: FakeRuntime
) -> None:
    worker.poll_once()

    assert backend.requests == [f"GET /devices/{DEVICE}/deployments/next"]
    assert runtime.calls == []


def test_deployment_is_applied_and_reported(
    worker: DeploymentWorker, backend: FakeBackend, runtime: FakeRuntime
) -> None:
    backend.offer("1.3.0")

    worker.poll_once()

    assert backend.reports == [("in_progress", None), ("succeeded", "version 1.3.0 is healthy")]
    assert runtime.running_version() == "1.3.0"


def test_failed_update_is_reported_with_the_reason(
    worker: DeploymentWorker, backend: FakeBackend, runtime: FakeRuntime
) -> None:
    backend.offer("9.9.9")  # not in the registry

    worker.poll_once()

    assert backend.reports[0] == ("in_progress", None)
    status, message = backend.reports[1]
    assert status == "failed"
    assert message is not None and "pull failed" in message
    assert runtime.running_version() == "1.2.0"


def test_nothing_is_changed_if_the_backend_cannot_be_told_first(
    worker: DeploymentWorker, backend: FakeBackend, runtime: FakeRuntime
) -> None:
    backend.offer("1.3.0")
    backend.fail_reports["in_progress"] = 503

    worker.poll_once()

    assert runtime.calls == []
    assert runtime.running_version() == "1.2.0"

    backend.fail_reports.clear()
    worker.poll_once()
    assert runtime.running_version() == "1.3.0"


def test_final_report_is_retried_without_repeating_the_update(
    worker: DeploymentWorker, backend: FakeBackend, runtime: FakeRuntime
) -> None:
    backend.offer("1.3.0")
    backend.fail_reports["succeeded"] = "down"

    worker.poll_once()
    calls_after_update = list(runtime.calls)
    assert runtime.running_version() == "1.3.0"
    assert backend.reports == [("in_progress", None)]

    worker.poll_once()  # backend still unreachable
    backend.fail_reports.clear()
    worker.poll_once()  # delivered, then polls for new work

    assert backend.reports == [("in_progress", None), ("succeeded", "version 1.3.0 is healthy")]
    assert runtime.calls == calls_after_update
    assert backend.requests[-1] == f"GET /devices/{DEVICE}/deployments/next"


def test_rejected_report_is_dropped_so_later_deployments_are_not_blocked(
    worker: DeploymentWorker, backend: FakeBackend, runtime: FakeRuntime
) -> None:
    backend.offer("1.3.0")
    backend.fail_reports["succeeded"] = 409

    worker.poll_once()
    backend.fail_reports.clear()
    backend.offer("1.2.0", deployment_id="dep-2")
    worker.poll_once()

    assert backend.reports[-1] == ("succeeded", "version 1.2.0 is healthy")
    assert runtime.running_version() == "1.2.0"


def test_unexpected_errors_do_not_escape_the_poll(
    worker: DeploymentWorker, backend: FakeBackend
) -> None:
    backend.deployment = {"unexpected": "shape"}
    worker.poll_once()  # must not raise
