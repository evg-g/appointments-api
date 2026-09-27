"""Domain errors raised by the service layer.

These are plain exceptions with no HTTP knowledge. The API layer (milestone 3) maps each one to
an RFC 9457 problem+json response and status code; keeping them here means the business rules can
be tested without a web framework.
"""

from __future__ import annotations

from appointments_api.enums import AppointmentStatus


class DomainError(Exception):
    """Base class for every business-rule violation."""


class IllegalTransitionError(DomainError):
    """An appointment status change that the state machine does not allow (maps to 409)."""

    def __init__(self, current: AppointmentStatus, target: AppointmentStatus) -> None:
        self.current = current
        self.target = target
        super().__init__(f"Cannot move appointment from {current} to {target}.")


class CancellationWindowError(DomainError):
    """A patient tried to cancel later than the clinic's cutoff allows (maps to 403)."""

    def __init__(self, cutoff_hours: int) -> None:
        self.cutoff_hours = cutoff_hours
        super().__init__(
            f"Cancellation is only allowed more than {cutoff_hours}h before the appointment."
        )


class OutsideWorkingHoursError(DomainError):
    """The requested time is not inside the clinician's working hours (maps to 422)."""

    def __init__(self) -> None:
        super().__init__("The requested time is outside the clinician's working hours.")
