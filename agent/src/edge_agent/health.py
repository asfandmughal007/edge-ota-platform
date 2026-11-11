"""Application health checking after an update."""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

import httpx


@dataclass(frozen=True, slots=True)
class HealthResult:
    healthy: bool
    detail: str


class HealthChecker(Protocol):
    def wait_until_healthy(self, url: str, expected_version: str) -> HealthResult: ...


class HttpHealthChecker:
    """Polls a health URL until it reports healthy or the deadline passes.

    Healthy means HTTP 200 and, if the body carries a ``version`` field, that
    it equals the version just deployed. The version check stops a leftover
    old container on the same port from passing for the new one.
    """

    def __init__(
        self,
        timeout_seconds: float = 30.0,
        interval_seconds: float = 2.0,
        request_timeout_seconds: float = 3.0,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout = timeout_seconds
        self._interval = interval_seconds
        self._client = httpx.Client(timeout=request_timeout_seconds, transport=transport)
        self._sleep = sleep
        self._monotonic = monotonic

    def check_once(self, url: str, expected_version: str) -> HealthResult:
        try:
            response = self._client.get(url)
        except httpx.HTTPError as exc:
            return HealthResult(False, f"{type(exc).__name__}: {exc}")
        if response.status_code != httpx.codes.OK:
            return HealthResult(False, f"HTTP {response.status_code}")
        try:
            body = response.json()
        except ValueError:
            body = None
        reported = body.get("version") if isinstance(body, dict) else None
        if reported is not None and reported != expected_version:
            return HealthResult(False, f"reports version {reported}, expected {expected_version}")
        return HealthResult(True, "HTTP 200")

    def wait_until_healthy(self, url: str, expected_version: str) -> HealthResult:
        deadline = self._monotonic() + self._timeout
        attempts = 0
        while True:
            attempts += 1
            result = self.check_once(url, expected_version)
            if result.healthy:
                return result
            if self._monotonic() + self._interval > deadline:
                return HealthResult(
                    False,
                    f"not healthy after {self._timeout:g}s ({attempts} attempts); "
                    f"last result: {result.detail}",
                )
            self._sleep(self._interval)

    def close(self) -> None:
        self._client.close()
