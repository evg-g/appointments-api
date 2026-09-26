"""The appointment status state machine.

Allowed moves (spec §4, rule 1):

    REQUESTED  -> CONFIRMED, CANCELLED
    CONFIRMED  -> COMPLETED, CANCELLED, NO_SHOW
    COMPLETED  -> (terminal)
    CANCELLED  -> (terminal)
    NO_SHOW    -> (terminal)

Any move not in this table is illegal and raises, which the API turns into a 409. Keeping the
rules as data (a dict) rather than a pile of if-statements makes them easy to read and to test
exhaustively.
"""

from __future__ import annotations

from appointments_api.enums import AppointmentStatus
from appointments_api.services.errors import IllegalTransitionError

_ALLOWED: dict[AppointmentStatus, frozenset[AppointmentStatus]] = {
    AppointmentStatus.REQUESTED: frozenset(
        {AppointmentStatus.CONFIRMED, AppointmentStatus.CANCELLED}
    ),
    AppointmentStatus.CONFIRMED: frozenset(
        {AppointmentStatus.COMPLETED, AppointmentStatus.CANCELLED, AppointmentStatus.NO_SHOW}
    ),
    AppointmentStatus.COMPLETED: frozenset(),
    AppointmentStatus.CANCELLED: frozenset(),
    AppointmentStatus.NO_SHOW: frozenset(),
}


def allowed_transitions(current: AppointmentStatus) -> frozenset[AppointmentStatus]:
    """Return the set of statuses reachable in one step from ``current``."""
    return _ALLOWED[current]


def can_transition(current: AppointmentStatus, target: AppointmentStatus) -> bool:
    """True if moving from ``current`` to ``target`` is a single legal step."""
    return target in _ALLOWED[current]


def assert_transition(current: AppointmentStatus, target: AppointmentStatus) -> None:
    """Raise :class:`IllegalTransitionError` unless the move is allowed."""
    if not can_transition(current, target):
        raise IllegalTransitionError(current, target)
