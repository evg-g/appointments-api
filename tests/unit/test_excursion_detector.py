"""Behaviour of the server excursion state machine, reading by reading.

The shared-fixture parity test proves the server agrees with the device on whole series. These tests
pin the individual rules that series-level output cannot see: the band edges are in range, the
recovery timer is measured in minutes, the events carry the right time and record, the machine
passes through CLEARING, and a backward clock step cannot move a breach start into the past.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from appointments_api.enums import ExcursionDirection
from appointments_api.services.telemetry.excursion import (
    ExcursionDetector,
    ExcursionEventKind,
    ExcursionState,
    ThresholdBand,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)
BAND = ThresholdBand(
    min_temperature_c=2.0, max_temperature_c=8.0, dwell_minutes=30, recovery_minutes=10
)


def _at(minutes: float) -> datetime:
    return T0 + timedelta(minutes=minutes)


def _state(detector: ExcursionDetector) -> ExcursionState:
    # Read through a call so mypy does not carry a narrowed ``detector.state`` across updates.
    return detector.state


def _open_high_excursion(detector: ExcursionDetector) -> None:
    """Drive a detector into EXCURSION: breach at t=0, dwell elapses at t=30."""
    assert detector.update(9.0, _at(0)) is None
    started = detector.update(9.5, _at(30))
    assert started is not None and started.kind is ExcursionEventKind.STARTED


def test_readings_exactly_on_the_band_edges_are_in_range() -> None:
    detector = ExcursionDetector(BAND)

    for minute, temperature in enumerate([2.0, 8.0, 2.0, 8.0]):
        assert detector.update(temperature, _at(minute * 60)) is None
        assert _state(detector) is ExcursionState.NORMAL


def test_started_event_carries_the_dwell_time_and_the_open_record() -> None:
    detector = ExcursionDetector(BAND)
    detector.update(9.0, _at(0))
    detector.update(10.5, _at(15))

    event = detector.update(9.5, _at(30))

    assert event is not None
    assert event.kind is ExcursionEventKind.STARTED
    assert event.at == _at(30)
    assert event.excursion.started_at == _at(0)  # when it left the band, not when dwell elapsed
    assert event.excursion.direction is ExcursionDirection.HIGH
    assert event.excursion.peak_temperature_c == 10.5
    assert event.excursion.ended_at is None
    assert _state(detector) is ExcursionState.EXCURSION


def test_returning_to_range_moves_to_clearing_not_straight_to_normal() -> None:
    detector = ExcursionDetector(BAND)
    _open_high_excursion(detector)

    assert detector.update(5.0, _at(40)) is None
    assert _state(detector) is ExcursionState.CLEARING


def test_recovery_is_measured_in_minutes() -> None:
    detector = ExcursionDetector(BAND)
    _open_high_excursion(detector)
    detector.update(5.0, _at(40))  # recovery starts

    # Five minutes back in range is not yet the ten-minute recovery.
    assert detector.update(5.0, _at(45)) is None
    assert _state(detector) is ExcursionState.CLEARING

    ended = detector.update(5.0, _at(50))
    assert ended is not None
    assert ended.kind is ExcursionEventKind.ENDED
    assert ended.at == _at(50)
    assert ended.excursion.started_at == _at(0)
    assert ended.excursion.ended_at == _at(40)  # when it returned to the band
    assert _state(detector) is ExcursionState.NORMAL


def test_a_backward_clock_step_cannot_move_the_breach_start_into_the_past() -> None:
    detector = ExcursionDetector(BAND)
    detector.update(5.0, _at(100))

    # Out of range, but stamped earlier than the last reading: the timeline is frozen at t=100.
    detector.update(9.0, _at(10))
    event = detector.update(9.0, _at(130))

    assert event is not None
    assert event.kind is ExcursionEventKind.STARTED
    assert event.excursion.started_at == _at(100)
