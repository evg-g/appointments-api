"""Clinician endpoints: create (clinic/platform admin), list by clinic, read."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from appointments_api.api.deps import (
    ClinicianRepoDep,
    CurrentUser,
    PageParams,
    require_roles,
)
from appointments_api.api.errors import NotFoundError
from appointments_api.api.pagination import Page, build_page
from appointments_api.api.schemas import ClinicianCreate, ClinicianOut
from appointments_api.enums import UserRole
from appointments_api.models import Clinician, ClinicianWorkingHours

router = APIRouter(prefix="/clinicians", tags=["clinicians"])


@router.post(
    "",
    response_model=ClinicianOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(UserRole.CLINIC_ADMIN, UserRole.PLATFORM_ADMIN))],
)
async def create_clinician(body: ClinicianCreate, clinicians: ClinicianRepoDep) -> Clinician:
    clinician = Clinician(
        clinic_id=body.clinic_id,
        user_id=body.user_id,
        specialty=body.specialty,
        buffer_minutes=body.buffer_minutes,
        working_hours=[
            ClinicianWorkingHours(weekday=w.weekday, start_time=w.start, end_time=w.end)
            for w in body.working_hours
        ],
    )
    return await clinicians.add(clinician)


@router.get("", response_model=Page[ClinicianOut])
async def list_clinicians(
    current: CurrentUser,
    clinicians: ClinicianRepoDep,
    page: PageParams,
    clinic_id: Annotated[uuid.UUID, Query()],
) -> Page[ClinicianOut]:
    limit, cursor = page
    rows, has_more = await clinicians.list_by_clinic(clinic_id, limit=limit, cursor=cursor)
    return build_page(rows, has_more, ClinicianOut.model_validate)


@router.get("/{clinician_id}", response_model=ClinicianOut)
async def get_clinician(
    clinician_id: uuid.UUID, current: CurrentUser, clinicians: ClinicianRepoDep
) -> Clinician:
    clinician = await clinicians.get(clinician_id)
    if clinician is None:
        raise NotFoundError("No such clinician.")
    return clinician
