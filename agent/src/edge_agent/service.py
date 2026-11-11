"""The heartbeat loop."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from datetime import UTC, datetime

from edge_agent.collector import SystemCollector
from edge_agent.models import Heartbeat
from edge_agent.sinks import HeartbeatSink, PublishError

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(UTC)


class HeartbeatService:
    def __init__(
        self,
        device_id: str,
        version_provider: Callable[[], str],
        interval_seconds: float,
        collector: SystemCollector,
        sink: HeartbeatSink,
        clock: Callable[[], datetime] = _utcnow,
    ) -> None:
        self._device_id = device_id
        self._version_provider = version_provider
        self._interval = interval_seconds
        self._collector = collector
        self._sink = sink
        self._clock = clock

    def build_heartbeat(self) -> Heartbeat:
        metrics = self._collector.collect()
        return Heartbeat(
            device_id=self._device_id,
            hostname=metrics.hostname,
            software_version=self._version_provider(),
            cpu_percent=metrics.cpu_percent,
            memory_percent=metrics.memory_percent,
            disk_percent=metrics.disk_percent,
            uptime_seconds=metrics.uptime_seconds,
            network=metrics.network,
            timestamp=self._clock(),
        )

    def beat(self) -> bool:
        """Collect and send one heartbeat. Returns False on failure, never raises.

        A missed heartbeat is not retried: the next tick carries fresher data,
        and the backend detects gaps from timestamps.
        """
        try:
            heartbeat = self.build_heartbeat()
            self._sink.send(heartbeat)
            version = heartbeat.software_version
        except PublishError as exc:
            logger.warning(
                "heartbeat delivery failed",
                extra={"device_id": self._device_id, "error": str(exc)},
            )
            return False
        except Exception:
            # The agent is a long-running daemon; one bad tick must not kill it.
            logger.exception("heartbeat tick failed", extra={"device_id": self._device_id})
            return False
        logger.info(
            "heartbeat sent",
            extra={"device_id": self._device_id, "software_version": version},
        )
        return True

    def run(self, stop: threading.Event) -> None:
        """Send heartbeats every interval until ``stop`` is set."""
        logger.info(
            "agent started",
            extra={"device_id": self._device_id, "interval_seconds": self._interval},
        )
        while not stop.is_set():
            self.beat()
            stop.wait(self._interval)
        logger.info("agent stopped", extra={"device_id": self._device_id})
