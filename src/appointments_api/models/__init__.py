"""ORM models for Aurora Clinic's scheduling domain.

Importing this package registers every model on ``Base.metadata``, which is what Alembic reads
to know the full schema. Import models from here so that registration always happens.
"""

from __future__ import annotations

from appointments_api.models.appointment import Appointment
from appointments_api.models.audit import AuditLogEntry
from appointments_api.models.base import Base
from appointments_api.models.clinic import Clinic
from appointments_api.models.clinician import Clinician, ClinicianWorkingHours
from appointments_api.models.device import Device
from appointments_api.models.excursion import Excursion
from appointments_api.models.service import Service
from appointments_api.models.telemetry import TelemetryReading
from appointments_api.models.threshold_policy import ThresholdPolicy
from appointments_api.models.user import User
from appointments_api.models.webhook import WebhookSubscription

__all__ = [
    "Appointment",
    "AuditLogEntry",
    "Base",
    "Clinic",
    "Clinician",
    "ClinicianWorkingHours",
    "Device",
    "Excursion",
    "Service",
    "TelemetryReading",
    "ThresholdPolicy",
    "User",
    "WebhookSubscription",
]
