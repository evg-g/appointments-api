"""ThresholdPolicy repository, plus the ingestion ``Policies`` adapter.

Resolution is "most specific wins": a device's own policy overrides its clinic's policy. If neither
exists, the device has no band configured and the excursion engine is simply not run for it.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from appointments_api.models import ThresholdPolicy
from appointments_api.services.telemetry.excursion import ThresholdBand


class ThresholdPolicyRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, policy: ThresholdPolicy) -> ThresholdPolicy:
        self._s.add(policy)
        await self._s.flush()
        return policy

    async def get_for_device(self, device_id: uuid.UUID) -> ThresholdPolicy | None:
        stmt = select(ThresholdPolicy).where(ThresholdPolicy.device_id == device_id)
        return (await self._s.execute(stmt)).scalars().first()

    async def get_for_clinic(self, clinic_id: uuid.UUID) -> ThresholdPolicy | None:
        stmt = select(ThresholdPolicy).where(ThresholdPolicy.clinic_id == clinic_id)
        return (await self._s.execute(stmt)).scalars().first()

    async def resolve_band(
        self, device_id: uuid.UUID, clinic_id: uuid.UUID
    ) -> ThresholdBand | None:
        policy = await self.get_for_device(device_id)
        if policy is None:
            policy = await self.get_for_clinic(clinic_id)
        if policy is None:
            return None
        return ThresholdBand(
            min_temperature_c=policy.min_temperature_c,
            max_temperature_c=policy.max_temperature_c,
            dwell_minutes=policy.dwell_minutes,
            recovery_minutes=policy.recovery_minutes,
        )


class IngestionPolicies:
    """Adapts ``ThresholdPolicyRepository`` to the ingestion service's ``Policies`` protocol."""

    def __init__(self, repo: ThresholdPolicyRepository) -> None:
        self._repo = repo

    async def band_for_device(
        self, device_id: uuid.UUID, clinic_id: uuid.UUID
    ) -> ThresholdBand | None:
        return await self._repo.resolve_band(device_id, clinic_id)
