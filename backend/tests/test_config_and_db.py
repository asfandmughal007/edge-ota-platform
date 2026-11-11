from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError
from sqlalchemy.exc import StatementError
from sqlalchemy.orm import Session

from device_mgmt.config import Settings
from device_mgmt.db.models import Device
from device_mgmt.db.session import Base, create_db_engine


def test_offline_threshold_must_exceed_stale_threshold() -> None:
    with pytest.raises(ValidationError, match="must exceed"):
        Settings(heartbeat_stale_after_seconds=300, heartbeat_offline_after_seconds=90)


def test_settings_read_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DEVICE_MGMT_DATABASE_URL", "postgresql+psycopg://u:p@db/x")
    assert Settings().database_url == "postgresql+psycopg://u:p@db/x"


def test_datetimes_round_trip_as_aware_utc_on_file_sqlite(tmp_path: Path) -> None:
    engine = create_db_engine(f"sqlite:///{tmp_path / 'test.db'}")
    Base.metadata.create_all(engine)
    plus_two = timezone(timedelta(hours=2))

    with Session(engine) as session:
        session.add(
            Device(device_id="d1", registered_at=datetime(2026, 1, 1, 14, 0, tzinfo=plus_two))
        )
        session.commit()
    with Session(engine) as session:
        stored = session.get(Device, "d1")
        assert stored is not None
        assert stored.registered_at == datetime(2026, 1, 1, 12, 0, tzinfo=UTC)
        assert stored.registered_at.tzinfo is UTC
    engine.dispose()


def test_naive_datetimes_are_rejected() -> None:
    engine = create_db_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add(Device(device_id="d1", registered_at=datetime(2026, 1, 1, 12, 0)))
        with pytest.raises(StatementError, match="naive datetime"):
            session.commit()
    engine.dispose()
