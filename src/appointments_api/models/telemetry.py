"""TelemetryReading model — one temperature/humidity sample from a device.

Two timestamps, deliberately kept apart:

* ``measured_at`` — the device clock at the moment of the reading. This is what the excursion engine
  reasons over, because it is the physical timeline of the fridge.
* ``received_at`` — the server clock when the reading was ingested. The gap between the two is the
  clock skew; a reading whose skew is too large is *flagged* (``clock_skew_flagged``), and one from
  the future is rejected outright before it ever reaches this table (see the ingestion service).

Idempotency and ordering (spec §4 rule 9): a device numbers its readings with a monotonic
``sequence``. The ``(device_id, sequence)`` unique constraint is what makes ingestion idempotent —
a retried or duplicated batch collides and is dropped silently. Ordering is *not* assumed: batches
may arrive out of order or backfill hours late, so queries and the excursion engine always sort by
``measured_at`` rather than by insertion order. That column carries a **BRIN** index: telemetry is
append-heavy and naturally clustered in time, so a block-range index gives time-window scans at a
fraction of the size of a b-tree (spec §5, "no TimescaleDB dependency").
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, Boolean, DateTime, Float, ForeignKey, Index, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from appointments_api.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class TelemetryReading(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "telemetry_readings"
    __table_args__ = (
        # Idempotency key: a device's sequence numbers are unique, so a duplicate batch collides
        # here and is dropped instead of double-counted.
        UniqueConstraint("device_id", "sequence", name="uq_reading_device_sequence"),
        # BRIN over the physical timeline: cheap time-window scans for the downsampling queries.
        Index("ix_telemetry_measured_at_brin", "measured_at", postgresql_using="brin"),
        # A plain b-tree on (device_id, measured_at) serves the per-device series read that the
        # excursion engine and time-series endpoint run most often.
        Index("ix_telemetry_device_measured_at", "device_id", "measured_at"),
    )

    device_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=False
    )
    # Device clock (UTC). The excursion timeline is measured over this.
    measured_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Server clock (UTC) at ingestion.
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    temperature_c: Mapped[float] = mapped_column(Float, nullable=False)
    humidity_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    battery_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Device-assigned monotonic sequence number. BigInteger: a device runs for years.
    sequence: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # True when |measured_at - received_at| exceeded the allowed skew at ingestion.
    clock_skew_flagged: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
