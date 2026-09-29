"""Composition root for the telemetry ingestion service.

Both entry points — the HTTP batch endpoint (via ``api.deps``) and the MQTT worker — build the same
``TelemetryIngestionService`` from the same concrete adapters, so the two transports genuinely
funnel into one service and one test suite covers both (spec §5). Keeping the wiring here means
neither the router nor the worker duplicates it.
"""

from __future__ import annotations

from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from appointments_api.config import Settings
from appointments_api.repositories.devices import DeviceRepository, IngestionDevices
from appointments_api.repositories.excursions import ExcursionRepository
from appointments_api.repositories.redis_telemetry_stream import RedisTelemetryStream
from appointments_api.repositories.telemetry import TelemetryReadingRepository
from appointments_api.repositories.threshold_policies import (
    IngestionPolicies,
    ThresholdPolicyRepository,
)
from appointments_api.services.clock import Clock
from appointments_api.services.telemetry.ingestion import SkewPolicy, TelemetryIngestionService


def build_ingestion_service(
    *,
    session: AsyncSession,
    redis: Redis,
    settings: Settings,
    clock: Clock,
) -> TelemetryIngestionService:
    """Assemble the ingestion service from the SQL adapters and the live SSE publisher."""
    return TelemetryIngestionService(
        devices=IngestionDevices(DeviceRepository(session)),
        readings=TelemetryReadingRepository(session),
        policies=IngestionPolicies(ThresholdPolicyRepository(session)),
        excursions=ExcursionRepository(session),
        clock=clock,
        skew=SkewPolicy(
            max_skew_seconds=settings.telemetry_max_skew_seconds,
            future_tolerance_seconds=settings.telemetry_future_tolerance_seconds,
        ),
        publisher=RedisTelemetryStream(redis),
    )
