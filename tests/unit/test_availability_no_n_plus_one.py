"""Spy test: computing a day of availability must not issue an N+1 of repository calls.

Uses ``mocker.spy`` (pytest-mock) to wrap the *real* in-memory repository and count how often the
availability service reaches for busy intervals. The whole day must be answered from a single query,
not one per candidate slot.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta

from pytest_mock import MockerFixture

from appointments_api.services.availability import AvailabilityService, WorkingWindow
from tests.fakes.repositories import InMemoryBusyRepository


async def test_slots_for_day_queries_busy_intervals_exactly_once(mocker: MockerFixture) -> None:
    repository = InMemoryBusyRepository()
    spy = mocker.spy(repository, "busy_intervals")
    service = AvailabilityService(repository)

    slots = await service.slots_for_day(
        clinician_id="c1",
        day=date(2035, 6, 11),  # a Monday
        clinic_tz="UTC",
        working_windows=[WorkingWindow(weekday=0, start=time(9), end=time(17))],
        service_duration=timedelta(minutes=30),
        step=timedelta(minutes=15),
        now=datetime(2035, 6, 1, tzinfo=UTC),
    )

    # Many candidate slots, but only one trip to the repository — no N+1.
    assert len(slots) > 1
    assert spy.call_count == 1
