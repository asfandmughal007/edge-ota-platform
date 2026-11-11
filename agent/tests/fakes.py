"""In-memory stand-ins for Docker and the app health endpoint."""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime

from edge_agent.health import HealthResult
from edge_agent.models import DeploymentCommand, Heartbeat, NetworkStatus
from edge_agent.runtime import ContainerInfo, ContainerRuntimeError, ContainerSpec
from edge_agent.updater import DEPLOYMENT_LABEL, OCI_VERSION_LABEL, VERSION_LABEL

REGISTRY = "registry.test/my-edge-app"


def image(version: str) -> str:
    return f"{REGISTRY}:{version}"


def command(version: str = "1.3.0", deployment_id: str = "dep-1") -> DeploymentCommand:
    return DeploymentCommand(
        id=deployment_id, target_version=version, image=image(version), status="pending"
    )


def make_heartbeat(**overrides: object) -> Heartbeat:
    fields: dict[str, object] = {
        "device_id": "device-001",
        "hostname": "edge-box",
        "software_version": "1.2.0",
        "cpu_percent": 12.5,
        "memory_percent": 40.0,
        "disk_percent": 71.3,
        "uptime_seconds": 3600.0,
        "network": NetworkStatus(
            interfaces_up=["eth0"], ip_address="10.0.0.5", backend_reachable=True
        ),
        "timestamp": datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC),
    }
    fields.update(overrides)
    return Heartbeat.model_validate(fields)


class FakeRuntime:
    """Behaves like Docker for the operations the updater uses.

    ``registry`` holds images that can be pulled, ``images`` those present
    locally. Add an operation name to ``fail_on`` to make it raise.
    """

    def __init__(self, versions: tuple[str, ...] = ("1.2.0", "1.3.0")) -> None:
        self.registry: dict[str, dict[str, str]] = {
            image(v): {OCI_VERSION_LABEL: v} for v in versions
        }
        self.images: dict[str, dict[str, str]] = {}
        self.containers: dict[str, ContainerInfo] = {}
        self.calls: list[str] = []
        self.fail_on: set[str] = set()

    def _record(self, operation: str, detail: str) -> None:
        self.calls.append(f"{operation} {detail}")
        if operation in self.fail_on:
            raise ContainerRuntimeError(f"{operation}: simulated failure")

    def _require(self, name: str) -> ContainerInfo:
        if name not in self.containers:
            raise ContainerRuntimeError(f"container '{name}' does not exist")
        return self.containers[name]

    def _set_running(self, name: str, running: bool) -> None:
        old = self._require(name)
        self.containers[name] = ContainerInfo(old.name, old.image, running, old.labels)

    def seed_running(
        self, version: str, name: str = "edge-app", deployment_id: str = "old"
    ) -> None:
        """Pretend ``version`` was deployed earlier and is running."""
        self.images[image(version)] = self.registry[image(version)]
        self.containers[name] = ContainerInfo(
            name, image(version), True, {VERSION_LABEL: version, DEPLOYMENT_LABEL: deployment_id}
        )

    # --- ContainerRuntime ----------------------------------------------------

    def pull_image(self, image: str) -> None:
        self._record("pull", image)
        if image not in self.registry:
            raise ContainerRuntimeError(f"pull: manifest for {image} not found")
        self.images[image] = self.registry[image]

    def image_labels(self, image: str) -> Mapping[str, str] | None:
        return self.images.get(image)

    def get(self, name: str) -> ContainerInfo | None:
        if "get" in self.fail_on:
            raise ContainerRuntimeError("get: cannot connect to the Docker daemon")
        return self.containers.get(name)

    def run(self, spec: ContainerSpec) -> None:
        self._record("run", f"{spec.name} {spec.image}")
        if spec.name in self.containers:
            raise ContainerRuntimeError(f"run: name '{spec.name}' is already in use")
        self.containers[spec.name] = ContainerInfo(spec.name, spec.image, True, dict(spec.labels))
        self.last_spec = spec

    def start(self, name: str) -> None:
        self._record("start", name)
        self._set_running(name, True)

    def stop(self, name: str, timeout_seconds: int) -> None:
        self._record("stop", name)
        self._set_running(name, False)

    def rename(self, name: str, new_name: str) -> None:
        self._record("rename", f"{name} {new_name}")
        if new_name in self.containers:
            raise ContainerRuntimeError(f"rename: name '{new_name}' is already in use")
        old = self._require(name)
        del self.containers[name]
        self.containers[new_name] = ContainerInfo(new_name, old.image, old.running, old.labels)

    def remove(self, name: str) -> None:
        if name in self.containers:
            self._record("remove", name)
            del self.containers[name]

    def logs(self, name: str, tail: int) -> str:
        self._require(name)
        return f"last {tail} log lines of {name}"

    # --- assertions helpers --------------------------------------------------

    def running_version(self, name: str = "edge-app") -> str | None:
        container = self.containers.get(name)
        if container is None or not container.running:
            return None
        return container.labels.get(VERSION_LABEL)


class FakeHealth:
    """Reports healthy only for versions listed in ``healthy_versions``."""

    def __init__(self, healthy_versions: tuple[str, ...] = ("1.2.0", "1.3.0")) -> None:
        self.healthy_versions = set(healthy_versions)
        self.checks: list[tuple[str, str | None]] = []

    def wait_until_healthy(self, url: str, expected_version: str | None) -> HealthResult:
        self.checks.append((url, expected_version))
        if expected_version in self.healthy_versions:
            return HealthResult(True, "HTTP 200")
        return HealthResult(False, "not healthy after 30s (15 attempts); last result: HTTP 500")
