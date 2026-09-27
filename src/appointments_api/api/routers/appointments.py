"""Appointment endpoints: book, list (role-scoped), read, transition, cancel.

RBAC (spec §4 rule 5): a PATIENT sees/acts on only their own appointments; a CLINICIAN on their
clinic's; CLINIC_ADMIN and PLATFORM_ADMIN on all. Clinic-scoping of CLINIC_ADMIN is intentionally
broad for now — the model has no admin↔clinic link yet; see docs/KNOWN_GAPS.md.
"""

from __future__ import annotations

import uuid
from datetime import UTC, timedelta

from fastapi import APIRouter, status

from appointments_api.api.deps import (
    AppointmentRepoDep,
    ClinicianRepoDep,
    ClinicRepoDep,
    ClockDep,
    CurrentUser,
    PageParams,
    ServiceRepoDep,
    UserRepoDep,
)
from appointments_api.api.errors import (
    ForbiddenError,
    NotFoundError,
    SlotUnavailableError,
    ValidationProblem,
)
from appointments_api.api.pagination import Page, build_page
from appointments_api.api.schemas import (
    AppointmentCreate,
    AppointmentOut,
    AppointmentTransition,
    CancelRequest,
)
from appointments_api.enums import AppointmentStatus, UserRole
from appointments_api.models import Appointment, User
from appointments_api.services.appointments.cancellation import assert_can_cancel
from appointments_api.services.appointments.state_machine import assert_transition
from appointments_api.services.availability import fits_working_hours

router = APIRouter(prefix="/appointments", tags=["appointments"])

_ADMIN_ROLES = frozenset({UserRole.CLINIC_ADMIN, UserRole.PLATFORM_ADMIN})


async def _assert_can_access(
    user: User, appointment: Appointment, clinicians: ClinicianRepoDep
) -> None:
    """Raise 403 unless ``user`` may see/act on ``appointment``."""
    if user.role in _ADMIN_ROLES:
        return
    if user.role is UserRole.PATIENT:
        if appointment.patient_id != user.id:
            raise ForbiddenError("You may only access your own appointments.")
        return
    if user.role is UserRole.CLINICIAN:
        clinician = await clinicians.get_by_user(user.id)
        if clinician is None or appointment.clinic_id != clinician.clinic_id:
            raise ForbiddenError("You may only access your clinic's appointments.")
        return
    raise ForbiddenError("Not allowed.")


@router.post("", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    body: AppointmentCreate,
    current: CurrentUser,
    users: UserRepoDep,
    clinics: ClinicRepoDep,
    clinicians: ClinicianRepoDep,
    services: ServiceRepoDep,
    appointments: AppointmentRepoDep,
) -> Appointment:
    # Resolve the patient: a PATIENT books for themselves; staff must name the patient.
    if current.role is UserRole.PATIENT:
        if body.patient_id is not None and body.patient_id != current.id:
            raise ForbiddenError("Patients can only book for themselves.")
        patient_id = current.id
    else:
        if body.patient_id is None:
            raise ValidationProblem("patient_id is required when booking on behalf of a patient.")
        patient = await users.get(body.patient_id)
        if patient is None or patient.role is not UserRole.PATIENT:
            raise ValidationProblem("patient_id must reference an existing patient.")
        patient_id = patient.id

    clinic = await clinics.get(body.clinic_id)
    if clinic is None:
        raise NotFoundError("No such clinic.")
    clinician = await clinicians.get(body.clinician_id)
    if clinician is None:
        raise NotFoundError("No such clinician.")
    service = await services.get(body.service_id)
    if service is None:
        raise NotFoundError("No such service.")

    if clinician.clinic_id != clinic.id or service.clinic_id != clinic.id:
        raise ValidationProblem("clinician and service must belong to the clinic.")
    if not service.is_active:
        raise ValidationProblem("That service is not active.")
    if current.role is UserRole.CLINICIAN:
        me = await clinicians.get_by_user(current.id)
        if me is None or me.clinic_id != clinic.id:
            raise ForbiddenError("You may only book in your own clinic.")

    starts_at = body.starts_at.astimezone(UTC)
    ends_at = starts_at + timedelta(minutes=service.duration_minutes)

    windows = await clinicians.working_windows(clinician)
    if not fits_working_hours(
        start=starts_at, end=ends_at, clinic_tz=clinic.timezone, working_windows=windows
    ):
        from appointments_api.services.errors import OutsideWorkingHoursError

        raise OutsideWorkingHoursError()

    # Service-layer overlap pre-check for a friendly error; the DB exclusion constraint is the
    # real guarantee under concurrency (a lost race surfaces as 409 via the IntegrityError handler).
    buffer = timedelta(minutes=clinician.buffer_minutes)
    busy = await appointments.busy_intervals(str(clinician.id), starts_at, ends_at)
    for interval in busy:
        if starts_at - buffer < interval.end and interval.start < ends_at + buffer:
            raise SlotUnavailableError(
                "That time overlaps an existing appointment for this clinician."
            )

    appointment = Appointment(
        clinic_id=clinic.id,
        clinician_id=clinician.id,
        patient_id=patient_id,
        service_id=service.id,
        starts_at=starts_at,
        ends_at=ends_at,
        status=AppointmentStatus.REQUESTED,
    )
    return await appointments.add(appointment)


