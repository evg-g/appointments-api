"""Cancellation-window policy (spec §4, rule 4).

A patient may cancel only while there is still more than the clinic's cutoff (e.g. 24h) before
the appointment. A clinic admin or platform admin may cancel at any time. This is an
authorization rule *and* a business rule, so it lives in the service layer where both the API and
the tests can reach it, and it takes an injected ``now`` so it never reads the wall clock.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from appointments_api.enums import UserRole
from appointments_api.services.errors import CancellationWindowError

_PRIVILEGED_ROLES = frozenset({UserRole.CLINIC_ADMIN, UserRole.PLATFORM_ADMIN})


def can_cancel(
    *,
    starts_at: datetime,
    now: datetime,
    cutoff_hours: int,
    role: UserRole,
) -> bool:
    """True if ``role`` may cancel an appointment starting at ``starts_at`` given ``now``."""
    if role in _PRIVILEGED_ROLES:
        return True
    return starts_at - now >= timedelta(hours=cutoff_hours)


def assert_can_cancel(
    *,
    starts_at: datetime,
    now: datetime,
    cutoff_hours: int,
    role: UserRole,
) -> None:
    """Raise :class:`CancellationWindowError` if this role cannot cancel this appointment now."""
    if not can_cancel(starts_at=starts_at, now=now, cutoff_hours=cutoff_hours, role=role):
        raise CancellationWindowError(cutoff_hours)
