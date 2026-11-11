"""Domain errors. The API layer maps these to HTTP responses; services never
import FastAPI."""

from __future__ import annotations


class DomainError(Exception):
    """Base class for expected, client-facing failures."""


class DeviceNotFoundError(DomainError):
    def __init__(self, device_id: str) -> None:
        super().__init__(f"device '{device_id}' is not registered")
        self.device_id = device_id


class DeviceAlreadyExistsError(DomainError):
    def __init__(self, device_id: str) -> None:
        super().__init__(f"device '{device_id}' is already registered")
        self.device_id = device_id


class DeviceIdMismatchError(DomainError):
    def __init__(self, path_id: str, body_id: str) -> None:
        super().__init__(f"heartbeat for '{body_id}' was posted to device '{path_id}'")
