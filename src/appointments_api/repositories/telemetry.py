"""TelemetryReading repository.

This one class serves two callers:

* the ingestion service, through the ``existing_sequences`` / ``insert_ignoring_duplicates`` /
  ``series_for_device`` methods (it structurally satisfies the ingestion ``TelemetryReadings``
  protocol);
* the time-series endpoint, through ``bucketed`` (server-side downsampling with ``date_bin``) and
  ``latest_for_device`` (device health).

Idempotent insertion is done with Postgres ``INSERT ... ON CONFLICT (device_id, sequence) DO NOTHING
RETURNING sequence``: the database drops duplicates atomically, even against a concurrent batch, and
tells us exactly which rows were new.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import Row, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from appointments_api.models import TelemetryReading
from appointments_api.services.telemetry.ingestion import NewReading

Aggregate = Literal["avg", "min", "max"]

# Fixed origin so bucket boundaries are stable regardless of the query window.
_BUCKET_ORIGIN = datetime(1970, 1, 1, tzinfo=UTC)


class TelemetryReadingRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def existing_sequences(self, device_id: uuid.UUID, sequences: list[int]) -> set[int]:
        if not sequences:
            return set()
        stmt = select(TelemetryReading.sequence).where(
            TelemetryReading.device_id == device_id,
            TelemetryReading.sequence.in_(sequences),
        )
        return set((await self._s.execute(stmt)).scalars().all())

    async def insert_ignoring_duplicates(
        self, device_id: uuid.UUID, rows: list[NewReading]
    ) -> set[int]:
        if not rows:
            return set()
        values = [
            {
                "id": uuid.uuid4(),
                "device_id": device_id,
                "measured_at": r.measured_at,
                "received_at": r.received_at,
                "temperature_c": r.temperature_c,
                "humidity_pct": r.humidity_pct,
                "battery_pct": r.battery_pct,
                "sequence": r.sequence,
                "clock_skew_flagged": r.clock_skew_flagged,
            }
            for r in rows
        ]
        stmt = (
            pg_insert(TelemetryReading)
            .values(values)
            .on_conflict_do_nothing(index_elements=["device_id", "sequence"])
            .returning(TelemetryReading.sequence)
        )
        return set((await self._s.execute(stmt)).scalars().all())

    async def series_for_device(self, device_id: uuid.UUID) -> list[tuple[datetime, float]]:
        stmt = (
            select(TelemetryReading.measured_at, TelemetryReading.temperature_c)
            .where(TelemetryReading.device_id == device_id)
            .order_by(TelemetryReading.measured_at, TelemetryReading.id)
        )
        rows = (await self._s.execute(stmt)).all()
        return [(row[0], row[1]) for row in rows]

    async def latest_for_device(self, device_id: uuid.UUID) -> TelemetryReading | None:
        stmt = (
            select(TelemetryReading)
            .where(TelemetryReading.device_id == device_id)
            .order_by(TelemetryReading.measured_at.desc(), TelemetryReading.id.desc())
            .limit(1)
        )
        return (await self._s.execute(stmt)).scalars().first()

    async def bucketed(
        self,
        device_id: uuid.UUID,
        *,
        bucket_seconds: int,
        agg: Aggregate,
        start: datetime | None = None,
        end: datetime | None = None,
    ) -> list[Row[tuple[datetime, float, int]]]:
        """Downsample a device's series into fixed-width time buckets, server-side.

        Uses Postgres ``date_bin`` against a fixed origin so buckets align the same way for every
        query. Returns ``(bucket_start, value, sample_count)`` ordered oldest-first.
        """
        stride = func.make_interval(0, 0, 0, 0, 0, 0, float(bucket_seconds))
        bucket = func.date_bin(stride, TelemetryReading.measured_at, _BUCKET_ORIGIN).label("bucket")
        column = TelemetryReading.temperature_c
        if agg == "avg":
            value = func.avg(column)
        elif agg == "min":
            value = func.min(column)
        else:
            value = func.max(column)
        stmt = (
            select(
                bucket,
                value.label("value"),
                func.count().label("sample_count"),
            )
            .where(TelemetryReading.device_id == device_id)
            .group_by(bucket)
            .order_by(bucket)
        )
        if start is not None:
            stmt = stmt.where(TelemetryReading.measured_at >= start)
        if end is not None:
            stmt = stmt.where(TelemetryReading.measured_at < end)
        return list((await self._s.execute(stmt)).all())
