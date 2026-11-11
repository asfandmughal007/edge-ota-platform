"""How long a device has been silent, and what that means.

Status is derived from the server's own receive time. It is computed on demand
(for API reads) and by the background monitor (to detect transitions), always
through these functions so the two can never disagree.
"""

from __future__ import annotations

from datetime import datetime

from device_mgmt.config import Settings
from device_mgmt.schemas import DeviceStatus


def silence_seconds(last_seen_at: datetime | None, now: datetime) -> float | None:
    if last_seen_at is None:
        return None
    return max(0.0, (now - last_seen_at).total_seconds())


def compute_status(
    last_seen_at: datetime | None, now: datetime, settings: Settings
) -> DeviceStatus:
    silence = silence_seconds(last_seen_at, now)
    if silence is None:
        return DeviceStatus.NEVER_SEEN
    if silence < settings.heartbeat_stale_after_seconds:
        return DeviceStatus.ONLINE
    if silence < settings.heartbeat_offline_after_seconds:
        return DeviceStatus.STALE
    return DeviceStatus.OFFLINE
