"""Pydantic request/response models for the API surface.

``*Out`` models set ``from_attributes=True`` so they can be built straight from ORM objects.
Input models validate at the edge (e.g. a real IANA timezone, an aware ``starts_at``) so bad data
never reaches the service layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from typing import Annotated, Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    HttpUrl,
    ValidationInfo,
    field_validator,
)

from appointments_api.enums import (
    AppointmentStatus,
    DeviceStatus,
    ExcursionDirection,
    UserRole,
)
from appointments_api.services.webhooks.events import WebhookEventType

# ---- auth ----


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1)


class RefreshRequest(BaseModel):
    refresh_token: str = Field(min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"
    expires_in: int


# ---- users ----


class UserCreate(BaseModel):
    email: EmailStr
    full_name: str = Field(min_length=1, max_length=200)
    role: UserRole
    password: str = Field(min_length=8, max_length=200)


class UserOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    email: EmailStr
    full_name: str
    role: UserRole
    is_active: bool


# ---- clinics ----


class ClinicCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    timezone: str = Field(min_length=1, max_length=64)
    address: str = Field(min_length=1, max_length=500)
    cancellation_cutoff_hours: int = Field(default=24, ge=0, le=720)

    @field_validator("timezone")
    @classmethod
    def _valid_timezone(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown IANA timezone: {value!r}") from exc
        return value


class ClinicOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    timezone: str
    address: str
    cancellation_cutoff_hours: int


# ---- clinicians ----


class WorkingWindowIn(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start: time
    end: time

    @field_validator("end")
    @classmethod
    def _end_after_start(cls, end: time, info: ValidationInfo) -> time:
        start = info.data.get("start")
        if isinstance(start, time) and end <= start:
            raise ValueError("end must be after start")
        return end


class ClinicianCreate(BaseModel):
    clinic_id: uuid.UUID
    user_id: uuid.UUID
    specialty: str = Field(min_length=1, max_length=100)
    buffer_minutes: int = Field(default=0, ge=0, le=240)
    working_hours: list[WorkingWindowIn] = Field(default_factory=list)


class WorkingWindowOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    weekday: int
    start: time = Field(validation_alias="start_time")
    end: time = Field(validation_alias="end_time")


class ClinicianOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    clinic_id: uuid.UUID
    user_id: uuid.UUID
    specialty: str
    buffer_minutes: int
    working_hours: list[WorkingWindowOut]


# ---- services ----


class ServiceCreate(BaseModel):
    clinic_id: uuid.UUID
    name: str = Field(min_length=1, max_length=200)
    duration_minutes: int = Field(gt=0, le=1440)
    price_cents: int = Field(ge=0)
    currency: str = Field(default="USD", min_length=3, max_length=3)
    is_active: bool = True


class ServiceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    clinic_id: uuid.UUID
    name: str
    duration_minutes: int
    price_cents: int
    currency: str
    is_active: bool


# ---- appointments ----


class AppointmentCreate(BaseModel):
    clinic_id: uuid.UUID
    clinician_id: uuid.UUID
    service_id: uuid.UUID
    starts_at: AwareDatetime
    # Optional: admins/clinicians may book on behalf of a patient; a PATIENT omits it (self).
    patient_id: uuid.UUID | None = None


class AppointmentTransition(BaseModel):
    target_status: AppointmentStatus


class CancelRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class AppointmentOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    clinic_id: uuid.UUID
    clinician_id: uuid.UUID
    patient_id: uuid.UUID
    service_id: uuid.UUID
    starts_at: datetime
    ends_at: datetime
    status: AppointmentStatus
    version: int
    cancellation_reason: str | None


class SlotOut(BaseModel):
    start: datetime
    end: datetime


# ---- webhooks ----


class WebhookSubscriptionCreate(BaseModel):
    url: HttpUrl
    # The shared signing secret. Required to be reasonably long; write-only (never returned).
    secret: str = Field(min_length=16, max_length=255)
    event_types: list[WebhookEventType] = Field(min_length=1)
    clinic_id: uuid.UUID | None = None


class WebhookSubscriptionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    url: str
    event_types: list[str]
    clinic_id: uuid.UUID | None
    is_active: bool


# ---- devices ----


class DeviceCreate(BaseModel):
    clinic_id: uuid.UUID
    location_label: str = Field(min_length=1, max_length=200)
    hardware_version: str = Field(min_length=1, max_length=64)
    firmware_version: str = Field(min_length=1, max_length=64)


class DeviceOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    clinic_id: uuid.UUID
    location_label: str
    hardware_version: str
    firmware_version: str
    status: DeviceStatus
    last_seen_at: datetime | None


class DeviceCredentialOut(BaseModel):
    """Returned once at provisioning and once on each rotation — the only time the plaintext secret
    is ever exposed. The device stores it; the server keeps only its Argon2 hash."""

    device: DeviceOut
    secret: str


# ---- threshold policies ----


class ThresholdPolicyCreate(BaseModel):
    # Exactly one scope: a device-specific policy or a clinic-wide one.
    device_id: uuid.UUID | None = None
    clinic_id: uuid.UUID | None = None
    min_temperature_c: float
    max_temperature_c: float
    dwell_minutes: float = Field(gt=0)
    recovery_minutes: float = Field(gt=0)

    @field_validator("max_temperature_c")
    @classmethod
    def _max_above_min(cls, value: float, info: ValidationInfo) -> float:
        minimum = info.data.get("min_temperature_c")
        if isinstance(minimum, int | float) and value <= minimum:
            raise ValueError("max_temperature_c must be greater than min_temperature_c")
        return value

    @field_validator("clinic_id")
    @classmethod
    def _exactly_one_scope(
        cls, clinic_id: uuid.UUID | None, info: ValidationInfo
    ) -> uuid.UUID | None:
        device_id = info.data.get("device_id")
        if (device_id is None) == (clinic_id is None):
            raise ValueError("set exactly one of device_id or clinic_id")
        return clinic_id


class ThresholdPolicyOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID | None
    clinic_id: uuid.UUID | None
    min_temperature_c: float
    max_temperature_c: float
    dwell_minutes: float
    recovery_minutes: float


# ---- telemetry ----


class ContractReadingIn(BaseModel):
    """The inner reading, matching the vendored telemetry contract's ``reading`` object.

    ``extra="forbid"`` mirrors the schema's ``additionalProperties: false`` so the OpenAPI model and
    the vendored contract accept exactly the same shape (a contract test proves it).
    """

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    # Per-reading schema version; the device sends "v". Default is the current version.
    schema_version: Annotated[int, Field(alias="v")] = 2
    measured_at: AwareDatetime
    temperature_c: float
    humidity_pct: float | None = Field(default=None, ge=0, le=100)
    battery_pct: float | None = Field(default=None, ge=0, le=100)
    raw_temperature: int | None = None
    raw_humidity: int | None = None


class TelemetryEnvelopeItem(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=0)
    reading: ContractReadingIn


class TelemetryBatchIn(BaseModel):
    """The telemetry batch envelope — the same shape the device publishes over MQTT (spec §8)."""

    model_config = ConfigDict(populate_by_name=True)

    # Envelope schema version; the server accepts the current version and the previous one.
    schema_version: Annotated[int, Field(alias="v")] = 2
    # Present in the wire envelope; on the HTTP path the authenticated path device id wins.
    device_id: str | None = None
    idempotency_key: str | None = None
    readings: list[TelemetryEnvelopeItem] = Field(min_length=1, max_length=1000)


class TelemetryBatchItemResult(BaseModel):
    sequence: int
    status: str  # "accepted" | "duplicate" | "rejected"
    detail: str | None = None


class TelemetryBatchResult(BaseModel):
    device_id: uuid.UUID
    accepted: int
    duplicates: int
    rejected: int
    open_excursions: int
    results: list[TelemetryBatchItemResult]


class TelemetryReadingOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID
    sequence: int
    measured_at: datetime
    received_at: datetime
    temperature_c: float
    humidity_pct: float | None
    battery_pct: float | None
    clock_skew_flagged: bool


class TimeSeriesPoint(BaseModel):
    bucket_start: datetime
    value: float
    sample_count: int


class TimeSeriesOut(BaseModel):
    device_id: uuid.UUID
    bucket_seconds: int
    agg: str
    points: list[TimeSeriesPoint]


class DeviceHealthOut(BaseModel):
    device_id: uuid.UUID
    status: DeviceStatus
    last_seen_at: datetime | None
    last_temperature_c: float | None
    last_battery_pct: float | None
    open_excursions: int


# ---- excursions ----


class ExcursionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    device_id: uuid.UUID
    started_at: datetime
    ended_at: datetime | None
    direction: ExcursionDirection
    peak_temperature_c: float
    acknowledged_by: uuid.UUID | None
    acknowledged_at: datetime | None


# ---- audit log ----


class AuditLogEntryOut(BaseModel):
    """A single append-only audit record. ``before``/``after`` carry the JSONB change snapshots."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    actor_id: uuid.UUID | None
    action: str
    entity_type: str
    entity_id: str
    before: dict[str, Any] | None
    after: dict[str, Any] | None
    created_at: datetime
