"""Excursion model — a server-recorded cold-chain breach.

The server does not trust the device to tell it when an excursion happened; it *re-derives*
excursions from the stored telemetry series using the same dwell/recovery rule the device applies
locally (spec §4 rule 11). Each breach is identified by ``(device_id, started_at)`` — the first
out-of-band reading — so re-running the engine over a grown series (a late backfill arrived) updates
the same row rather than creating a duplicate. ``ended_at`` is null while the breach is still open.
Acknowledgement is an operator action: ``acknowledged_by`` / ``acknowledged_at`` record who signed
off on the breach and when.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, Float, ForeignKey, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from appointments_api.enums import ExcursionDirection
from appointments_api.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Excursion(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "excursions"
    __table_args__ = (
        # One breach per (device, start instant): re-derivation upserts rather than duplicating.
        UniqueConstraint("device_id", "started_at", name="uq_excursion_device_started"),
    )

    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, index=True
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Null while the breach is still open.
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # This enum's member names (HIGH/LOW) differ from its values (high/low), and the Postgres type
    # was created with the lowercase *values* (the device's wire format). So the column stores the
    # values, not the names — hence values_callable.
    direction: Mapped[ExcursionDirection] = mapped_column(
        Enum(
            ExcursionDirection,
            name="excursion_direction",
            values_callable=lambda enum: [member.value for member in enum],
        ),
        nullable=False,
    )
    # The most extreme temperature reached during the breach (highest for HIGH, lowest for LOW).
    peak_temperature_c: Mapped[float] = mapped_column(Float, nullable=False)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
