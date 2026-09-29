"""Threshold-policy management and excursion acknowledgement.

Kept out of the device router so the paths read naturally: ``/threshold-policies`` and
``/excursions/{id}``. Policies are admin-managed; acknowledging an excursion is a clinician/admin
action recorded with who and when.
"""

from __future__ import annotations

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, status

from appointments_api.api.deps import (
    ClockDep,
    ExcursionRepoDep,
    ThresholdPolicyRepoDep,
    require_roles,
)
from appointments_api.api.errors import NotFoundError
from appointments_api.api.schemas import (
    ExcursionOut,
    ThresholdPolicyCreate,
    ThresholdPolicyOut,
)
from appointments_api.enums import UserRole
from appointments_api.models import Excursion, ThresholdPolicy, User

router = APIRouter(tags=["telemetry"])

AdminUser = Annotated[User, Depends(require_roles(UserRole.CLINIC_ADMIN, UserRole.PLATFORM_ADMIN))]
StaffUser = Annotated[
    User,
    Depends(require_roles(UserRole.CLINICIAN, UserRole.CLINIC_ADMIN, UserRole.PLATFORM_ADMIN)),
]


@router.post(
    "/threshold-policies",
    response_model=ThresholdPolicyOut,
    status_code=status.HTTP_201_CREATED,
    tags=["telemetry"],
)
async def create_threshold_policy(
    body: ThresholdPolicyCreate,
    _admin: AdminUser,
    repo: ThresholdPolicyRepoDep,
) -> ThresholdPolicy:
    """Create a device-scoped or clinic-scoped threshold band (exactly one scope)."""
    policy = ThresholdPolicy(
        device_id=body.device_id,
        clinic_id=body.clinic_id,
        min_temperature_c=body.min_temperature_c,
        max_temperature_c=body.max_temperature_c,
        dwell_minutes=body.dwell_minutes,
        recovery_minutes=body.recovery_minutes,
    )
    return await repo.add(policy)


@router.get("/excursions/{excursion_id}", response_model=ExcursionOut, tags=["telemetry"])
async def get_excursion(
    excursion_id: uuid.UUID,
    _staff: StaffUser,
    repo: ExcursionRepoDep,
) -> Excursion:
    excursion = await repo.get(excursion_id)
    if excursion is None:
        raise NotFoundError("No such excursion.")
    return excursion


@router.post(
    "/excursions/{excursion_id}:acknowledge",
    response_model=ExcursionOut,
    tags=["telemetry"],
)
async def acknowledge_excursion(
    excursion_id: uuid.UUID,
    staff: StaffUser,
    repo: ExcursionRepoDep,
    clock: ClockDep,
) -> Excursion:
    """Record that a staff member has reviewed a cold-chain breach."""
    excursion = await repo.get(excursion_id)
    if excursion is None:
        raise NotFoundError("No such excursion.")
    return await repo.acknowledge(excursion, user_id=staff.id, at=clock.now())
