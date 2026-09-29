"""Appointment endpoints: book, list (role-scoped), read, transition, cancel.

RBAC (spec §4 rule 5): a PATIENT sees/acts on only their own appointments; a CLINICIAN on their
clinic's; CLINIC_ADMIN and PLATFORM_ADMIN on all. Clinic-scoping of CLINIC_ADMIN is intentionally
broad for now — the model has no admin↔clinic link yet; see docs/KNOWN_GAPS.md.

Milestone 4 advanced semantics live here too:

- **Idempotency-Key** on create (rule 6): a repeated key replays the original resource instead of
  booking a duplicate.
- **ETag / If-Match** on read and the two mutating endpoints (rule 7): every appointment response
  carries an ``ETag`` built from its ``version``; a transition or cancel must echo the current ETag
  in ``If-Match`` or it is refused (``428`` if absent, ``412`` if stale).
"""

from __future__ import annotations

import uuid
from datetime import UTC, timedelta
from typing import Annotated, Any

from fastapi import APIRouter, Header, Request, status
from starlette.responses import JSONResponse, Response

from appointments_api.api.conditional import if_match_satisfied, make_etag
from appointments_api.api.deps import (
    AppointmentRepoDep,
    ClinicianRepoDep,
    ClinicRepoDep,
    ClockDep,
    CurrentUser,
    IdempotencyServiceDep,
    PageParams,
    ServiceRepoDep,
    UserRepoDep,
    WebhookDispatcherDep,
)
from appointments_api.api.errors import (
    ForbiddenError,
    IdempotencyConflictError,
    IdempotencyKeyReusedError,
    NotFoundError,
    PreconditionFailedError,
    PreconditionRequiredError,
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
from appointments_api.services.clock import Clock
from appointments_api.services.idempotency import (
    BeginState,
    StoredResponse,
    request_fingerprint,
)
from appointments_api.services.webhooks.dispatcher import WebhookDispatcher
from appointments_api.services.webhooks.events import (
    WebhookEvent,
    WebhookEventType,
    event_type_for_status,
)

router = APIRouter(prefix="/appointments", tags=["appointments"])

_ADMIN_ROLES = frozenset({UserRole.CLINIC_ADMIN, UserRole.PLATFORM_ADMIN})
_IDEMPOTENCY_SCOPE = "appointments:create"


def _serialize(appointment: Appointment) -> dict[str, Any]:
    """The JSON body for an appointment, identical to what response_model would produce."""
    return AppointmentOut.model_validate(appointment).model_dump(mode="json")


def _appointment_response(appointment: Appointment, *, status_code: int = 200) -> JSONResponse:
    """Serialize an appointment and attach its ETag (derived from the optimistic-lock version)."""
    return JSONResponse(
        status_code=status_code,
        content=_serialize(appointment),
        headers={"ETag": make_etag(appointment.version)},
    )


def _replayed_response(stored: StoredResponse) -> JSONResponse:
    headers = {"Idempotency-Replayed": "true"}
    if stored.etag is not None:
        headers["ETag"] = stored.etag
    return JSONResponse(status_code=stored.status_code, content=stored.body, headers=headers)


async def _emit(
    dispatcher: WebhookDispatcher,
    clock: Clock,
    event_type: WebhookEventType,
    appointment: Appointment,
) -> None:
    """Publish a state-change event for the webhook worker to fan out and deliver."""
    event = WebhookEvent(
        id=uuid.uuid4().hex,
        type=event_type.value,
        occurred_at=clock.now(),
        data={"appointment": _serialize(appointment)},
    )
    await dispatcher.dispatch(event)


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


async def _build_appointment(
    body: AppointmentCreate,
    current: User,
    users: UserRepoDep,
    clinics: ClinicRepoDep,
    clinicians: ClinicianRepoDep,
    services: ServiceRepoDep,
    appointments: AppointmentRepoDep,
) -> Appointment:
    """Validate the request and persist a new appointment, or raise the right problem."""
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


@router.post("", response_model=AppointmentOut, status_code=status.HTTP_201_CREATED)
async def create_appointment(
    body: AppointmentCreate,
    request: Request,
    current: CurrentUser,
    users: UserRepoDep,
    clinics: ClinicRepoDep,
    clinicians: ClinicianRepoDep,
    services: ServiceRepoDep,
    appointments: AppointmentRepoDep,
    idempotency: IdempotencyServiceDep,
    dispatcher: WebhookDispatcherDep,
    clock: ClockDep,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> Response:
    fingerprint: str | None = None
    if idempotency_key is not None:
        fingerprint = request_fingerprint(current.id, await request.body())
        begin = await idempotency.begin(_IDEMPOTENCY_SCOPE, idempotency_key, fingerprint)
        if begin.state is BeginState.REPLAY and begin.stored is not None:
            return _replayed_response(begin.stored)
        if begin.state is BeginState.IN_PROGRESS:
            raise IdempotencyConflictError(
                "A request with this Idempotency-Key is still being processed; retry shortly."
            )
        if begin.state is BeginState.FINGERPRINT_MISMATCH:
            raise IdempotencyKeyReusedError(
                "This Idempotency-Key was already used for a different request body."
            )

    try:
        appointment = await _build_appointment(
            body, current, users, clinics, clinicians, services, appointments
        )
    except Exception:
        # A failed attempt must not poison the key: drop the pending marker so a corrected retry
        # can start fresh.
        if idempotency_key is not None and fingerprint is not None:
            await idempotency.release(_IDEMPOTENCY_SCOPE, idempotency_key, fingerprint)
        raise

    if idempotency_key is not None and fingerprint is not None:
        await idempotency.complete(
            _IDEMPOTENCY_SCOPE,
            idempotency_key,
            fingerprint,
            StoredResponse(
                status_code=status.HTTP_201_CREATED,
                body=_serialize(appointment),
                etag=make_etag(appointment.version),
            ),
        )
    await _emit(dispatcher, clock, WebhookEventType.APPOINTMENT_CREATED, appointment)
    return _appointment_response(appointment, status_code=status.HTTP_201_CREATED)


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
) -> Response:
    appointment = await appointments.get(appointment_id)
    if appointment is None:
        raise NotFoundError("No such appointment.")
    await _assert_can_access(current, appointment, clinicians)
    return _appointment_response(appointment)


def _require_if_match(if_match: str | None, current_version: int) -> None:
    """Enforce the ETag precondition on a mutating request."""
    if if_match is None:
        raise PreconditionRequiredError(
            "This update requires an If-Match header carrying the appointment's current ETag."
        )
    if not if_match_satisfied(if_match, current_version):
        raise PreconditionFailedError(
            "The appointment has changed since you last read it; re-read it and retry."
        )


@router.post("/{appointment_id}/transition", response_model=AppointmentOut)
async def transition_appointment(
    appointment_id: uuid.UUID,
    body: AppointmentTransition,
    current: CurrentUser,
    appointments: AppointmentRepoDep,
    clinicians: ClinicianRepoDep,
    dispatcher: WebhookDispatcherDep,
    clock: ClockDep,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Response:
    appointment = await appointments.get(appointment_id)
    if appointment is None:
        raise NotFoundError("No such appointment.")
    if current.role is UserRole.PATIENT:
        raise ForbiddenError("Patients cannot change status directly; use the cancel endpoint.")
    await _assert_can_access(current, appointment, clinicians)
    _require_if_match(if_match, appointment.version)
    if body.target_status is AppointmentStatus.CANCELLED:
        raise ValidationProblem("Use the cancel endpoint to cancel an appointment.")
    assert_transition(appointment.status, body.target_status)
    appointment.status = body.target_status
    await appointments.flush()
    event_type = event_type_for_status(appointment.status)
    if event_type is not None:
        await _emit(dispatcher, clock, event_type, appointment)
    return _appointment_response(appointment)


@router.post("/{appointment_id}/cancel", response_model=AppointmentOut)
async def cancel_appointment(
    appointment_id: uuid.UUID,
    body: CancelRequest,
    current: CurrentUser,
    appointments: AppointmentRepoDep,
    clinics: ClinicRepoDep,
    clinicians: ClinicianRepoDep,
    clock: ClockDep,
    dispatcher: WebhookDispatcherDep,
    if_match: Annotated[str | None, Header(alias="If-Match")] = None,
) -> Response:
    appointment = await appointments.get(appointment_id)
    if appointment is None:
        raise NotFoundError("No such appointment.")
    await _assert_can_access(current, appointment, clinicians)
    clinic = await clinics.get(appointment.clinic_id)
    cutoff = clinic.cancellation_cutoff_hours if clinic else 24
    # The cancellation window is an authorization rule (patient vs admin), checked before the ETag
    # precondition so an unauthorized cancel is refused without revealing the resource's version.
    assert_can_cancel(
        starts_at=appointment.starts_at,
        now=clock.now(),
        cutoff_hours=cutoff,
        role=current.role,
    )
    _require_if_match(if_match, appointment.version)
    assert_transition(appointment.status, AppointmentStatus.CANCELLED)
    appointment.status = AppointmentStatus.CANCELLED
    appointment.cancellation_reason = body.reason
    await appointments.flush()
    await _emit(dispatcher, clock, WebhookEventType.APPOINTMENT_CANCELLED, appointment)
    return _appointment_response(appointment)
