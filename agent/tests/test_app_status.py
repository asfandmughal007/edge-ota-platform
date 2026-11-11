"""Stage 7: the agent reports the state and health of the managed application."""

from __future__ import annotations

import json
import threading

import pytest
from fakes import FakeHealth, FakeRuntime, command, image

from edge_agent.collector import SystemCollector
from edge_agent.health import HealthResult
from edge_agent.models import AppStatus, Heartbeat
from edge_agent.runtime import ContainerInfo, ContainerRuntimeError
from edge_agent.service import HeartbeatService
from edge_agent.sinks import PublishError
from edge_agent.updater import VERSION_LABEL, AppConfig, Updater


class CapturingSink:
    def __init__(self) -> None:
        self.sent: list[Heartbeat] = []

    def send(self, heartbeat: Heartbeat) -> None:
        self.sent.append(heartbeat)


def make_updater(runtime: FakeRuntime, health: FakeHealth | None = None) -> Updater:
    return Updater(runtime, health or FakeHealth(), AppConfig())


def test_no_container_is_reported_as_absent() -> None:
    assert make_updater(FakeRuntime()).app_status() == AppStatus(state="absent")


def test_stopped_container_is_reported_with_its_version() -> None:
    runtime = FakeRuntime()
    runtime.seed_running("1.2.0")
    runtime.stop("edge-app", 0)

    assert make_updater(runtime).app_status() == AppStatus(state="stopped", version="1.2.0")


def test_running_healthy_container() -> None:
    runtime = FakeRuntime()
    runtime.seed_running("1.2.0")

    status = make_updater(runtime).app_status()

    assert status == AppStatus(state="running", healthy=True, version="1.2.0")


def test_running_but_failing_health_checks_is_reported_unhealthy() -> None:
    runtime = FakeRuntime()
    runtime.seed_running("1.2.0")

    status = make_updater(runtime, FakeHealth(healthy_versions=())).app_status()

    assert status == AppStatus(state="running", healthy=False, version="1.2.0")


def test_health_is_not_judged_during_an_update() -> None:
    """The new container is expected to be unready for a few seconds; that is not an incident."""
    runtime = FakeRuntime()
    runtime.seed_running("1.2.0")
    observed: list[AppStatus] = []

    class ObservingHealth(FakeHealth):
        def wait_until_healthy(self, url: str, expected_version: str | None) -> HealthResult:
            observed.append(updater.app_status())  # what a heartbeat would report right now
            return super().wait_until_healthy(url, expected_version)

    updater = make_updater(runtime, ObservingHealth())
    updater.apply(command("1.3.0"))

    assert observed == [AppStatus(state="updating")]
    assert updater.app_status().state == "running"  # back to normal afterwards


def test_updating_flag_is_cleared_even_if_the_update_blows_up() -> None:
    runtime = FakeRuntime()
    runtime.seed_running("1.2.0")
    updater = make_updater(runtime)

    def explode(*_args: object) -> None:
        raise RuntimeError("bug")

    runtime.pull_image = explode  # type: ignore[method-assign]
    with pytest.raises(RuntimeError):
        updater.apply(command("1.3.0"))

    assert updater.app_status().state == "running"


def test_docker_errors_propagate_to_the_caller() -> None:
    runtime = FakeRuntime()
    runtime.fail_on.add("get")
    with pytest.raises(ContainerRuntimeError):
        make_updater(runtime).app_status()


def test_container_without_a_version_label_has_no_version() -> None:
    runtime = FakeRuntime()
    runtime.containers["edge-app"] = ContainerInfo("edge-app", image("1.2.0"), True, {})

    status = make_updater(runtime).app_status()

    assert status.version is None
    assert VERSION_LABEL not in runtime.containers["edge-app"].labels


# --- heartbeat integration ---------------------------------------------------


def make_service(observer, sink: CapturingSink) -> HeartbeatService:  # type: ignore[no-untyped-def]
    return HeartbeatService(
        device_id="device-001",
        default_version="0.0.0",
        interval_seconds=1,
        collector=SystemCollector(initial_cpu_sample_seconds=0),
        sink=sink,
        app_observer=observer,
    )


def test_heartbeat_carries_app_status_and_the_running_version() -> None:
    sink = CapturingSink()
    service = make_service(lambda: AppStatus(state="running", healthy=True, version="1.3.0"), sink)

    assert service.beat() is True

    sent = sink.sent[0]
    assert sent.software_version == "1.3.0"  # not the configured default
    payload = json.loads(sent.model_dump_json())
    assert payload["app"] == {"state": "running", "healthy": True, "version": "1.3.0"}


def test_configured_version_is_used_when_nothing_is_running() -> None:
    sink = CapturingSink()
    make_service(lambda: AppStatus(state="absent"), sink).beat()
    assert sink.sent[0].software_version == "0.0.0"


def test_heartbeat_without_an_observer_has_no_app_section() -> None:
    sink = CapturingSink()
    make_service(lambda: None, sink).beat()
    assert sink.sent[0].app is None


def test_observer_failure_degrades_to_unknown_but_the_heartbeat_still_goes_out() -> None:
    sink = CapturingSink()

    def broken() -> AppStatus:
        raise ContainerRuntimeError("cannot connect to the Docker daemon")

    assert make_service(broken, sink).beat() is True
    assert sink.sent[0].app == AppStatus(state="unknown")


def test_delivery_failure_still_reported_with_app_status() -> None:
    class FailingSink:
        def send(self, heartbeat: Heartbeat) -> None:
            raise PublishError("down")

    service = make_service(lambda: AppStatus(state="absent"), CapturingSink())
    service._sink = FailingSink()  # type: ignore[assignment]
    assert service.beat() is False


def test_concurrent_status_reads_during_an_update_are_safe() -> None:
    runtime = FakeRuntime()
    runtime.seed_running("1.2.0")
    updater = make_updater(runtime)
    stop = threading.Event()
    errors: list[Exception] = []

    def reader() -> None:
        while not stop.is_set():
            try:
                updater.app_status()
            except Exception as exc:
                errors.append(exc)

    thread = threading.Thread(target=reader)
    thread.start()
    for deployment in range(20):
        updater.apply(command("1.3.0" if deployment % 2 == 0 else "1.2.0", f"dep-{deployment}"))
    stop.set()
    thread.join()

    assert errors == []
