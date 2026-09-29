"""Property-based invariants for slot computation (``services/availability.py``).

Example-based unit tests pin down a handful of hand-picked days. These tests instead assert the
*laws* the slot computer must obey for every input hypothesis can think of: durations, DST-active
timezones, arbitrary working windows, busy periods, blackouts, and buffers. If any law can be
broken, hypothesis shrinks to the smallest counter-example and prints it.

No I/O: these exercise the pure functions directly, so they run at unit speed.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from hypothesis import given, settings
from hypothesis import strategies as st

from appointments_api.services.availability import (
    Slot,
    TimeInterval,
    WorkingWindow,
    compute_slots,
    fits_working_hours,
)

# A spread of real zones, including ones with daylight-saving transitions in both hemispheres,
# so the local->UTC conversion is genuinely exercised (not just the UTC identity case).
_TIMEZONES = [
    "UTC",
    "America/New_York",
    "Europe/London",
    "Europe/Berlin",
    "Australia/Sydney",
    "Asia/Kolkata",  # a +05:30 offset with no DST — catches half-hour-offset bugs
]

_QUARTER_HOURS = [0, 15, 30, 45]


@dataclass(frozen=True, slots=True)
class Scenario:
    """One fully-drawn set of arguments for ``compute_slots``."""

    day: date
    clinic_tz: str
    working_windows: tuple[WorkingWindow, ...]
    service_duration: timedelta
    step: timedelta
    now: datetime
    buffer: timedelta
    busy: tuple[TimeInterval, ...]
    blackout: tuple[TimeInterval, ...]


def _wall_time(hour: int, minute: int) -> time:
    return time(hour=hour, minute=minute)


@st.composite
def _scenarios(draw: st.DrawFn) -> Scenario:
    day = draw(st.dates(min_value=date(2021, 1, 1), max_value=date(2030, 12, 31)))
    clinic_tz = draw(st.sampled_from(_TIMEZONES))

    # Working windows. Bias most of them onto the drawn day's weekday so slots actually appear;
    # the computer must ignore windows on other weekdays, which we also let happen.
    def _window() -> st.SearchStrategy[WorkingWindow]:
        start_h = st.integers(min_value=0, max_value=22)
        return st.builds(
            lambda wd, sh, sm, span_h, em: WorkingWindow(
                weekday=wd,
                start=_wall_time(sh, sm),
                # end is at least one hour after start and never past 23:xx
                end=_wall_time(min(sh + span_h, 23), em),
            ),
            st.sampled_from([day.weekday(), day.weekday(), draw(st.integers(0, 6))]),
            start_h,
            st.sampled_from(_QUARTER_HOURS),
            st.integers(min_value=1, max_value=4),
            st.sampled_from(_QUARTER_HOURS),
        )

    windows = tuple(w for w in draw(st.lists(_window(), max_size=3)) if w.start < w.end)

    service_minutes = draw(st.sampled_from([15, 30, 45, 60, 90]))
    step_minutes = draw(st.sampled_from([5, 15, 30, 60]))
    buffer_minutes = draw(st.sampled_from([0, 5, 15, 30]))

    # ``now`` well before the day so the past-filter is inert unless a test opts in.
    day_start_utc = datetime.combine(day, time.min, tzinfo=ZoneInfo(clinic_tz)).astimezone(UTC)
    now = day_start_utc - timedelta(days=1)

    def _interval() -> st.SearchStrategy[TimeInterval]:
        return st.builds(
            lambda offset_min, dur_min: TimeInterval(
                start=day_start_utc + timedelta(minutes=offset_min),
                end=day_start_utc + timedelta(minutes=offset_min + dur_min),
            ),
            st.integers(min_value=0, max_value=24 * 60),
            st.integers(min_value=15, max_value=180),
        )

    busy = tuple(draw(st.lists(_interval(), max_size=3)))
    blackout = tuple(draw(st.lists(_interval(), max_size=3)))

    return Scenario(
        day=day,
        clinic_tz=clinic_tz,
        working_windows=windows,
        service_duration=timedelta(minutes=service_minutes),
        step=timedelta(minutes=step_minutes),
        now=now,
        buffer=timedelta(minutes=buffer_minutes),
        busy=busy,
        blackout=blackout,
    )


def _run(scenario: Scenario) -> list[Slot]:
    return compute_slots(
        day=scenario.day,
        clinic_tz=scenario.clinic_tz,
        working_windows=scenario.working_windows,
        service_duration=scenario.service_duration,
        step=scenario.step,
        now=scenario.now,
        buffer=scenario.buffer,
        busy=scenario.busy,
        blackout=scenario.blackout,
    )


def _overlaps(a: TimeInterval, b_start: datetime, b_end: datetime) -> bool:
    return a.start < b_end and b_start < a.end


@given(_scenarios())
@settings(max_examples=250, deadline=None)
def test_every_slot_has_the_requested_duration(scenario: Scenario) -> None:
    for slot in _run(scenario):
        assert slot.end - slot.start == scenario.service_duration


@given(_scenarios())
@settings(max_examples=250, deadline=None)
def test_slots_are_returned_sorted_and_unique_by_start(scenario: Scenario) -> None:
    slots = _run(scenario)
    starts = [s.start for s in slots]
    assert starts == sorted(starts)


@given(_scenarios())
@settings(max_examples=250, deadline=None)
def test_no_slot_overlaps_a_blackout(scenario: Scenario) -> None:
    for slot in _run(scenario):
        assert not any(_overlaps(bl, slot.start, slot.end) for bl in scenario.blackout)


@given(_scenarios())
@settings(max_examples=250, deadline=None)
def test_busy_periods_are_kept_clear_including_the_buffer(scenario: Scenario) -> None:
    for slot in _run(scenario):
        guard_start = slot.start - scenario.buffer
        guard_end = slot.end + scenario.buffer
        assert not any(b.start < guard_end and guard_start < b.end for b in scenario.busy)


@given(_scenarios())
@settings(max_examples=250, deadline=None)
def test_adding_a_blackout_never_creates_slots(scenario: Scenario) -> None:
    # A blackout can only remove availability, never add it.
    without = compute_slots(
        day=scenario.day,
        clinic_tz=scenario.clinic_tz,
        working_windows=scenario.working_windows,
        service_duration=scenario.service_duration,
        step=scenario.step,
        now=scenario.now,
        buffer=scenario.buffer,
        busy=scenario.busy,
        blackout=(),
    )
    with_blackout = _run(scenario)
    assert len(with_blackout) <= len(without)
    assert set(with_blackout).issubset(set(without))


@given(_scenarios())
@settings(max_examples=250, deadline=None)
def test_computation_is_deterministic(scenario: Scenario) -> None:
    assert _run(scenario) == _run(scenario)


@given(_scenarios())
@settings(max_examples=250, deadline=None)
def test_slots_fit_working_hours_by_the_independent_checker(scenario: Scenario) -> None:
    # With no busy/blackout and no buffer, every produced slot must satisfy the separate
    # ``fits_working_hours`` predicate — two implementations of "inside a window" must agree.
    slots = compute_slots(
        day=scenario.day,
        clinic_tz=scenario.clinic_tz,
        working_windows=scenario.working_windows,
        service_duration=scenario.service_duration,
        step=scenario.step,
        now=scenario.now,
        buffer=timedelta(0),
        busy=(),
        blackout=(),
    )
    for slot in slots:
        assert fits_working_hours(
            start=slot.start,
            end=slot.end,
            clinic_tz=scenario.clinic_tz,
            working_windows=scenario.working_windows,
        )


@given(_scenarios())
@settings(max_examples=250, deadline=None)
def test_slots_never_start_in_the_past(scenario: Scenario) -> None:
    # Move ``now`` into the middle of the day and confirm nothing before it survives.
    day_start_utc = datetime.combine(
        scenario.day, time.min, tzinfo=ZoneInfo(scenario.clinic_tz)
    ).astimezone(UTC)
    now = day_start_utc + timedelta(hours=12)
    slots = compute_slots(
        day=scenario.day,
        clinic_tz=scenario.clinic_tz,
        working_windows=scenario.working_windows,
        service_duration=scenario.service_duration,
        step=scenario.step,
        now=now,
        buffer=scenario.buffer,
        busy=scenario.busy,
        blackout=scenario.blackout,
    )
    for slot in slots:
        assert slot.start >= now


@given(day=st.dates(min_value=date(2021, 1, 1), max_value=date(2030, 12, 31)))
@settings(max_examples=50, deadline=None)
def test_fits_working_hours_is_false_when_no_window_matches(day: date) -> None:
    # A slot on a day with no working window can never fit.
    start = datetime.combine(day, time(10, 0), tzinfo=UTC)
    end = start + timedelta(minutes=30)
    assert not fits_working_hours(start=start, end=end, clinic_tz="UTC", working_windows=())
