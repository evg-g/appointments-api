"""Telemetry ingestion — the one service both the HTTP batch endpoint and the MQTT worker funnel
into, so a single test suite covers both paths (spec §5).

It enforces the three ingestion guarantees from spec §4:

* **Idempotent** (rule 9): a reading is keyed by ``(device_id, sequence)``. Duplicates — within the
  same batch, across retried batches, or racing a concurrent batch — are dropped silently. The
  database's unique constraint is the real guarantee (``INSERT ... ON CONFLICT DO NOTHING``); the
  in-memory pre-check just lets us report each item's fate.
* **Order-independent** (rule 9): batches may arrive out of order or backfill hours late. Nothing
  here assumes arrival order — after every batch the excursion engine re-derives the whole series
  from ``measured_at``, so a late batch that fills a gap is still evaluated for excursions.
* **Clock-skew aware** (rule 10): a reading whose ``measured_at`` sits further than the allowed skew
  from the server clock is *flagged* (stored, but marked); a reading from the future (beyond a small
  tolerance) is *rejected* — never stored, never trusted.

The service is pure orchestration over ``typing.Protocol`` seams (`Devices`, `TelemetryReadings`,
`Policies`, `Excursions`, `TelemetryPublisher`) and an injected `Clock`, so unit tests substitute
in-memory fakes with no database (see ``tests/unit/test_ingestion.py``).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal, Protocol

from appointments_api.enums import DeviceStatus
from appointments_api.services.clock import Clock
from appointments_api.services.telemetry.errors import DeviceInactiveError, DeviceNotFoundError
from appointments_api.services.telemetry.excursion import (
    DerivedExcursion,
    ThresholdBand,
    detect_excursions,
)

ItemStatus = Literal["accepted", "duplicate", "rejected"]


@dataclass(frozen=True, slots=True)
class ReadingInput:
    """One inbound reading, framework-free. The API schema / MQTT payload maps onto this."""

    sequence: int
    measured_at: datetime
    temperature_c: float
    humidity_pct: float | None = None
    battery_pct: float | None = None


@dataclass(frozen=True, slots=True)
class NewReading:
    """A reading the service has decided to persist (skew already evaluated)."""

    sequence: int
    measured_at: datetime
    received_at: datetime
    temperature_c: float
    humidity_pct: float | None
    battery_pct: float | None
    clock_skew_flagged: bool


@dataclass(slots=True)
class BatchItemResult:
    """The fate of one item in the batch — the per-item result array (spec §5)."""

    sequence: int
    status: ItemStatus
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class IngestionOutcome:
    """Summary the caller turns into an HTTP body or an MQTT ack/log line."""

    results: list[BatchItemResult]
    accepted: int
    duplicates: int
    rejected: int
    open_excursions: int


@dataclass(frozen=True, slots=True)
class DeviceView:
    """The slice of a device the ingestion service needs — no ORM leak into the service layer."""

    id: uuid.UUID
    clinic_id: uuid.UUID
    status: DeviceStatus


@dataclass(frozen=True, slots=True)
class SkewPolicy:
    """How far the device clock may drift before a reading is flagged, and how far into the future a
    reading may sit before it is rejected outright."""

    max_skew_seconds: float = 300.0
    future_tolerance_seconds: float = 60.0


class Devices(Protocol):
    async def get(self, device_id: uuid.UUID) -> DeviceView | None: ...
    async def touch_last_seen(self, device_id: uuid.UUID, at: datetime) -> None: ...


class TelemetryReadings(Protocol):
    async def existing_sequences(self, device_id: uuid.UUID, sequences: list[int]) -> set[int]: ...
    async def insert_ignoring_duplicates(
        self, device_id: uuid.UUID, rows: list[NewReading]
    ) -> set[int]:
        """Persist rows, skipping any whose ``(device_id, sequence)`` already exists.

        Returns the set of sequences actually inserted, so a race lost to a concurrent batch is
        reported back as a duplicate rather than a spurious success.
        """
        ...

    async def series_for_device(self, device_id: uuid.UUID) -> list[tuple[datetime, float]]: ...


class Policies(Protocol):
    async def band_for_device(
        self, device_id: uuid.UUID, clinic_id: uuid.UUID
    ) -> ThresholdBand | None: ...


class Excursions(Protocol):
    async def replace_for_device(
        self, device_id: uuid.UUID, derived: list[DerivedExcursion]
    ) -> None:
        """Reconcile stored excursions for a device with a freshly derived list, keyed by
        ``started_at``, preserving acknowledgements on breaches that persist."""
        ...


class TelemetryPublisher(Protocol):
    async def publish(self, event: dict[str, object]) -> None:
        """Fan a just-accepted reading out to the live SSE stream. Best-effort."""
        ...


@dataclass(slots=True)
class _NullPublisher:
    """A publisher that drops everything — the default when no live stream is wired in."""

    async def publish(self, event: dict[str, object]) -> None:
        return None


class TelemetryIngestionService:
    def __init__(
        self,
        *,
        devices: Devices,
        readings: TelemetryReadings,
        policies: Policies,
        excursions: Excursions,
        clock: Clock,
        skew: SkewPolicy | None = None,
        publisher: TelemetryPublisher | None = None,
    ) -> None:
        self._devices = devices
        self._readings = readings
        self._policies = policies
        self._excursions = excursions
        self._clock = clock
        self._skew = skew or SkewPolicy()
        self._publisher = publisher or _NullPublisher()

    async def ingest(self, device_id: uuid.UUID, items: list[ReadingInput]) -> IngestionOutcome:
        device = await self._devices.get(device_id)
        if device is None:
            raise DeviceNotFoundError(device_id)
        if device.status in (DeviceStatus.DISABLED, DeviceStatus.RETIRED):
            raise DeviceInactiveError(device_id)

        now = self._clock.now()
        existing = await self._readings.existing_sequences(
            device_id, [item.sequence for item in items]
        )

        results: list[BatchItemResult] = []
        to_insert: list[NewReading] = []
        by_sequence: dict[int, BatchItemResult] = {}
        seen_in_batch: set[int] = set()
        future_cutoff = now + timedelta(seconds=self._skew.future_tolerance_seconds)

        for item in items:
            result = BatchItemResult(item.sequence, "accepted")
            results.append(result)
            # A sequence already stored, or repeated earlier in this same batch, is a duplicate.
            if item.sequence in existing or item.sequence in seen_in_batch:
                result.status = "duplicate"
                continue
            seen_in_batch.add(item.sequence)
            if item.measured_at > future_cutoff:
                result.status = "rejected"
                result.detail = "measured_at is in the future"
                continue
            skew_seconds = abs((now - item.measured_at).total_seconds())
            flagged = skew_seconds > self._skew.max_skew_seconds
            if flagged:
                result.detail = "clock skew exceeds the allowed threshold; reading flagged"
            to_insert.append(
                NewReading(
                    sequence=item.sequence,
                    measured_at=item.measured_at,
                    received_at=now,
                    temperature_c=item.temperature_c,
                    humidity_pct=item.humidity_pct,
                    battery_pct=item.battery_pct,
                    clock_skew_flagged=flagged,
                )
            )
            by_sequence[item.sequence] = result

        inserted_seqs: set[int] = set()
        if to_insert:
            inserted_seqs = await self._readings.insert_ignoring_duplicates(device_id, to_insert)
            # Lost a race to a concurrent batch: the row already existed → report it as a duplicate.
            for seq, result in by_sequence.items():
                if seq not in inserted_seqs:
                    result.status = "duplicate"
                    result.detail = None

        open_excursions = await self._reevaluate_and_publish(device, to_insert, inserted_seqs, now)

        accepted = sum(1 for r in results if r.status == "accepted")
        duplicates = sum(1 for r in results if r.status == "duplicate")
        rejected = sum(1 for r in results if r.status == "rejected")
        return IngestionOutcome(
            results=results,
            accepted=accepted,
            duplicates=duplicates,
            rejected=rejected,
            open_excursions=open_excursions,
        )

    async def _reevaluate_and_publish(
        self,
        device: DeviceView,
        candidates: list[NewReading],
        inserted_seqs: set[int],
        now: datetime,
    ) -> int:
        """After a batch lands, refresh last-seen, re-derive excursions over the whole series, and
        fan the newly-stored readings out to the live stream. Returns the open-excursion count."""
        newly_stored = [nr for nr in candidates if nr.sequence in inserted_seqs]
        if not newly_stored:
            # Nothing new (all duplicates/rejected): report current open excursions, do no writes.
            return await self._open_excursion_count(device)

        await self._devices.touch_last_seen(device.id, now)

        open_excursions = 0
        band = await self._policies.band_for_device(device.id, device.clinic_id)
        if band is not None:
            series = await self._readings.series_for_device(device.id)
            derived = detect_excursions(series, band)
            await self._excursions.replace_for_device(device.id, derived)
            open_excursions = sum(1 for e in derived if e.ended_at is None)

        for nr in newly_stored:
            await self._publisher.publish(
                {
                    "device_id": str(device.id),
                    "clinic_id": str(device.clinic_id),
                    "sequence": nr.sequence,
                    "measured_at": nr.measured_at.isoformat(),
                    "received_at": nr.received_at.isoformat(),
                    "temperature_c": nr.temperature_c,
                    "humidity_pct": nr.humidity_pct,
                    "battery_pct": nr.battery_pct,
                    "clock_skew_flagged": nr.clock_skew_flagged,
                }
            )
        return open_excursions

    async def _open_excursion_count(self, device: DeviceView) -> int:
        band = await self._policies.band_for_device(device.id, device.clinic_id)
        if band is None:
            return 0
        series = await self._readings.series_for_device(device.id)
        return sum(1 for e in detect_excursions(series, band) if e.ended_at is None)


__all__ = [
    "BatchItemResult",
    "DeviceView",
    "Devices",
    "Excursions",
    "IngestionOutcome",
    "NewReading",
    "Policies",
    "ReadingInput",
    "SkewPolicy",
    "TelemetryIngestionService",
    "TelemetryPublisher",
    "TelemetryReadings",
]
