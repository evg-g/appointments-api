"""Domain enumerations shared by the ORM models and the pure service layer.

These live at the package root, with no SQLAlchemy or FastAPI import, so the service layer
can depend on them without pulling in any infrastructure. Each string enum uses values equal
to its member names, so the value stored in the database and the value used in code are the
same token — there is nothing to translate and nothing to drift.
"""

from __future__ import annotations

from enum import IntEnum, StrEnum


class UserRole(StrEnum):
    PATIENT = "PATIENT"
    CLINICIAN = "CLINICIAN"
    CLINIC_ADMIN = "CLINIC_ADMIN"
    PLATFORM_ADMIN = "PLATFORM_ADMIN"


class AppointmentStatus(StrEnum):
    REQUESTED = "REQUESTED"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    CANCELLED = "CANCELLED"
    NO_SHOW = "NO_SHOW"


class Weekday(IntEnum):
    """Weekday numbering that matches :meth:`datetime.date.weekday` (Monday is 0)."""

    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6
