"""Entry point: ``edge-agent`` or ``python -m edge_agent``."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from collections.abc import Callable, Sequence
from contextlib import ExitStack
from types import FrameType

from pydantic import ValidationError

from edge_agent.backend_client import BackendClient
from edge_agent.collector import SystemCollector
from edge_agent.config import AgentSettings
from edge_agent.health import HttpHealthChecker
from edge_agent.logging_setup import configure_logging
from edge_agent.runtime import ContainerRuntimeError, DockerRuntime
from edge_agent.service import HeartbeatService
from edge_agent.sinks import HeartbeatSink, HttpHeartbeatSink, StdoutHeartbeatSink
from edge_agent.updater import AppConfig, Updater
from edge_agent.worker import DeploymentWorker

logger = logging.getLogger(__name__)


def build_updater(settings: AgentSettings, stack: ExitStack) -> Updater:
    health = HttpHealthChecker(
        timeout_seconds=settings.app_health_timeout_seconds,
        interval_seconds=settings.app_health_interval_seconds,
    )
    stack.callback(health.close)
    return Updater(
        runtime=DockerRuntime(),
        health=health,
        config=AppConfig(
            container_name=settings.app_container_name,
            container_port=settings.app_container_port,
            host_port=settings.app_host_port,
            network=settings.app_network,
            health_url=settings.resolved_app_health_url,
            pull_policy=settings.image_pull_policy,
            stop_timeout_seconds=settings.app_stop_timeout_seconds,
        ),
    )


def version_provider(settings: AgentSettings, updater: Updater | None) -> Callable[[], str]:
    """Report the version of the container actually running, when there is one.

    Falls back to the configured version if updates are disabled, nothing is
    deployed yet, or Docker cannot be reached.
    """

    def provide() -> str:
        if updater is not None:
            try:
                running = updater.current_version()
            except ContainerRuntimeError as exc:
                logger.debug("could not read running version", extra={"error": str(exc)})
            else:
                if running is not None:
                    return running
        return settings.software_version

    return provide


def build_heartbeat_service(
    settings: AgentSettings, sink: HeartbeatSink, updater: Updater | None
) -> HeartbeatService:
    collector = SystemCollector(
        disk_path=settings.disk_path,
        connectivity_target=settings.connectivity_target,
        connectivity_timeout=settings.connectivity_timeout_seconds,
    )
    return HeartbeatService(
        device_id=settings.device_id,
        version_provider=version_provider(settings, updater),
        interval_seconds=settings.heartbeat_interval_seconds,
        collector=collector,
        sink=sink,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="edge-agent", description=__doc__)
    parser.add_argument("--once", action="store_true", help="run one cycle and exit")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="print heartbeats to stdout instead of sending them (same as EDGE_AGENT_DRY_RUN=1)",
    )
    args = parser.parse_args(argv)
    if args.dry_run:
        os.environ["EDGE_AGENT_DRY_RUN"] = "true"

    try:
        settings = AgentSettings()
    except ValidationError as exc:
        print(f"Invalid agent configuration:\n{exc}", file=sys.stderr)
        return 2

    configure_logging(settings.log_level)

    with ExitStack() as stack:
        sink: HeartbeatSink
        worker: DeploymentWorker | None = None
        updater: Updater | None = None

        if settings.dry_run:
            sink = StdoutHeartbeatSink()
        else:
            assert settings.backend_url is not None and settings.device_token is not None
            client = BackendClient(
                settings.backend_url, settings.device_token, settings.request_timeout_seconds
            )
            stack.callback(client.close)
            sink = HttpHeartbeatSink(client)
            if settings.updates_enabled:
                updater = build_updater(settings, stack)
                worker = DeploymentWorker(
                    device_id=settings.device_id,
                    client=client,
                    updater=updater,
                    poll_interval_seconds=settings.deployment_poll_interval_seconds,
                )

        heartbeats = build_heartbeat_service(settings, sink, updater)

        if args.once:
            if worker is not None:
                worker.poll_once()
            return 0 if heartbeats.beat() else 1

        stop = threading.Event()

        def request_stop(signum: int, _frame: FrameType | None) -> None:
            logger.info("shutdown requested", extra={"signal": signal.Signals(signum).name})
            stop.set()

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)

        # Updates run on their own thread so a slow image pull never delays
        # heartbeats and makes a healthy device look offline.
        worker_thread: threading.Thread | None = None
        if worker is not None:
            worker_thread = threading.Thread(
                target=worker.run, args=(stop,), name="deployment-worker"
            )
            worker_thread.start()
        try:
            heartbeats.run(stop)
        finally:
            stop.set()
            if worker_thread is not None:
                # Not a daemon thread: an update in flight is allowed to finish
                # rather than being killed halfway through a container switch.
                worker_thread.join()
        return 0
