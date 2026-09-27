"""Clinician repository, including working-hours loading for availability."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from appointments_api.api.pagination import Cursor
from appointments_api.models import Clinician
from appointments_api.repositories.keyset import keyset_page
from appointments_api.services.availability import WorkingWindow


class ClinicianRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, clinician_id: uuid.UUID) -> Clinician | None:
        stmt = (
            select(Clinician)
            .where(Clinician.id == clinician_id)
            .options(selectinload(Clinician.working_hours))
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def get_by_user(self, user_id: uuid.UUID) -> Clinician | None:
        stmt = (
            select(Clinician)
            .where(Clinician.user_id == user_id)
            .options(selectinload(Clinician.working_hours))
        )
        return (await self._s.execute(stmt)).scalar_one_or_none()

    async def add(self, clinician: Clinician) -> Clinician:
        self._s.add(clinician)
        await self._s.flush()
        return clinician

    async def list_by_clinic(
        self, clinic_id: uuid.UUID, *, limit: int, cursor: Cursor | None
    ) -> tuple[list[Clinician], bool]:
        stmt = (
            select(Clinician)
            .where(Clinician.clinic_id == clinic_id)
            .options(selectinload(Clinician.working_hours))
        )
        return await keyset_page(self._s, stmt, Clinician, limit=limit, cursor=cursor)

    async def working_windows(self, clinician: Clinician) -> list[WorkingWindow]:
        """Map the clinician's stored working hours to availability value objects."""
        return [
            WorkingWindow(weekday=wh.weekday, start=wh.start_time, end=wh.end_time)
            for wh in clinician.working_hours
        ]
