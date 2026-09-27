"""Pydantic request/response models for the API surface.

``*Out`` models set ``from_attributes=True`` so they can be built straight from ORM objects.
Input models validate at the edge (e.g. a real IANA timezone, an aware ``starts_at``) so bad data
never reaches the service layer.
"""

from __future__ import annotations

import uuid
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    EmailStr,
    Field,
    ValidationInfo,
    field_validator,
)

from appointments_api.enums import AppointmentStatus, UserRole

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
