"""Structured JSON logging: one JSON object per line on stderr.

stdout is reserved for dry-run heartbeat output so the two streams can be
piped separately.
"""

from __future__ import annotations

import json
import logging
import sys
from datetime import UTC, datetime
from typing import Any

_STANDARD_ATTRS = frozenset(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Anything passed via ``extra=`` becomes a top-level field.
        payload.update(
            {key: value for key, value in record.__dict__.items() if key not in _STANDARD_ATTRS}
        )
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers[:] = [handler]
    root.setLevel(level.upper())
    # httpx logs every request at INFO, which would drown out the agent's own logs.
    logging.getLogger("httpx").setLevel(logging.WARNING)
