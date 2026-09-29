"""Device model — a cold-chain temperature sensor node in a clinic.

A device belongs to one clinic and monitors one location (e.g. "Vaccine fridge A"). It authenticates
to the telemetry endpoints with a per-device secret, stored only as an Argon2 hash: the plaintext is
shown once at provisioning and once on each rotation, never again. ``last_seen_at`` is refreshed
whenever a reading from the device is accepted, so a stale ``last_seen_at`` is how the health view
spots a silent node.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from appointments_api.enums import DeviceStatus
from appointments_api.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Device(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "devices"

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    location_label: Mapped[str] = mapped_column(String(200), nullable=False)
    hardware_version: Mapped[str] = mapped_column(String(64), nullable=False)
    firmware_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[DeviceStatus] = mapped_column(
        Enum(DeviceStatus, name="device_status"),
        default=DeviceStatus.PROVISIONED,
        server_default=DeviceStatus.PROVISIONED.value,
        nullable=False,
    )
    # Argon2 hash of the device's telemetry credential. Write-only at the API edge — never returned.
    secret_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    # Refreshed on every accepted reading; null until the device first reports.
    last_seen_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
