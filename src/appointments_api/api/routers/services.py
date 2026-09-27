"""Service endpoints: create (clinic/platform admin), list by clinic, read."""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from appointments_api.api.deps import (
    CurrentUser,
    PageParams,
    ServiceRepoDep,
    require_roles,
)
from appointments_api.api.errors import NotFoundError
from appointments_api.api.pagination import Page, build_page
from appointments_api.api.schemas import ServiceCreate, ServiceOut
from appointments_api.enums import UserRole
from appointments_api.models import Service

router = APIRouter(prefix="/services", tags=["services"])


@router.post(
    "",
    response_model=ServiceOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_roles(UserRole.CLINIC_ADMIN, UserRole.PLATFORM_ADMIN))],
)
async def create_service(body: ServiceCreate, services: ServiceRepoDep) -> Service:
    return await services.add(Service(**body.model_dump()))


@router.get("", response_model=Page[ServiceOut])
async def list_services(
    current: CurrentUser,
    services: ServiceRepoDep,
    page: PageParams,
    clinic_id: Annotated[uuid.UUID, Query()],
) -> Page[ServiceOut]:
    limit, cursor = page
    rows, has_more = await services.list_by_clinic(clinic_id, limit=limit, cursor=cursor)
    return build_page(rows, has_more, ServiceOut.model_validate)


@router.get("/{service_id}", response_model=ServiceOut)
async def get_service(
    service_id: uuid.UUID, current: CurrentUser, services: ServiceRepoDep
) -> Service:
    service = await services.get(service_id)
    if service is None:
        raise NotFoundError("No such service.")
    return service