@router.get("", response_model=Page[AppointmentOut])
async def list_appointments(
    current: CurrentUser,
    appointments: AppointmentRepoDep,
    clinicians: ClinicianRepoDep,
    page: PageParams,
) -> Page[AppointmentOut]:
    limit, cursor = page
    if current.role is UserRole.PATIENT:
        rows, has_more = await appointments.list_for_patient(current.id, limit=limit, cursor=cursor)
    elif current.role is UserRole.CLINICIAN:
        me = await clinicians.get_by_user(current.id)
        if me is None:
            rows, has_more = [], False
        else:
            rows, has_more = await appointments.list_for_clinic(
                me.clinic_id, limit=limit, cursor=cursor
            )
    else:
        rows, has_more = await appointments.list_all(limit=limit, cursor=cursor)
    return build_page(rows, has_more, AppointmentOut.model_validate)


@router.get("/{appointment_id}", response_model=AppointmentOut)
async def get_appointment(
    appointment_id: uuid.UUID,
    current: CurrentUser,
    appointments: AppointmentRepoDep,
    clinicians: ClinicianRepoDep,
) -> Appointment:
    appointment = await appointments.get(appointment_id)
    if appointment is None:
        raise NotFoundError("No such appointment.")
    await _assert_can_access(current, appointment, clinicians)
    return appointment


@router.post("/{appointment_id}/transition", response_model=AppointmentOut)
async def transition_appointment(
    appointment_id: uuid.UUID,
    body: AppointmentTransition,
    current: CurrentUser,
    appointments: AppointmentRepoDep,
    clinicians: ClinicianRepoDep,
) -> Appointment:
    appointment = await appointments.get(appointment_id)
    if appointment is None:
        raise NotFoundError("No such appointment.")
    if current.role is UserRole.PATIENT:
        raise ForbiddenError("Patients cannot change status directly; use the cancel endpoint.")
    await _assert_can_access(current, appointment, clinicians)
    if body.target_status is AppointmentStatus.CANCELLED:
        raise ValidationProblem("Use the cancel endpoint to cancel an appointment.")
    assert_transition(appointment.status, body.target_status)
    appointment.status = body.target_status
    return appointment


@router.post("/{appointment_id}/cancel", response_model=AppointmentOut)
async def cancel_appointment(
    appointment_id: uuid.UUID,
    body: CancelRequest,
    current: CurrentUser,
    appointments: AppointmentRepoDep,
    clinics: ClinicRepoDep,
    clinicians: ClinicianRepoDep,
    clock: ClockDep,
) -> Appointment:
    appointment = await appointments.get(appointment_id)
    if appointment is None:
        raise NotFoundError("No such appointment.")
    await _assert_can_access(current, appointment, clinicians)
    clinic = await clinics.get(appointment.clinic_id)
    cutoff = clinic.cancellation_cutoff_hours if clinic else 24
    assert_can_cancel(
        starts_at=appointment.starts_at,
        now=clock.now(),
        cutoff_hours=cutoff,
        role=current.role,
    )
    assert_transition(appointment.status, AppointmentStatus.CANCELLED)
    appointment.status = AppointmentStatus.CANCELLED
    appointment.cancellation_reason = body.reason
    return appointment
