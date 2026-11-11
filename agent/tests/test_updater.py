from __future__ import annotations

import pytest
from fakes import FakeHealth, FakeRuntime, command, image

from edge_agent.runtime import ContainerInfo
from edge_agent.updater import DEPLOYMENT_LABEL, OCI_VERSION_LABEL, AppConfig, Updater

CONFIG = AppConfig()


@pytest.fixture
def runtime() -> FakeRuntime:
    fake = FakeRuntime()
    fake.seed_running("1.2.0")
    return fake


@pytest.fixture
def health() -> FakeHealth:
    return FakeHealth()


def make_updater(runtime: FakeRuntime, health: FakeHealth, config: AppConfig = CONFIG) -> Updater:
    return Updater(runtime, health, config)


def test_update_replaces_old_version_in_the_documented_order(
    runtime: FakeRuntime, health: FakeHealth
) -> None:
    result = make_updater(runtime, health).apply(command("1.3.0"))

    assert result.outcome == "succeeded"
    assert runtime.calls == [
        f"pull {image('1.3.0')}",
        "stop edge-app",
        "rename edge-app edge-app-previous",
        f"run edge-app {image('1.3.0')}",
        "remove edge-app-previous",
    ]
    assert health.checks == [("http://127.0.0.1:8080/health", "1.3.0")]
    assert runtime.running_version() == "1.3.0"
    assert list(runtime.containers) == ["edge-app"]


def test_new_container_is_labelled_and_published(runtime: FakeRuntime, health: FakeHealth) -> None:
    make_updater(runtime, health).apply(command("1.3.0", deployment_id="dep-42"))

    spec = runtime.last_spec
    assert spec.labels == {"edge-ota.version": "1.3.0", "edge-ota.deployment-id": "dep-42"}
    assert spec.ports == {8080: 8080}
    assert spec.network is None


def test_network_mode_publishes_no_host_port(runtime: FakeRuntime, health: FakeHealth) -> None:
    config = AppConfig(host_port=None, network="edge-net", health_url="http://edge-app:8080/health")

    make_updater(runtime, health, config).apply(command("1.3.0"))

    assert runtime.last_spec.ports == {}
    assert runtime.last_spec.network == "edge-net"
    assert health.checks[0][0] == "http://edge-app:8080/health"


def test_first_install_has_nothing_to_stop(health: FakeHealth) -> None:
    runtime = FakeRuntime()

    result = make_updater(runtime, health).apply(command("1.2.0"))

    assert result.outcome == "succeeded"
    assert runtime.calls == [f"pull {image('1.2.0')}", f"run edge-app {image('1.2.0')}"]
    assert runtime.running_version() == "1.2.0"


def test_pull_failure_leaves_the_running_version_untouched(
    runtime: FakeRuntime, health: FakeHealth
) -> None:
    result = make_updater(runtime, health).apply(command("9.9.9"))

    assert result.outcome == "failed"
    assert "pull failed" in result.message
    assert runtime.calls == [f"pull {image('9.9.9')}"]
    assert runtime.running_version() == "1.2.0"
    assert health.checks == []


def test_image_declaring_a_different_version_is_rejected_before_any_downtime(
    runtime: FakeRuntime, health: FakeHealth
) -> None:
    runtime.registry[image("1.3.0")] = {OCI_VERSION_LABEL: "1.1.0"}  # mis-tagged image

    result = make_updater(runtime, health).apply(command("1.3.0"))

    assert result.outcome == "failed"
    assert "declares version 1.1.0, expected 1.3.0" in result.message
    assert not any(call.startswith(("stop", "run")) for call in runtime.calls)
    assert runtime.running_version() == "1.2.0"


def test_image_without_version_label_is_accepted(runtime: FakeRuntime, health: FakeHealth) -> None:
    runtime.registry[image("1.3.0")] = {}
    assert make_updater(runtime, health).apply(command("1.3.0")).outcome == "succeeded"


def test_if_not_present_policy_skips_pull_for_local_images(
    runtime: FakeRuntime, health: FakeHealth
) -> None:
    runtime.images[image("1.3.0")] = {OCI_VERSION_LABEL: "1.3.0"}
    del runtime.registry[image("1.3.0")]  # a pull would fail

    result = make_updater(runtime, health, AppConfig(pull_policy="if_not_present")).apply(
        command("1.3.0")
    )

    assert result.outcome == "succeeded"
    assert not any(call.startswith("pull") for call in runtime.calls)


def test_stale_previous_container_does_not_block_the_update(
    runtime: FakeRuntime, health: FakeHealth
) -> None:
    runtime.containers["edge-app-previous"] = ContainerInfo(
        "edge-app-previous", image("1.1.0"), False, {}
    )

    result = make_updater(runtime, health).apply(command("1.3.0"))

    assert result.outcome == "succeeded"
    assert runtime.calls[1] == "remove edge-app-previous"
    assert list(runtime.containers) == ["edge-app"]


def test_resumed_deployment_is_verified_without_switching_again(health: FakeHealth) -> None:
    """Agent crashed after starting the new container but before reporting."""
    runtime = FakeRuntime()
    runtime.seed_running("1.3.0", deployment_id="dep-1")
    runtime.containers["edge-app-previous"] = ContainerInfo(
        "edge-app-previous", image("1.2.0"), False, {DEPLOYMENT_LABEL: "old"}
    )

    result = make_updater(runtime, health).apply(command("1.3.0", deployment_id="dep-1"))

    assert result.outcome == "succeeded"
    assert runtime.calls == ["remove edge-app-previous"]
    assert health.checks == [("http://127.0.0.1:8080/health", "1.3.0")]


def test_redeploying_same_version_under_a_new_deployment_does_switch(
    runtime: FakeRuntime, health: FakeHealth
) -> None:
    runtime.seed_running("1.3.0", deployment_id="dep-1")

    make_updater(runtime, health).apply(command("1.3.0", deployment_id="dep-2"))

    assert f"run edge-app {image('1.3.0')}" in runtime.calls


@pytest.mark.parametrize("operation", ["stop", "rename", "run"])
def test_runtime_errors_become_failed_results(
    runtime: FakeRuntime, health: FakeHealth, operation: str
) -> None:
    runtime.fail_on.add(operation)

    result = make_updater(runtime, health).apply(command("1.3.0"))

    assert result.outcome == "failed"
    assert "simulated failure" in result.message


def test_docker_daemon_unreachable_is_a_failed_result_not_a_crash(
    runtime: FakeRuntime, health: FakeHealth
) -> None:
    runtime.fail_on.add("get")

    result = make_updater(runtime, health).apply(command("1.3.0"))

    assert result.outcome == "failed"
    assert "cannot connect to the Docker daemon" in result.message


def test_success_survives_failure_to_clean_up_previous_container(
    runtime: FakeRuntime, health: FakeHealth
) -> None:
    runtime.fail_on.add("remove")

    result = make_updater(runtime, health).apply(command("1.3.0"))

    assert result.outcome == "succeeded"
    assert runtime.running_version() == "1.3.0"


def test_current_version_reflects_the_running_container(
    runtime: FakeRuntime, health: FakeHealth
) -> None:
    updater = make_updater(runtime, health)
    assert updater.current_version() == "1.2.0"

    updater.apply(command("1.3.0"))
    assert updater.current_version() == "1.3.0"

    runtime.stop("edge-app", 0)
    assert updater.current_version() is None
    assert make_updater(FakeRuntime(), health).current_version() is None
