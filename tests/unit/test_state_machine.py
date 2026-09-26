"""Unit tests for the appointment status state machine. Pure logic, no I/O."""

from __future__ import annotations

import pytest

from appointments_api.enums import AppointmentStatus as S
from appointments_api.services.appointments import state_machine as sm
from appointments_api.services.errors import IllegalTransitionError

_LEGAL = [
    (S.REQUESTED, S.CONFIRMED),
    (S.REQUESTED, S.CANCELLED),
    (S.CONFIRMED, S.COMPLETED),
    (S.CONFIRMED, S.CANCELLED),
    (S.CONFIRMED, S.NO_SHOW),
]

# A no-show is only reachable from CONFIRMED; terminal states go nowhere.
_ILLEGAL = [
    (S.REQUESTED, S.COMPLETED),
    (S.REQUESTED, S.NO_SHOW),
    (S.CONFIRMED, S.REQUESTED),
    (S.COMPLETED, S.CANCELLED),
    (S.CANCELLED, S.CONFIRMED),
    (S.NO_SHOW, S.CONFIRMED),
    (S.REQUESTED, S.REQUESTED),
]


@pytest.mark.parametrize(("current", "target"), _LEGAL)
def test_legal_transitions_are_allowed(current: S, target: S) -> None:
    assert sm.can_transition(current, target) is True
    sm.assert_transition(current, target)  # does not raise


@pytest.mark.parametrize(("current", "target"), _ILLEGAL)
def test_illegal_transitions_are_rejected(current: S, target: S) -> None:
    assert sm.can_transition(current, target) is False
    with pytest.raises(IllegalTransitionError) as exc:
        sm.assert_transition(current, target)
    assert exc.value.current is current
    assert exc.value.target is target


def test_terminal_states_have_no_transitions() -> None:
    for terminal in (S.COMPLETED, S.CANCELLED, S.NO_SHOW):
        assert sm.allowed_transitions(terminal) == frozenset()
