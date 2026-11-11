"""Engine construction and shared column types."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import DateTime, Dialect, Engine, create_engine, event
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import StaticPool
from sqlalchemy.types import TypeDecorator


class Base(DeclarativeBase):
    pass


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on every backend.

    PostgreSQL keeps the offset; SQLite silently drops it. Normalising to UTC
    on the way in and re-attaching UTC on the way out makes both behave the
    same, and naive datetimes are rejected rather than guessed at.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError("naive datetime is not allowed; use an aware UTC datetime")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


def create_db_engine(database_url: str) -> Engine:
    kwargs: dict[str, Any] = {}
    if database_url.startswith("sqlite"):
        # FastAPI runs sync endpoints in a thread pool.
        kwargs["connect_args"] = {"check_same_thread": False}
        if database_url in {"sqlite://", "sqlite:///:memory:"}:
            # An in-memory database exists per connection; share one.
            kwargs["poolclass"] = StaticPool
    else:
        kwargs["pool_pre_ping"] = True

    engine = create_engine(database_url, **kwargs)

    if engine.dialect.name == "sqlite":

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

    return engine
