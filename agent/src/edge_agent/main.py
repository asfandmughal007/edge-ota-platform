"""Entry point: ``edge-agent`` or ``python -m edge_agent``."""

from __future__ import annotations

import argparse
import logging
import os
import signal
import sys
import threading
from collections.abc import Sequence
from types import FrameType

from pydantic import ValidationError

from edge_agent.collector import SystemCollector
from edge_agent.config import AgentSettings
from edge_agent.logging_setup import configure_logging
from edge_agent.service import HeartbeatService
from edge_agent.sinks import HeartbeatSink, HttpHeartbeatSink, StdoutHeartbeatSink

logger = logging.getLogger(__name__)


def build_sink(settings: AgentSettings) -> HeartbeatSink:
    if settings.dry_run:
        return StdoutHeartbeatSink()
    assert settings.backend_url is not None and settings.device_token is not None
    return HttpHeartbeatSink(
        backend_url=settings.backend_url,
        device_token=settings.device_token,
        timeout=settings.request_timeout_seconds,
    )


def build_service(settings: AgentSettings, sink: HeartbeatSink) -> HeartbeatService:
    collector = SystemCollector(
        disk_path=settings.disk_path,
        connectivity_target=settings.connectivity_target,
        connectivity_timeout=settings.connectivity_timeout_seconds,
    )
    return HeartbeatService(
        device_id=settings.device_id,
        software_version=settings.software_version,
        interval_seconds=settings.heartbeat_interval_seconds,
        collector=collector,
        sink=sink,
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="edge-agent", description=__doc__)
    parser.add_argument("--once", action="store_true", help="send one heartbeat and exit")
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
    sink = build_sink(settings)
    service = build_service(settings, sink)

    try:
        if args.once:
            return 0 if service.beat() else 1

        stop = threading.Event()

        def request_stop(signum: int, _frame: FrameType | None) -> None:
            logger.info("shutdown requested", extra={"signal": signal.Signals(signum).name})
            stop.set()

        signal.signal(signal.SIGINT, request_stop)
        signal.signal(signal.SIGTERM, request_stop)
        service.run(stop)
        return 0
    finally:
        sink.close()
