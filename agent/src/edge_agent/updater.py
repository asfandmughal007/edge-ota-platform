"""OTA update of the managed application container.

Sequence for one deployment:

    1. pull the new image            } nothing running is touched yet;
    2. validate it                   } a bad image costs zero downtime
    3. stop the old container and keep it as ``<name>-previous``
    4. start the new container
    5. health-check it
    6. on success, remove ``<name>-previous``

The running container carries the version it was deployed as in a label, so
the container itself is the agent's source of truth for "what am I running".
No state file can drift out of sync with reality.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal

from edge_agent.health import HealthChecker
from edge_agent.models import DeploymentCommand
from edge_agent.runtime import ContainerRuntime, ContainerRuntimeError, ContainerSpec

logger = logging.getLogger(__name__)

VERSION_LABEL = "edge-ota.version"
DEPLOYMENT_LABEL = "edge-ota.deployment-id"
OCI_VERSION_LABEL = "org.opencontainers.image.version"

Outcome = Literal["succeeded", "failed", "rolled_back"]


@dataclass(frozen=True, slots=True)
class AppConfig:
    """How the managed application container is run on this device."""

    container_name: str = "edge-app"
    container_port: int = 8080
    host_port: int | None = 8080
    network: str | None = None
    health_url: str = "http://127.0.0.1:8080/health"
    pull_policy: Literal["always", "if_not_present"] = "always"
    stop_timeout_seconds: int = 10

    @property
    def previous_container_name(self) -> str:
        return f"{self.container_name}-previous"


@dataclass(frozen=True, slots=True)
class UpdateResult:
    outcome: Outcome
    message: str


class UpdateStepError(Exception):
    """A step of the update failed; the message is safe to report to the backend."""


class Updater:
    def __init__(self, runtime: ContainerRuntime, health: HealthChecker, config: AppConfig) -> None:
        self._runtime = runtime
        self._health = health
        self._config = config

    def current_version(self) -> str | None:
        """Version of the running app container, or None if there is none."""
        container = self._runtime.get(self._config.container_name)
        if container is None or not container.running:
            return None
        return container.labels.get(VERSION_LABEL)

    def apply(self, command: DeploymentCommand) -> UpdateResult:
        log = {"deployment_id": command.id, "target_version": command.target_version}
        try:
            if self._already_running(command):
                # The agent restarted after switching containers but before
                # reporting. Do not switch again; just verify and report.
                logger.info("target version already running, verifying health", extra=log)
            else:
                self._step("pull", log, lambda: self._pull(command))
                self._step("validate", log, lambda: self._validate(command))
                self._step("stop_old", log, self._stop_old)
                self._step("start_new", log, lambda: self._start_new(command))
            self._step("health_check", log, lambda: self._check_health(command))
        except UpdateStepError as exc:
            logger.error("update failed", extra={**log, "error": str(exc)})
            return UpdateResult("failed", str(exc))

        self._remove_previous(log)
        logger.info("update succeeded", extra=log)
        return UpdateResult("succeeded", f"version {command.target_version} is healthy")

    # --- steps ---------------------------------------------------------------

    def _step(self, name: str, log: dict[str, str], action: Callable[[], None]) -> None:
        logger.info("update step started", extra={**log, "step": name})
        try:
            action()
        except ContainerRuntimeError as exc:
            raise UpdateStepError(f"{name} failed: {exc}") from exc
        logger.info("update step finished", extra={**log, "step": name})

    def _already_running(self, command: DeploymentCommand) -> bool:
        try:
            container = self._runtime.get(self._config.container_name)
        except ContainerRuntimeError as exc:
            raise UpdateStepError(f"inspect failed: {exc}") from exc
        return (
            container is not None
            and container.running
            and container.labels.get(DEPLOYMENT_LABEL) == command.id
        )

    def _pull(self, command: DeploymentCommand) -> None:
        if (
            self._config.pull_policy == "if_not_present"
            and self._runtime.image_labels(command.image) is not None
        ):
            return
        self._runtime.pull_image(command.image)

    def _validate(self, command: DeploymentCommand) -> None:
        labels = self._runtime.image_labels(command.image)
        if labels is None:
            raise UpdateStepError(f"validate failed: image {command.image} is not present")
        declared = labels.get(OCI_VERSION_LABEL)
        if declared is not None and declared != command.target_version:
            raise UpdateStepError(
                f"validate failed: image {command.image} declares version {declared}, "
                f"expected {command.target_version}"
            )

    def _stop_old(self) -> None:
        name = self._config.container_name
        previous = self._config.previous_container_name
        # A leftover from an earlier interrupted update would block the rename.
        self._runtime.remove(previous)
        current = self._runtime.get(name)
        if current is None:
            return  # first install on this device
        if current.running:
            self._runtime.stop(name, self._config.stop_timeout_seconds)
        self._runtime.rename(name, previous)

    def _start_new(self, command: DeploymentCommand) -> None:
        config = self._config
        ports = {config.container_port: config.host_port} if config.host_port is not None else {}
        self._runtime.run(
            ContainerSpec(
                name=config.container_name,
                image=command.image,
                labels={VERSION_LABEL: command.target_version, DEPLOYMENT_LABEL: command.id},
                ports=ports,
                network=config.network,
            )
        )

    def _check_health(self, command: DeploymentCommand) -> None:
        result = self._health.wait_until_healthy(self._config.health_url, command.target_version)
        if not result.healthy:
            raise UpdateStepError(f"health check failed: {result.detail}")

    def _remove_previous(self, log: dict[str, str]) -> None:
        try:
            self._runtime.remove(self._config.previous_container_name)
        except ContainerRuntimeError as exc:
            # The new version is healthy; a leftover stopped container is
            # untidy, not a failed deployment. The next update removes it.
            logger.warning("could not remove previous container", extra={**log, "error": str(exc)})
