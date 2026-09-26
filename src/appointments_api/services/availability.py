"""Availability / slot computation.

An ``AvailabilitySlot`` is never stored; it is computed on demand from a clinician's working
hours minus the appointments and blackout periods that already fill part of the day.

The one genuinely tricky part is time. Working hours are wall-clock times in the *clinic's*
timezone ("09:00-17:00"), but appointments are stored as absolute UTC instants. So we build each
candidate slot in local time, then convert to UTC. Because we let ``zoneinfo`` do the conversion
per calendar day, the same "09:00" maps to a different UTC instant on either side of a daylight
saving change — which is exactly the behaviour the DST test pins down.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from zoneinfo import ZoneInfo


@dataclass(frozen=True, slots=True)
class WorkingWindow:
    """One shift on one weekday, in the clinic's local wall-clock time."""

    weekday: int  # 0 = Monday .. 6 = Sunday
    start: time
    end: time


@dataclass(frozen=True, slots=True)
class TimeInterval:
    """A half-open [start, end) interval of absolute UTC time."""

    start: datetime
    end: datetime


@dataclass(frozen=True, slots=True)
class Slot:
    """A bookable slot, as absolute UTC instants."""

    start: datetime
    end: datetime


def _overlaps(a_start: datetime, a_end: datetime, b_start: datetime, b_end: datetime) -> bool:
    """True if two half-open intervals share any time."""
    return a_start < b_end and b_start < a_end


def compute_slots(
    *,
    day: date,
    clinic_tz: str,
    working_windows: Sequence[WorkingWindow],
    service_duration: timedelta,
    step: timedelta,
    now: datetime,
    buffer: timedelta = timedelta(0),
    busy: Sequence[TimeInterval] = (),
    blackout: Sequence[TimeInterval] = (),
) -> list[Slot]:
    """Return the free slots for one clinic-local calendar ``day``.

    A candidate slot survives if it fits inside a working window, does not start in the past
    (relative to ``now``), keeps ``buffer`` clear on both sides of every existing appointment
    (``busy``), and does not overlap any ``blackout`` period.
    """
    tz = ZoneInfo(clinic_tz)
    slots: list[Slot] = []

    for window in working_windows:
        if window.weekday != day.weekday():
            continue

        local_end = datetime.combine(day, window.end, tzinfo=tz)
        cursor = datetime.combine(day, window.start, tzinfo=tz)

        while cursor + service_duration <= local_end:
            start_utc = cursor.astimezone(UTC)
            end_utc = (cursor + service_duration).astimezone(UTC)
            cursor += step

            if start_utc < now:
                continue
            # The clinician needs `buffer` free before and after, so widen the guard band.
            guard_start = start_utc - buffer
            guard_end = end_utc + buffer
            if any(_overlaps(guard_start, guard_end, b.start, b.end) for b in busy):
                continue
            if any(_overlaps(start_utc, end_utc, bl.start, bl.end) for bl in blackout):
                continue
            slots.append(Slot(start=start_utc, end=end_utc))

    slots.sort(key=lambda s: s.start)
    return slots


class BusyPeriodRepository(Protocol):
    """Source of a clinician's already-booked time. Implemented by SQLAlchemy in production and
    by an in-memory fake in unit tests."""

    async def busy_intervals(
        self, clinician_id: str, start: datetime, end: datetime
    ) -> Sequence[TimeInterval]:
        """Return the clinician's live appointments overlapping [start, end), as UTC intervals."""
        ...


class AvailabilityService:
    """Computes bookable slots for a clinician on a given day.

    It depends only on a :class:`BusyPeriodRepository` protocol and an injected ``now`` callable,
    so unit tests drive it with a fake repository and a fixed clock — no database.
    """

    def __init__(self, repository: BusyPeriodRepository) -> None:
        self._repository = repository

    async def slots_for_day(
        self,
        *,
        clinician_id: str,
        day: date,
        clinic_tz: str,
        working_windows: Sequence[WorkingWindow],
        service_duration: timedelta,
        step: timedelta,
        now: datetime,
        buffer: timedelta = timedelta(0),
        blackout: Sequence[TimeInterval] = (),
    ) -> list[Slot]:
        tz = ZoneInfo(clinic_tz)
        day_start = datetime.combine(day, time.min, tzinfo=tz).astimezone(UTC)
        day_end = datetime.combine(day, time.max, tzinfo=tz).astimezone(UTC)
        busy = await self._repository.busy_intervals(clinician_id, day_start, day_end)
        return compute_slots(
            day=day,
            clinic_tz=clinic_tz,
            working_windows=working_windows,
            service_duration=service_duration,
            step=step,
            now=now,
            buffer=buffer,
            busy=busy,
            blackout=blackout,
        )
