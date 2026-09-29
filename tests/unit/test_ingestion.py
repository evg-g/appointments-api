"""Unit tests for the telemetry ingestion service — no database, no network.

These prove the three ingestion guarantees (spec §4 rules 9 and 10) against in-memory fakes:
idempotency (duplicates dropped), order-independence (a late backfill still raises the excursion),
and clock-skew handling (flag vs reject). The fakes substitute for the SQL adapters via the
service's ``typing.Protocol`` seams, which is the whole point of keeping the service pure.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta

import pytest

from appointments_api.enums import DeviceStatus, ExcursionDirection
from appointments_api.services.telemetry.errors import DeviceInactiveError, DeviceNotFoundError
from appointments_api.services.telemetry.excursion import DerivedExcursion, ThresholdBand
from appointments_api.services.telemetry.ingestion import (
    DeviceView,
    NewReading,
    ReadingInput,
    SkewPolicy,
    TelemetryIngestionService,
)
from tests.fakes.clock import FixedClock

# pytest is configured with asyncio_mode="auto", so async test functions need no marker.

_NOW = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
# A base well in the past for multi-hour excursion series, so no reading trips the future check.
_SERIES_BASE = _NOW - timedelta(hours=2)
_DEVICE_ID = uuid.uuid4()
_CLINIC_ID = uuid.uuid4()
_BAND = ThresholdBand(
    min_temperature_c=2.0, max_temperature_c=8.0, dwell_minutes=15.0, recovery_minutes=10.0
)


class FakeDevices:
    def __init__(self, view: DeviceView | None) -> None:
        self._view = view
        self.touched: list[tuple[uuid.UUID, datetime]] = []

    async def get(self, device_id: uuid.UUID) -> DeviceView | None:
        return self._view

    async def touch_last_seen(self, device_id: uuid.UUID, at: datetime) -> None:
        self.touched.append((device_id, at))


class FakeReadings:
    """In-memory reading store keyed by ``(device_id, sequence)``."""

    def __init__(self) -> None:
        self.rows: dict[tuple[uuid.UUID, int], NewReading] = {}

    async def existing_sequences(self, device_id: uuid.UUID, sequences: list[int]) -> set[int]:
        return {s for s in sequences if (device_id, s) in self.rows}

    async def insert_ignoring_duplicates(
        self, device_id: uuid.UUID, rows: list[NewReading]
    ) -> set[int]:
        inserted: set[int] = set()
        for r in rows:
            key = (device_id, r.sequence)
            if key in self.rows:
                continue
            self.rows[key] = r
            inserted.add(r.sequence)
        return inserted

    async def series_for_device(self, device_id: uuid.UUID) -> list[tuple[datetime, float]]:
        rows = [r for (d, _s), r in self.rows.items() if d == device_id]
        rows.sort(key=lambda r: (r.measured_at, r.sequence))
        return [(r.measured_at, r.temperature_c) for r in rows]


class FakePolicies:
    def __init__(self, band: ThresholdBand | None) -> None:
        self._band = band

    async def band_for_device(
        self, device_id: uuid.UUID, clinic_id: uuid.UUID
    ) -> ThresholdBand | None:
        return self._band


class FakeExcursions:
    def __init__(self) -> None:
        self.last: list[DerivedExcursion] = []
        self.calls = 0

    async def replace_for_device(
        self, device_id: uuid.UUID, derived: list[DerivedExcursion]
    ) -> None:
        self.calls += 1
        self.last = derived


class FakePublisher:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []

    async def publish(self, event: dict[str, object]) -> None:
        self.events.append(event)


def _service(
    *,
    status: DeviceStatus = DeviceStatus.ACTIVE,
    band: ThresholdBand | None = _BAND,
    device: bool = True,
    clock: FixedClock | None = None,
) -> tuple[TelemetryIngestionService, FakeReadings, FakeExcursions, FakePublisher, FakeDevices]:
    view = DeviceView(id=_DEVICE_ID, clinic_id=_CLINIC_ID, status=status) if device else None
    devices = FakeDevices(view)
    readings = FakeReadings()
    excursions = FakeExcursions()
    publisher = FakePublisher()
    service = TelemetryIngestionService(
        devices=devices,
        readings=readings,
        policies=FakePolicies(band),
        excursions=excursions,
        clock=clock or FixedClock(_NOW),
        skew=SkewPolicy(max_skew_seconds=300.0, future_tolerance_seconds=60.0),
        publisher=publisher,
    )
    return service, readings, excursions, publisher, devices


def _reading(seq: int, offset_s: int, temp: float) -> ReadingInput:
    return ReadingInput(
        sequence=seq, measured_at=_NOW + timedelta(seconds=offset_s), temperature_c=temp
    )


def _sreading(seq: int, offset_s: int, temp: float) -> ReadingInput:
    """A reading on the past series base — used for multi-hour excursion series."""
    return ReadingInput(
        sequence=seq, measured_at=_SERIES_BASE + timedelta(seconds=offset_s), temperature_c=temp
    )


async def test_accepts_new_readings_and_publishes() -> None:
    service, readings, _exc, publisher, devices = _service()
    items = [_reading(1, -600, 5.0), _reading(2, -300, 5.1)]

    outcome = await service.ingest(_DEVICE_ID, items)

    assert outcome.accepted == 2
    assert outcome.duplicates == 0
    assert outcome.rejected == 0
    assert len(readings.rows) == 2
    assert len(publisher.events) == 2
    assert devices.touched  # last_seen refreshed


async def test_duplicate_sequences_dropped_within_and_across_batches() -> None:
    service, readings, _exc, _pub, _dev = _service()
    await service.ingest(_DEVICE_ID, [_reading(1, -600, 5.0)])

    # A retried batch (seq 1 again) plus a repeat of seq 2 inside the same batch.
    outcome = await service.ingest(
        _DEVICE_ID, [_reading(1, -600, 5.0), _reading(2, -300, 5.0), _reading(2, -300, 5.0)]
    )

    statuses = {r.sequence: r.status for r in outcome.results}
    assert statuses[1] == "duplicate"
    # First occurrence of seq 2 accepted, second is a duplicate.
    seq2 = [r.status for r in outcome.results if r.sequence == 2]
    assert sorted(seq2) == ["accepted", "duplicate"]
    assert len(readings.rows) == 2  # only seq 1 and seq 2 stored once each


async def test_future_reading_rejected_not_stored() -> None:
    service, readings, _exc, _pub, _dev = _service()
    # 10 minutes into the future, well past the 60s tolerance.
    future = ReadingInput(sequence=1, measured_at=_NOW + timedelta(minutes=10), temperature_c=5.0)

    outcome = await service.ingest(_DEVICE_ID, [future])

    assert outcome.rejected == 1
    assert outcome.accepted == 0
    assert readings.rows == {}
    assert outcome.results[0].status == "rejected"
    assert "future" in (outcome.results[0].detail or "")


async def test_clock_skew_reading_flagged_but_stored() -> None:
    service, readings, _exc, _pub, _dev = _service()
    # 20 min in the past: beyond the 300s skew threshold, not in the future → flagged, still stored.
    skewed = ReadingInput(sequence=1, measured_at=_NOW - timedelta(minutes=20), temperature_c=5.0)

    outcome = await service.ingest(_DEVICE_ID, [skewed])

    assert outcome.accepted == 1
    assert len(readings.rows) == 1
    stored = next(iter(readings.rows.values()))
    assert stored.clock_skew_flagged is True
    assert "skew" in (outcome.results[0].detail or "")


async def test_late_backfill_still_raises_excursion() -> None:
    """Order-independence: a batch that fills an earlier gap is still evaluated for excursions."""
    service, _readings, excursions, _pub, _dev = _service()

    # First, deliver the tail of a sustained-high excursion and the recovery — but NOT the middle.
    # With the gap present, no full dwell is observed yet.
    await service.ingest(
        _DEVICE_ID,
        [
            _sreading(1, 0, 5.0),
            _sreading(7, 1800, 5.0),
            _sreading(8, 2100, 5.0),
            _sreading(9, 2400, 5.0),
        ],
    )
    assert excursions.last == []  # no excursion from the sparse series

    # Now backfill the missing high readings (out of order). Dwell (15m) is now satisfied and the
    # excursion closes at the recovery point.
    outcome = await service.ingest(
        _DEVICE_ID,
        [
            _sreading(2, 300, 5.0),
            _sreading(6, 1500, 10.0),
            _sreading(3, 600, 10.0),
            _sreading(5, 1200, 10.0),
            _sreading(4, 900, 10.0),
        ],
    )

    assert len(excursions.last) == 1
    breach = excursions.last[0]
    assert breach.direction is ExcursionDirection.HIGH
    assert breach.ended_at is not None
    assert breach.peak_temperature_c == pytest.approx(10.0)
    assert outcome.open_excursions == 0


async def test_unknown_device_raises() -> None:
    service, _r, _e, _p, _d = _service(device=False)
    with pytest.raises(DeviceNotFoundError):
        await service.ingest(_DEVICE_ID, [_reading(1, -600, 5.0)])


async def test_disabled_device_raises() -> None:
    service, _r, _e, _p, _d = _service(status=DeviceStatus.DISABLED)
    with pytest.raises(DeviceInactiveError):
        await service.ingest(_DEVICE_ID, [_reading(1, -600, 5.0)])


async def test_no_policy_skips_excursion_engine() -> None:
    service, readings, excursions, _pub, _dev = _service(band=None)
    outcome = await service.ingest(_DEVICE_ID, [_reading(1, -600, 5.0)])
    assert outcome.accepted == 1
    assert excursions.calls == 0
    assert outcome.open_excursions == 0
