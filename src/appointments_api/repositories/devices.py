"""Device repository (ORM CRUD for the routers) plus the ingestion ``Devices`` adapter.

``DeviceRepository`` is what the device/health routers use — it returns ORM ``Device`` rows.
``IngestionDevices`` adapts it to the ingestion service's ``Devices`` protocol, handing back the
framework-free ``DeviceView`` and refreshing ``last_seen_at`` (promoting a first-reporting device
from PROVISIONED to ACTIVE).
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from appointments_api.api.pagination import Cursor
from appointments_api.enums import DeviceStatus
from appointments_api.models import Device
from appointments_api.repositories.keyset import keyset_page
from appointments_api.services.telemetry.ingestion import DeviceView


class DeviceRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, device: Device) -> Device:
        self._s.add(device)
        await self._s.flush()
        return device

    async def get(self, device_id: uuid.UUID) -> Device | None:
        return await self._s.get(Device, device_id)

    async def list(
        self,
        *,
        limit: int,
        cursor: Cursor | None,
        clinic_id: uuid.UUID | None = None,
    ) -> tuple[Sequence[Device], bool]:
        stmt = select(Device)
        if clinic_id is not None:
            stmt = stmt.where(Device.clinic_id == clinic_id)
        return await keyset_page(self._s, stmt, Device, limit=limit, cursor=cursor)

    async def set_secret_hash(self, device: Device, secret_hash: str) -> None:
        device.secret_hash = secret_hash
        await self._s.flush()

    async def set_status(self, device: Device, status: DeviceStatus) -> None:
        device.status = status
        await self._s.flush()


class IngestionDevices:
    """Adapts ``DeviceRepository`` to the ingestion service's ``Devices`` protocol."""

    def __init__(self, repo: DeviceRepository) -> None:
        self._repo = repo

    async def get(self, device_id: uuid.UUID) -> DeviceView | None:
        device = await self._repo.get(device_id)
        if device is None:
            return None
        return DeviceView(id=device.id, clinic_id=device.clinic_id, status=device.status)

    async def touch_last_seen(self, device_id: uuid.UUID, at: datetime) -> None:
        device = await self._repo.get(device_id)
        if device is None:
            return
        device.last_seen_at = at
        if device.status is DeviceStatus.PROVISIONED:
            device.status = DeviceStatus.ACTIVE
