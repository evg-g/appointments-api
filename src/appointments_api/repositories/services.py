"""Service (bookable clinic service) repository."""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from appointments_api.api.pagination import Cursor
from appointments_api.models import Service
from appointments_api.repositories.keyset import keyset_page


class ServiceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, service_id: uuid.UUID) -> Service | None:
        return await self._s.get(Service, service_id)

    async def add(self, service: Service) -> Service:
        self._s.add(service)
        await self._s.flush()
        return service

    async def list_by_clinic(
        self, clinic_id: uuid.UUID, *, limit: int, cursor: Cursor | None
    ) -> tuple[list[Service], bool]:
        stmt = select(Service).where(Service.clinic_id == clinic_id)
        return await keyset_page(self._s, stmt, Service, limit=limit, cursor=cursor)
