from __future__ import annotations

import json
import threading
from datetime import UTC, datetime

import pytest

from edge_agent.collector import SystemCollector
from edge_agent.models import Heartbeat
from edge_agent.service import HeartbeatService
from edge_agent.sinks import PublishError

FIXED_NOW = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)

REQUIRED_FIELDS = {
    "device_id",
    "hostname",
    "software_version",
    "cpu_percent",
    "memory_percent",
    "disk_percent",
    "uptime_seconds",
    "network",
    "timestamp",
}


class RecordingSink:
    def __init__(self, fail_first: int = 0, stop_after: int | None = None) -> None:
        self.sent: list[Heartbeat] = []
        self.attempts = 0
        self.stop = threading.Event()
        self._fail_first = fail_first
        self._stop_after = stop_after

    def send(self, heartbeat: Heartbeat) -> None:
        self.attempts += 1
        if self._stop_after is not None and self.attempts >= self._stop_after:
            self.stop.set()
        if self.attempts <= self._fail_first:
            raise PublishError("backend unavailable")
        self.sent.append(heartbeat)

    def close(self) -> None:
        return None


def make_service(sink: RecordingSink, interval: float = 0.001) -> HeartbeatService:
    return HeartbeatService(
        device_id="device-001",
        software_version="1.2.0",
        interval_seconds=interval,
        collector=SystemCollector(),
        sink=sink,
        clock=lambda: FIXED_NOW,
    )


def test_heartbeat_is_structured_json_with_all_required_fields() -> None:
    sink = RecordingSink()
    assert make_service(sink).beat() is True

    payload = json.loads(sink.sent[0].model_dump_json())

    assert payload.keys() >= REQUIRED_FIELDS
    assert payload["device_id"] == "device-001"
    assert payload["software_version"] == "1.2.0"
    assert payload["timestamp"] == "2026-01-02T03:04:05Z"
    assert set(payload["network"]) == {"interfaces_up", "ip_address", "backend_reachable"}


def test_delivery_failure_is_reported_not_raised() -> None:
    sink = RecordingSink(fail_first=1)
    service = make_service(sink)
    assert service.beat() is False
    assert service.beat() is True
    assert len(sink.sent) == 1


def test_collector_failure_does_not_crash_the_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    sink = RecordingSink()
    service = make_service(sink)

    def boom() -> None:
        raise RuntimeError("sensor read failed")

    monkeypatch.setattr(service._collector, "collect", boom)
    assert service.beat() is False
    assert sink.attempts == 0


def test_run_keeps_beating_through_failures_until_stopped() -> None:
    sink = RecordingSink(fail_first=2, stop_after=5)
    service = make_service(sink)

    worker = threading.Thread(target=service.run, args=(sink.stop,))
    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert sink.attempts == 5
    assert len(sink.sent) == 3


def test_stop_interrupts_a_long_interval_promptly() -> None:
    sink = RecordingSink(stop_after=1)
    service = make_service(sink, interval=3600)

    worker = threading.Thread(target=service.run, args=(sink.stop,))
    worker.start()
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert sink.attempts == 1
