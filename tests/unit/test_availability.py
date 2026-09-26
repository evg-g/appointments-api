"""Unit tests for slot computation, including the required DST-transition case.

All pure and I/O-free. The AvailabilityService test uses the in-memory repository fake.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from freezegun import freeze_time

from appointments_api.services.availability import (
    AvailabilityService,
    TimeInterval,
    WorkingWindow,
    compute_slots,
)
from tests.fakes.repositories import InMemoryBusyRepository

# A "now" far in the past so past-slot filtering never interferes unless a test sets it.
LONG_AGO = datetime(2000, 1, 1, tzinfo=UTC)


def _utc(y: int, mo: int, d: int, h: int, mi: int = 0) -> datetime:
    return datetime(y, mo, d, h, mi, tzinfo=UTC)


def test_dst_spring_forward_shifts_the_utc_instant() -> None:
    # New York springs forward on 2026-03-08. The same local 09:00 is 14:00Z the day before
    # (EST, UTC-5) and 13:00Z on/after the change (EDT, UTC-4). Same wall time, different instant.
    windows = [
        WorkingWindow(weekday=5, start=time(9), end=time(10)),  # Saturday 2026-03-07
        WorkingWindow(weekday=6, start=time(9), end=time(10)),  # Sunday   2026-03-08
    ]

    before = compute_slots(
        day=date(2026, 3, 7),
        clinic_tz="America/New_York",
        working_windows=windows,
        service_duration=timedelta(hours=1),
        step=timedelta(hours=1),
        now=LONG_AGO,
    )
    after = compute_slots(
        day=date(2026, 3, 8),
        clinic_tz="America/New_York",
        working_windows=windows,
        service_duration=timedelta(hours=1),
        step=timedelta(hours=1),
        now=LONG_AGO,
    )

    assert [s.start for s in before] == [_utc(2026, 3, 7, 14)]
    assert [s.start for s in after] == [_utc(2026, 3, 8, 13)]


def test_freezegun_cross_check_matches_injected_clock() -> None:
    # Belt-and-braces: the spec asks for freezegun as an independent cross-check of the injected
    # clock. Frozen wall time and an explicit `now` must produce the same past-filtering result.
    windows = [WorkingWindow(weekday=0, start=time(9), end=time(11))]
    frozen = _utc(2026, 6, 1, 9, 45)  # a Monday
    with freeze_time(frozen):
        slots = compute_slots(
            day=date(2026, 6, 1),
            clinic_tz="UTC",
            working_windows=windows,
            service_duration=timedelta(minutes=30),
            step=timedelta(minutes=30),
            now=frozen,
        )
    assert [s.start for s in slots] == [_utc(2026, 6, 1, 10), _utc(2026, 6, 1, 10, 30)]


def test_buffer_blocks_slots_adjacent_to_existing_appointments() -> None:
    windows = [WorkingWindow(weekday=0, start=time(9), end=time(11))]
    busy = [TimeInterval(start=_utc(2026, 6, 1, 10), end=_utc(2026, 6, 1, 10, 30))]
    slots = compute_slots(
        day=date(2026, 6, 1),
        clinic_tz="UTC",
        working_windows=windows,
        service_duration=timedelta(minutes=30),
        step=timedelta(minutes=30),
        now=LONG_AGO,
        buffer=timedelta(minutes=15),
        busy=busy,
    )
    # 09:30 (guard 09:15-10:15) and 10:30 (guard 10:15-11:15) both touch the busy block; only
    # 09:00 keeps 15 minutes clear on both sides.
    assert [s.start for s in slots] == [_utc(2026, 6, 1, 9)]


def test_past_slots_are_dropped() -> None:
    windows = [WorkingWindow(weekday=0, start=time(9), end=time(11))]
    slots = compute_slots(
        day=date(2026, 6, 1),
        clinic_tz="UTC",
        working_windows=windows,
        service_duration=timedelta(minutes=30),
        step=timedelta(minutes=30),
        now=_utc(2026, 6, 1, 9, 45),
    )
    assert [s.start for s in slots] == [_utc(2026, 6, 1, 10), _utc(2026, 6, 1, 10, 30)]


def test_blackout_removes_only_the_overlapping_slot() -> None:
    windows = [WorkingWindow(weekday=0, start=time(9), end=time(11))]
    blackout = [TimeInterval(start=_utc(2026, 6, 1, 10), end=_utc(2026, 6, 1, 10, 30))]
    slots = compute_slots(
        day=date(2026, 6, 1),
        clinic_tz="UTC",
        working_windows=windows,
        service_duration=timedelta(minutes=30),
        step=timedelta(minutes=30),
        now=LONG_AGO,
        blackout=blackout,
    )
    starts = [s.start for s in slots]
    assert _utc(2026, 6, 1, 10) not in starts
    assert starts == [
        _utc(2026, 6, 1, 9),
        _utc(2026, 6, 1, 9, 30),
        _utc(2026, 6, 1, 10, 30),
    ]


def test_days_off_produce_no_slots() -> None:
    # Working hours only on Monday; asking for a Tuesday returns nothing.
    windows = [WorkingWindow(weekday=0, start=time(9), end=time(17))]
    slots = compute_slots(
        day=date(2026, 6, 2),  # Tuesday
        clinic_tz="UTC",
        working_windows=windows,
        service_duration=timedelta(minutes=30),
        step=timedelta(minutes=30),
        now=LONG_AGO,
    )
    assert slots == []


async def test_availability_service_excludes_busy_from_the_fake_repository() -> None:
    # Demonstrates the Fake technique: an in-memory repository standing in for SQLAlchemy.
    repo = InMemoryBusyRepository()
    repo.add("c1", TimeInterval(start=_utc(2026, 6, 1, 10), end=_utc(2026, 6, 1, 10, 30)))
    service = AvailabilityService(repository=repo)

    slots = await service.slots_for_day(
        clinician_id="c1",
        day=date(2026, 6, 1),
        clinic_tz="UTC",
        working_windows=[WorkingWindow(weekday=0, start=time(9), end=time(11))],
        service_duration=timedelta(minutes=30),
        step=timedelta(minutes=30),
        now=LONG_AGO,
    )

    starts = [s.start for s in slots]
    assert _utc(2026, 6, 1, 10) not in starts
    assert starts == [
        _utc(2026, 6, 1, 9),
        _utc(2026, 6, 1, 9, 30),
        _utc(2026, 6, 1, 10, 30),
    ]
