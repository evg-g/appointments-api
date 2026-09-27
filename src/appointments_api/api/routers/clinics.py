"""Clinic endpoints: create (platform admin), list, and read (any authenticated user)."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, status

from appointments_api.api.deps import ClinicRepoDep, CurrentUser, PageParams, require_roles
from appointments_api.api.errors import NotFoundError
from appointments_api.api.pagination import Page, build_page
from appointments_api.api.schemas import ClinicCreate, ClinicOut
from appointments_api.enums import UserRole
from appointments_api.models import Clinic

router = APIRouter(prefix="/clinics", tags=["clinics"])


@router.post(
    "",
    response_model=ClinicOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(UserRole.PLATFORM_ADMIN))],
)
async def create_clinic(body: ClinicCreate, clinics: ClinicRepoDep) -> Clinic:
    return await clinics.add(Clinic(**body.model_dump()))


@router.get("", response_model=Page[ClinicOut])
async def list_clinics(
    current: CurrentUser, clinics: ClinicRepoDep, page: PageParams
) -> Page[ClinicOut]:
    limit, cursor = page
    rows, has_more = await clinics.list(limit=limit, cursor=cursor)
    return build_page(rows, has_more, ClinicOut.model_validate)


@router.get("/{clinic_id}", response_model=ClinicOut)
async def get_clinic(clinic_id: uuid.UUID, current: CurrentUser, clinics: ClinicRepoDep) -> Clinic:
    clinic = await clinics.get(clinic_id)
    if clinic is None:
        raise NotFoundError("No such clinic.")
    return clinic
