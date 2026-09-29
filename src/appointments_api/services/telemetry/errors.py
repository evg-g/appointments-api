"""Domain errors for telemetry ingestion — no HTTP knowledge.

The HTTP layer maps these to problem+json; the MQTT worker logs and drops the message. Keeping them
framework-free means the ingestion service can be unit-tested without FastAPI.
"""

from __future__ import annotations

import uuid


class TelemetryError(Exception):
    """Base class for telemetry ingestion failures."""


class DeviceNotFoundError(TelemetryError):
    """Telemetry arrived for a device id that does not exist (maps to 404)."""

    def __init__(self, device_id: uuid.UUID) -> None:
        self.device_id = device_id
        super().__init__(f"No device with id {device_id}.")


class DeviceInactiveError(TelemetryError):
    """Telemetry arrived for a disabled or retired device (maps to 409)."""

    def __init__(self, device_id: uuid.UUID) -> None:
        self.device_id = device_id
        super().__init__(f"Device {device_id} is not accepting telemetry.")
