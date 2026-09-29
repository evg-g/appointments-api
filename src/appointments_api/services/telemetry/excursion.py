"""Server-side cold-chain excursion engine.

This is the *server's* copy of the excursion state machine. The device runs the same rule locally
(``aurora-sensor-agent/src/aurora_sensor_agent/logic/excursion.py``); the server re-derives
excursions from the stored telemetry series and the two must agree (spec §4 rule 11). Agreement is
proved by running both implementations against a **shared fixture set** — the device owns
``tests/fixtures/excursions/cases.json`` and this repo vendors a byte-identical copy that its parity
test drives.

Why re-derive server-side instead of trusting the device's word? Because telemetry arrives late, out
of order, and backfilled (spec §4 rule 9): a breach may only become visible once a gap is filled in.
The engine is pure and clock-free — it reasons over the ``measured_at`` timeline carried by the
readings, never the wall clock — so the same series always yields the same excursions, whether
evaluated live or replayed months later. A backward step in ``measured_at`` cannot make a dwell
timer go negative: the timeline is frozen at the last-seen instant for such a sample.

The algorithm is kept deliberately identical to the device's, down to the reset-on-recovery and
flapping rules, so any divergence surfaces immediately in the shared-fixture parity test rather than
as a silent disagreement in production.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from enum import Enum

from appointments_api.enums import ExcursionDirection


@dataclass(frozen=True, slots=True)
class ThresholdBand:
    """The four numbers that define an excursion, mirroring the device's ``ThresholdPolicy``."""

    min_temperature_c: float
    max_temperature_c: float
    dwell_minutes: float
    recovery_minutes: float


class ExcursionState(Enum):
    """Where the machine currently is (see the module docstring for the transition rule)."""

    NORMAL = "normal"
    PENDING = "pending"
    EXCURSION = "excursion"
    CLEARING = "clearing"


class ExcursionEventKind(Enum):
    STARTED = "started"
    ENDED = "ended"


@dataclass(frozen=True, slots=True)
class DerivedExcursion:
    """One breach derived from the stored series.

    ``started_at`` is when the temperature first left the band (not when the dwell elapsed).
    ``ended_at`` is when it returned to the band (``None`` while still open). The peak is the most
    extreme temperature reached — the highest for a HIGH breach, the lowest for a LOW one.
    """

    started_at: datetime
    direction: ExcursionDirection
    peak_temperature_c: float
    ended_at: datetime | None = None


@dataclass(frozen=True, slots=True)
class ExcursionEvent:
    """Emitted when a breach is raised (STARTED) or cleared (ENDED)."""

    kind: ExcursionEventKind
    at: datetime
    excursion: DerivedExcursion


class ExcursionDetector:
    """Feeds on ``(temperature, at)`` and emits an :class:`ExcursionEvent` on raise/clear."""

    def __init__(self, band: ThresholdBand) -> None:
        self._band = band
        self._dwell_s = band.dwell_minutes * 60.0
        self._recovery_s = band.recovery_minutes * 60.0
        self._state = ExcursionState.NORMAL
        self._last_at: datetime | None = None
        self._breach_start_at: datetime | None = None
        self._recovery_start_at: datetime | None = None
        self._direction: ExcursionDirection | None = None
        self._peak_c: float = 0.0

    @property
    def state(self) -> ExcursionState:
        return self._state

    def _out_of_range(self, temperature_c: float) -> ExcursionDirection | None:
        if temperature_c < self._band.min_temperature_c:
            return ExcursionDirection.LOW
        if temperature_c > self._band.max_temperature_c:
            return ExcursionDirection.HIGH
        return None

    def _track_peak(self, temperature_c: float) -> None:
        if self._direction is ExcursionDirection.HIGH:
            self._peak_c = max(self._peak_c, temperature_c)
        else:
            self._peak_c = min(self._peak_c, temperature_c)

    def open_excursion(self) -> DerivedExcursion:
        """The breach currently being tracked, as an open (``ended_at is None``) record."""
        assert self._breach_start_at is not None and self._direction is not None
        return DerivedExcursion(
            started_at=self._breach_start_at,
            direction=self._direction,
            peak_temperature_c=self._peak_c,
        )

    def update(self, temperature_c: float, at: datetime) -> ExcursionEvent | None:
        """Process one reading. Returns a STARTED/ENDED event, or ``None`` if nothing changed."""
        # Backward-clock guard: freeze the timeline so no timer measures negative time.
        if self._last_at is not None and at < self._last_at:
            at = self._last_at
        self._last_at = at

        direction = self._out_of_range(temperature_c)

        if self._state is ExcursionState.NORMAL:
            if direction is not None:
                self._state = ExcursionState.PENDING
                self._breach_start_at = at
                self._direction = direction
                self._peak_c = temperature_c
            return None

        if self._state is ExcursionState.PENDING:
            if direction is None:
                # Back in range before dwell elapsed: a door opening, not an excursion.
                self._reset_to_normal()
                return None
            self._track_peak(temperature_c)
            assert self._breach_start_at is not None
            if (at - self._breach_start_at).total_seconds() >= self._dwell_s:
                self._state = ExcursionState.EXCURSION
                return ExcursionEvent(ExcursionEventKind.STARTED, at, self.open_excursion())
            return None

        if self._state is ExcursionState.EXCURSION:
            if direction is not None:
                self._track_peak(temperature_c)
            else:
                self._state = ExcursionState.CLEARING
                self._recovery_start_at = at
            return None

        # CLEARING
        if direction is not None:
            # Dropped back out of range before recovery finished: still the same excursion.
            self._track_peak(temperature_c)
            self._state = ExcursionState.EXCURSION
            self._recovery_start_at = None
            return None
        assert self._recovery_start_at is not None
        if (at - self._recovery_start_at).total_seconds() >= self._recovery_s:
            ended = replace(self.open_excursion(), ended_at=self._recovery_start_at)
            self._reset_to_normal()
            return ExcursionEvent(ExcursionEventKind.ENDED, at, ended)
        return None

    def _reset_to_normal(self) -> None:
        self._state = ExcursionState.NORMAL
        self._breach_start_at = None
        self._recovery_start_at = None
        self._direction = None
        self._peak_c = 0.0


def detect_excursions(
    series: list[tuple[datetime, float]], band: ThresholdBand
) -> list[DerivedExcursion]:
    """Run a fresh detector over a whole ``(at, temperature)`` series.

    Returns every excursion found, closed ones first (in the order they closed) and an open one last
    if the series ends mid-breach (its ``ended_at`` is ``None``). This is the exact function the
    shared fixture set drives, so device and server can be proved to agree.
    """
    detector = ExcursionDetector(band)
    excursions: list[DerivedExcursion] = []
    for at, temperature in series:
        event = detector.update(temperature, at)
        if event is not None and event.kind is ExcursionEventKind.ENDED:
            excursions.append(event.excursion)
    if detector.state in (ExcursionState.EXCURSION, ExcursionState.CLEARING):
        excursions.append(detector.open_excursion())
    return excursions


__all__ = [
    "DerivedExcursion",
    "ExcursionDetector",
    "ExcursionEvent",
    "ExcursionEventKind",
    "ExcursionState",
    "ThresholdBand",
    "detect_excursions",
]
