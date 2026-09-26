"""Unit tests for the cancellation-window policy. Uses an injected `now`, no wall clock."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from appointments_api.enums import UserRole
from appointments_api.services.appointments import cancellation
from appointments_api.services.errors import CancellationWindowError

NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
CUTOFF = 24


def test_patient_can_cancel_before_cutoff() -> None:
    starts_at = NOW + timedelta(hours=25)  # more than 24h away
    assert cancellation.can_cancel(
        starts_at=starts_at, now=NOW, cutoff_hours=CUTOFF, role=UserRole.PATIENT
    )
    cancellation.assert_can_cancel(
        starts_at=starts_at, now=NOW, cutoff_hours=CUTOFF, role=UserRole.PATIENT
    )


def test_patient_cannot_cancel_inside_cutoff() -> None:
    starts_at = NOW + timedelta(hours=23)  # inside the 24h window
    assert not cancellation.can_cancel(
        starts_at=starts_at, now=NOW, cutoff_hours=CUTOFF, role=UserRole.PATIENT
    )
    with pytest.raises(CancellationWindowError):
        cancellation.assert_can_cancel(
            starts_at=starts_at, now=NOW, cutoff_hours=CUTOFF, role=UserRole.PATIENT
        )


def test_exactly_at_cutoff_is_allowed_for_patient() -> None:
    starts_at = NOW + timedelta(hours=24)  # boundary is inclusive
    assert cancellation.can_cancel(
        starts_at=starts_at, now=NOW, cutoff_hours=CUTOFF, role=UserRole.PATIENT
    )


@pytest.mark.parametrize("role", [UserRole.CLINIC_ADMIN, UserRole.PLATFORM_ADMIN])
def test_admins_may_cancel_inside_cutoff(role: UserRole) -> None:
    starts_at = NOW + timedelta(hours=1)  # well inside the window
    cancellation.assert_can_cancel(starts_at=starts_at, now=NOW, cutoff_hours=CUTOFF, role=role)
