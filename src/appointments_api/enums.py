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


class AuditAction(StrEnum):
    """What an ``audit_log`` row records (ADR 0016).

    Kept apart from ``WebhookEventType``: the webhook set is a public contract, while audit
    actions will grow past it (device provisioning, excursions, ...).
    """

    APPOINTMENT_CANCELLED = "appointment.cancelled"


class Weekday(IntEnum):
    """Weekday numbering that matches :meth:`datetime.date.weekday` (Monday is 0)."""

    MONDAY = 0
    TUESDAY = 1
    WEDNESDAY = 2
    THURSDAY = 3
    FRIDAY = 4
    SATURDAY = 5
    SUNDAY = 6


class DeviceStatus(StrEnum):
    """Lifecycle of a cold-chain sensor node.

    ``PROVISIONED``  registered, credentials issued, not yet reporting.
    ``ACTIVE``       reporting telemetry.
    ``DISABLED``     administratively silenced (credentials rejected, telemetry refused).
    ``RETIRED``      decommissioned; kept for historical telemetry, never reactivated.
    """

    PROVISIONED = "PROVISIONED"
    ACTIVE = "ACTIVE"
    DISABLED = "DISABLED"
    RETIRED = "RETIRED"


class ExcursionDirection(StrEnum):
    """Which rail of the safe temperature band a breach crossed.

    Values match the device's ``logic.excursion.ExcursionDirection`` (lower-case) so the shared
    excursion fixtures compare byte-for-byte across the two implementations.
    """

    LOW = "low"
    HIGH = "high"
