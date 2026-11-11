from __future__ import annotations

import httpx

from edge_agent.health import HttpHealthChecker

URL = "http://127.0.0.1:8080/health"


class FakeTime:
    """Virtual clock: sleeping advances time instantly."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def make_checker(responses: list[httpx.Response | Exception], time: FakeTime) -> HttpHealthChecker:
    remaining = list(responses)

    def handler(request: httpx.Request) -> httpx.Response:
        # The last response repeats forever.
        item = remaining.pop(0) if len(remaining) > 1 else remaining[0]
        if isinstance(item, Exception):
            raise item
        return item

    return HttpHealthChecker(
        timeout_seconds=10,
        interval_seconds=2,
        transport=httpx.MockTransport(handler),
        sleep=time.sleep,
        monotonic=time.monotonic,
    )


def ok(version: str = "1.3.0") -> httpx.Response:
    return httpx.Response(200, json={"status": "ok", "version": version})


def test_healthy_on_first_attempt_does_not_sleep() -> None:
    time = FakeTime()
    result = make_checker([ok()], time).wait_until_healthy(URL, "1.3.0")

    assert result.healthy
    assert time.sleeps == []


def test_waits_for_a_slow_starting_app() -> None:
    time = FakeTime()
    not_listening = httpx.ConnectError("connection refused")
    checker = make_checker([not_listening, httpx.Response(503), ok()], time)

    result = checker.wait_until_healthy(URL, "1.3.0")

    assert result.healthy
    assert time.sleeps == [2, 2]


def test_gives_up_at_the_deadline_and_reports_the_last_failure() -> None:
    time = FakeTime()
    result = make_checker([httpx.Response(500)], time).wait_until_healthy(URL, "1.3.0")

    assert not result.healthy
    assert "not healthy after 10s" in result.detail
    assert "HTTP 500" in result.detail
    assert time.now <= 10


def test_old_version_answering_on_the_port_is_not_healthy() -> None:
    time = FakeTime()
    result = make_checker([ok("1.2.0")], time).wait_until_healthy(URL, "1.3.0")

    assert not result.healthy
    assert "reports version 1.2.0, expected 1.3.0" in result.detail


def test_200_without_a_version_field_is_healthy() -> None:
    time = FakeTime()
    checker = make_checker([httpx.Response(200, text="OK")], time)

    assert checker.wait_until_healthy(URL, "1.3.0").healthy
