"""Appointment repository. Also implements the availability ``BusyPeriodRepository`` protocol."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from appointments_api.api.pagination import Cursor
from appointments_api.enums import AppointmentStatus
from appointments_api.models import Appointment
from appointments_api.repositories.keyset import keyset_page
from appointments_api.services.availability import TimeInterval

# Statuses that still occupy a clinician's calendar (mirror of the DB exclusion constraint's WHERE).
_LIVE_STATUSES = (
    AppointmentStatus.REQUESTED,
    AppointmentStatus.CONFIRMED,
    AppointmentStatus.COMPLETED,
)


class AppointmentRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def get(self, appointment_id: uuid.UUID) -> Appointment | None:
        return await self._s.get(Appointment, appointment_id)

    async def add(self, appointment: Appointment) -> Appointment:
        self._s.add(appointment)
        await self._s.flush()
        return appointment

    async def flush(self) -> None:
        """Flush pending changes so an UPDATE is emitted now.

        This makes the optimistic-lock ``version`` bump visible on the in-memory object (for the
        response ETag) and surfaces a lost-update race as ``StaleDataError`` inside the request,
        where the error handler can turn it into ``412`` instead of a late 500 at commit time.
        """
        await self._s.flush()

    async def list_for_patient(
        self, patient_id: uuid.UUID, *, limit: int, cursor: Cursor | None
    ) -> tuple[list[Appointment], bool]:
        stmt = select(Appointment).where(Appointment.patient_id == patient_id)
        return await keyset_page(self._s, stmt, Appointment, limit=limit, cursor=cursor)

    async def list_for_clinic(
        self, clinic_id: uuid.UUID, *, limit: int, cursor: Cursor | None
    ) -> tuple[list[Appointment], bool]:
        stmt = select(Appointment).where(Appointment.clinic_id == clinic_id)
        return await keyset_page(self._s, stmt, Appointment, limit=limit, cursor=cursor)

    async def list_all(
        self, *, limit: int, cursor: Cursor | None
    ) -> tuple[list[Appointment], bool]:
        return await keyset_page(
            self._s, select(Appointment), Appointment, limit=limit, cursor=cursor
        )

    async def busy_intervals(
        self, clinician_id: str, start: datetime, end: datetime
    ) -> Sequence[TimeInterval]:
        """Live appointments for a clinician overlapping [start, end), as UTC intervals.

        ``clinician_id`` is a string (the availability protocol is storage-agnostic); we parse it
        back to a UUID for the query.
        """
        cid = uuid.UUID(clinician_id)
        stmt = select(Appointment).where(
            Appointment.clinician_id == cid,
            Appointment.status.in_(_LIVE_STATUSES),
            Appointment.starts_at < end,
            Appointment.ends_at > start,
        )
        rows = (await self._s.execute(stmt)).scalars().all()
        return [TimeInterval(start=a.starts_at, end=a.ends_at) for a in rows]
