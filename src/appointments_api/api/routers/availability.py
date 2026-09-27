"""Availability endpoint: free slots for a clinician on a given day for a given service."""

from __future__ import annotations

import uuid
from datetime import date, timedelta
from typing import Annotated

from fastapi import APIRouter, Query

from appointments_api.api.deps import (
    AppointmentRepoDep,
    ClinicianRepoDep,
    ClinicRepoDep,
    ClockDep,
    CurrentUser,
    ServiceRepoDep,
)
from appointments_api.api.errors import NotFoundError
from appointments_api.api.schemas import SlotOut
from appointments_api.services.availability import AvailabilityService

router = APIRouter(prefix="/availability", tags=["availability"])


@router.get("", response_model=list[SlotOut])
async def get_availability(
    current: CurrentUser,
    clinicians: ClinicianRepoDep,
    services: ServiceRepoDep,
    clinics: ClinicRepoDep,
    appointments: AppointmentRepoDep,
    clock: ClockDep,
    clinician_id: Annotated[uuid.UUID, Query()],
    service_id: Annotated[uuid.UUID, Query()],
    day: Annotated[date, Query()],
) -> list[SlotOut]:
    clinician = await clinicians.get(clinician_id)
    if clinician is None:
        raise NotFoundError("No such clinician.")
    service = await services.get(service_id)
    if service is None:
        raise NotFoundError("No such service.")
    clinic = await clinics.get(clinician.clinic_id)
    if clinic is None:
        raise NotFoundError("No such clinic.")

    windows = await clinicians.working_windows(clinician)
    duration = timedelta(minutes=service.duration_minutes)
    availability = AvailabilityService(appointments)
    slots = await availability.slots_for_day(
        clinician_id=str(clinician.id),
        day=day,
        clinic_tz=clinic.timezone,
        working_windows=windows,
        service_duration=duration,
        step=duration,
        now=clock.now(),
        buffer=timedelta(minutes=clinician.buffer_minutes),
    )
    return [SlotOut(start=s.start, end=s.end) for s in slots]
