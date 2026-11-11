"""Stage 5: a failed update must leave the device on its last good version."""

from __future__ import annotations

import pytest
from fakes import FakeHealth, FakeRuntime, command, image

from edge_agent.runtime import ContainerInfo
from edge_agent.updater import DEPLOYMENT_LABEL, VERSION_LABEL, AppConfig, Updater

BROKEN = "1.4.0"


@pytest.fixture
def runtime() -> FakeRuntime:
    fake = FakeRuntime(versions=("1.2.0", "1.3.0", BROKEN))
    fake.seed_running("1.3.0")
    return fake


@pytest.fixture
def health() -> FakeHealth:
    return FakeHealth(healthy_versions=("1.2.0", "1.3.0"))  # 1.4.0 never becomes healthy


def make_updater(runtime: FakeRuntime, health: FakeHealth) -> Updater:
    return Updater(runtime, health, AppConfig())


def test_failed_health_check_rolls_back_in_the_documented_order(
    runtime: FakeRuntime, health: FakeHealth
) -> None:
    result = make_updater(runtime, health).apply(command(BROKEN))

    assert result.outcome == "rolled_back"
    assert runtime.calls == [
        f"pull {image(BROKEN)}",
        "stop edge-app",
        "rename edge-app edge-app-previous",
        f"run edge-app {image(BROKEN)}",
        # health check fails here
        "remove edge-app",  # 1. stop the new version
        "rename edge-app-previous edge-app",  # 2. restore the previous one
        "start edge-app",
    ]
    assert runtime.running_version() == "1.3.0"
    assert list(runtime.containers) == ["edge-app"]


def test_restored_version_is_health_checked_too(runtime: FakeRuntime, health: FakeHealth) -> None:
    make_updater(runtime, health).apply(command(BROKEN))

    assert [version for _url, version in health.checks] == [BROKEN, "1.3.0"]


def test_report_explains_what_failed_and_what_was_restored(
    runtime: FakeRuntime, health: FakeHealth
) -> None:
    result = make_updater(runtime, health).apply(command(BROKEN))

    assert "health check failed" in result.message
    assert "HTTP 500" in result.message
    assert "last container logs: last 5 log lines of edge-app" in result.message
    assert result.message.endswith("rolled back to version 1.3.0")


def test_restored_container_keeps_its_original_identity(
    runtime: FakeRuntime, health: FakeHealth
) -> None:
    """Rollback restarts the container that ran before, not a fresh one from the image."""
    before = runtime.containers["edge-app"]

    make_updater(runtime, health).apply(command(BROKEN))

    after = runtime.containers["edge-app"]
    assert (after.image, after.labels) == (before.image, before.labels)
    assert not any(call == f"run edge-app {image('1.3.0')}" for call in runtime.calls)


def test_first_install_failure_has_nothing_to_roll_back_to(health: FakeHealth) -> None:
    runtime = FakeRuntime(versions=(BROKEN,))

    result = make_updater(runtime, health).apply(command(BROKEN))

    assert result.outcome == "failed"
    assert "no previous version to roll back to" in result.message
    assert runtime.containers == {}  # the broken container is not left running


def test_rollback_failure_is_reported_as_failed(runtime: FakeRuntime, health: FakeHealth) -> None:
    runtime.fail_on.add("start")  # previous container refuses to start again

    result = make_updater(runtime, health).apply(command(BROKEN))

    assert result.outcome == "failed"
    assert "health check failed" in result.message
    assert "rollback failed: start: simulated failure" in result.message


def test_unhealthy_previous_version_is_reported_as_failed(runtime: FakeRuntime) -> None:
    nothing_healthy = FakeHealth(healthy_versions=())

    result = make_updater(runtime, nothing_healthy).apply(command(BROKEN))

    assert result.outcome == "failed"
    assert "restored version 1.3.0 but it is unhealthy" in result.message
    assert runtime.running_version() == "1.3.0"  # still the best available state


def test_device_can_be_updated_again_after_a_rollback(
    runtime: FakeRuntime, health: FakeHealth
) -> None:
    updater = make_updater(runtime, health)
    assert updater.apply(command(BROKEN, deployment_id="dep-1")).outcome == "rolled_back"

    result = updater.apply(command("1.2.0", deployment_id="dep-2"))

    assert result.outcome == "succeeded"
    assert runtime.running_version() == "1.2.0"


def test_resumed_deployment_that_turns_out_unhealthy_is_rolled_back(health: FakeHealth) -> None:
    """Agent restarted after starting the broken version, before judging it."""
    runtime = FakeRuntime(versions=("1.3.0", BROKEN))
    runtime.seed_running(BROKEN, deployment_id="dep-1")
    runtime.containers["edge-app-previous"] = ContainerInfo(
        "edge-app-previous",
        image("1.3.0"),
        False,
        {VERSION_LABEL: "1.3.0", DEPLOYMENT_LABEL: "old"},
    )

    result = make_updater(runtime, health).apply(command(BROKEN, deployment_id="dep-1"))

    assert result.outcome == "rolled_back"
    assert runtime.running_version() == "1.3.0"


def test_interrupted_switch_keeps_the_only_copy_of_the_previous_version(
    health: FakeHealth,
) -> None:
    """Agent died after setting the old container aside but before starting the new one.

    On retry there is no ``edge-app``, only ``edge-app-previous``. It must
    survive so that a failing retry can still roll back.
    """
    runtime = FakeRuntime(versions=("1.3.0", BROKEN))
    runtime.images[image("1.3.0")] = runtime.registry[image("1.3.0")]
    runtime.containers["edge-app-previous"] = ContainerInfo(
        "edge-app-previous",
        image("1.3.0"),
        False,
        {VERSION_LABEL: "1.3.0", DEPLOYMENT_LABEL: "old"},
    )

    result = make_updater(runtime, health).apply(command(BROKEN))

    assert result.outcome == "rolled_back"
    assert runtime.running_version() == "1.3.0"


def test_very_long_failure_reports_fit_the_backend_limit(
    runtime: FakeRuntime, health: FakeHealth, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runtime, "logs", lambda name, tail: "x" * 5000)

    result = make_updater(runtime, health).apply(command(BROKEN))

    assert result.outcome == "rolled_back"
    assert len(result.message) <= 1900
