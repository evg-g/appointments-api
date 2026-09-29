"""Excursion repository.

``replace_for_device`` is the interesting one: the ingestion service re-derives the full excursion
list from the stored series after every batch, and this method reconciles the database with that
list. It is keyed on ``started_at`` (the first out-of-band instant, stable across re-derivations),
so a breach that is still present keeps its row — and therefore its acknowledgement. Only its
``ended_at``/``peak_temperature_c`` are updated (a backfill can extend a breach or reveal its end).
Rows whose ``started_at`` no longer appears are deleted (e.g. a gap-filling backfill merged two
breaches into one).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from appointments_api.api.pagination import Cursor
from appointments_api.models import Excursion
from appointments_api.repositories.keyset import keyset_page
from appointments_api.services.telemetry.excursion import DerivedExcursion


class ExcursionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, excursion_id: uuid.UUID) -> Excursion | None:
        return await self._s.get(Excursion, excursion_id)

    async def list_for_device(
        self,
        device_id: uuid.UUID,
        *,
        limit: int,
        cursor: Cursor | None,
        open_only: bool = False,
    ) -> tuple[Sequence[Excursion], bool]:
        stmt = select(Excursion).where(Excursion.device_id == device_id)
        if open_only:
            stmt = stmt.where(Excursion.ended_at.is_(None))
        return await keyset_page(self._s, stmt, Excursion, limit=limit, cursor=cursor)

    async def open_count(self, device_id: uuid.UUID) -> int:
        stmt = (
            select(func.count())
            .select_from(Excursion)
            .where(Excursion.device_id == device_id, Excursion.ended_at.is_(None))
        )
        return int((await self._s.execute(stmt)).scalar_one())

    async def acknowledge(
        self, excursion: Excursion, *, user_id: uuid.UUID, at: datetime
    ) -> Excursion:
        excursion.acknowledged_by = user_id
        excursion.acknowledged_at = at
        await self._s.flush()
        return excursion

    async def replace_for_device(
        self, device_id: uuid.UUID, derived: list[DerivedExcursion]
    ) -> None:
        stmt = select(Excursion).where(Excursion.device_id == device_id)
        existing = list((await self._s.execute(stmt)).scalars().all())
        by_start: dict[datetime, Excursion] = {row.started_at: row for row in existing}
        derived_starts: set[datetime] = set()

        for d in derived:
            derived_starts.add(d.started_at)
            row = by_start.get(d.started_at)
            if row is None:
                self._s.add(
                    Excursion(
                        device_id=device_id,
                        started_at=d.started_at,
                        ended_at=d.ended_at,
                        direction=d.direction,
                        peak_temperature_c=d.peak_temperature_c,
                    )
                )
            else:
                # Preserve acknowledgement; only the breach's span/peak may have moved.
                row.ended_at = d.ended_at
                row.direction = d.direction
                row.peak_temperature_c = d.peak_temperature_c

        for row in existing:
            if row.started_at not in derived_starts:
                await self._s.delete(row)

        await self._s.flush()
