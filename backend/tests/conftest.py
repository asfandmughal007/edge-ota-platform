from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from device_mgmt.config import Settings
from device_mgmt.main import create_app


class FakeClock:
    def __init__(self) -> None:
        self.now = datetime(2026, 1, 1, 12, 0, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += timedelta(seconds=seconds)


@pytest.fixture
def clock() -> FakeClock:
    return FakeClock()


@pytest.fixture
def client(clock: FakeClock) -> Iterator[TestClient]:
    settings = Settings(
        database_url="sqlite://",
        heartbeat_stale_after_seconds=90,
        heartbeat_offline_after_seconds=300,
    )
    with TestClient(create_app(settings, clock=clock)) as test_client:
        yield test_client
