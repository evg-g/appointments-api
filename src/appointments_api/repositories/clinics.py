"""Clinic repository."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from appointments_api.api.pagination import Cursor
from appointments_api.models import Clinic
from appointments_api.repositories.keyset import keyset_page


class ClinicRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, clinic_id: uuid.UUID) -> Clinic | None:
        return await self._s.get(Clinic, clinic_id)

    async def add(self, clinic: Clinic) -> Clinic:
        self._s.add(clinic)
        await self._s.flush()
        return clinic

    async def list(self, *, limit: int, cursor: Cursor | None) -> tuple[list[Clinic], bool]:
        return await keyset_page(self._s, select(Clinic), Clinic, limit=limit, cursor=cursor)
