"""Container runtime abstraction.

The updater talks to ``ContainerRuntime`` only. ``DockerRuntime`` is the real
implementation; tests use an in-memory fake, so the update and rollback logic
is verified without a Docker daemon.
"""

from __future__ import annotations

from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass, field
from typing import Any, Protocol

import docker
import docker.errors


class ContainerRuntimeError(Exception):
    """A container operation failed."""


@dataclass(frozen=True, slots=True)
class ContainerInfo:
    name: str
    image: str
    running: bool
    labels: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class ContainerSpec:
    name: str
    image: str
    labels: Mapping[str, str] = field(default_factory=dict)
    environment: Mapping[str, str] = field(default_factory=dict)
    # container port -> host port
    ports: Mapping[int, int] = field(default_factory=dict)
    network: str | None = None


class ContainerRuntime(Protocol):
    def pull_image(self, image: str) -> None: ...

    def image_labels(self, image: str) -> Mapping[str, str] | None:
        """Labels of a locally present image, or None if it is not present."""

    def get(self, name: str) -> ContainerInfo | None: ...

    def run(self, spec: ContainerSpec) -> None: ...

    def start(self, name: str) -> None: ...

    def stop(self, name: str, timeout_seconds: int) -> None: ...

    def rename(self, name: str, new_name: str) -> None: ...

    def remove(self, name: str) -> None:
        """Force-remove a container. Removing a missing container is not an error."""

    def logs(self, name: str, tail: int) -> str: ...


@contextmanager
def _docker_errors(operation: str) -> Iterator[None]:
    """Translate Docker SDK failures into ``ContainerRuntimeError``."""
    try:
        yield
    # OSError covers "daemon not reachable" (connection refused, missing socket).
    except (docker.errors.DockerException, OSError) as exc:
        raise ContainerRuntimeError(f"{operation}: {exc}") from exc


class DockerRuntime:
    def __init__(self, client: docker.DockerClient | None = None) -> None:
        self._client_instance = client

    @property
    def _client(self) -> docker.DockerClient:
        # Created lazily so the agent can start (and keep sending heartbeats)
        # even when the Docker daemon is down.
        if self._client_instance is None:
            self._client_instance = docker.from_env()
        return self._client_instance

    def _find(self, name: str) -> Any | None:
        try:
            return self._client.containers.get(name)
        except docker.errors.NotFound:
            return None

    def _require(self, name: str) -> Any:
        container = self._find(name)
        if container is None:
            raise ContainerRuntimeError(f"container '{name}' does not exist")
        return container

    def pull_image(self, image: str) -> None:
        with _docker_errors("pull"):
            self._client.images.pull(image)

    def image_labels(self, image: str) -> Mapping[str, str] | None:
        with _docker_errors("inspect image"):
            try:
                found = self._client.images.get(image)
            except docker.errors.ImageNotFound:
                return None
            return dict(found.labels or {})

    def get(self, name: str) -> ContainerInfo | None:
        with _docker_errors("inspect"):
            container = self._find(name)
            if container is None:
                return None
            config = container.attrs.get("Config", {})
            return ContainerInfo(
                name=name,
                image=str(config.get("Image", "")),
                running=container.status == "running",
                labels=dict(container.labels or {}),
            )

    def run(self, spec: ContainerSpec) -> None:
        ports: dict[str, int | list[int] | tuple[str, int] | None] = {
            f"{container}/tcp": host for container, host in spec.ports.items()
        }
        with _docker_errors("run"):
            # types-docker's restart policy type omits the valid "unless-stopped".
            self._client.containers.run(  # type: ignore[call-overload]
                spec.image,
                name=spec.name,
                detach=True,
                labels=dict(spec.labels),
                environment=dict(spec.environment),
                ports=ports,
                network=spec.network,
                # Survive device reboots and app crashes without the agent's help.
                restart_policy={"Name": "unless-stopped"},
            )

    def start(self, name: str) -> None:
        with _docker_errors("start"):
            self._require(name).start()

    def stop(self, name: str, timeout_seconds: int) -> None:
        with _docker_errors("stop"):
            self._require(name).stop(timeout=timeout_seconds)

    def rename(self, name: str, new_name: str) -> None:
        with _docker_errors("rename"):
            self._require(name).rename(new_name)

    def remove(self, name: str) -> None:
        with _docker_errors("remove"):
            container = self._find(name)
            if container is not None:
                container.remove(force=True)

    def logs(self, name: str, tail: int) -> str:
        with _docker_errors("logs"):
            raw: bytes = self._require(name).logs(tail=tail)
            return raw.decode("utf-8", errors="replace")
