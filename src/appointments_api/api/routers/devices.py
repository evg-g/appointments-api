"""Device provisioning, telemetry ingestion (HTTP fallback path), and time-series/health reads.

The batch endpoint ``POST /devices/{id}/telemetry:batch`` is the HTTP fallback to the MQTT worker;
both funnel into the same ``TelemetryIngestionService`` (spec §5). Devices authenticate to it with
their per-device secret (``X-Device-Secret``), issued once at provisioning and rotatable. The read
endpoints (list, get, time-series, health, excursions) use the normal user JWT with staff roles.
"""

from __future__ import annotations

import secrets
import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, status

from appointments_api.api.deps import (
    AuthenticatedDevice,
    DeviceRepoDep,
    ExcursionRepoDep,
    IngestionServiceDep,
    PageParams,
    TelemetryRepoDep,
    require_roles,
)
from appointments_api.api.errors import NotFoundError, ValidationProblem
from appointments_api.api.pagination import Page, build_page
from appointments_api.api.schemas import (
    DeviceCreate,
    DeviceCredentialOut,
    DeviceHealthOut,
    DeviceOut,
    ExcursionOut,
    TelemetryBatchIn,
    TelemetryBatchItemResult,
    TelemetryBatchResult,
    TimeSeriesOut,
    TimeSeriesPoint,
)
from appointments_api.enums import UserRole
from appointments_api.models import Device, User
from appointments_api.security import hash_password
from appointments_api.services.telemetry.contract import SUPPORTED_SCHEMA_VERSIONS
from appointments_api.services.telemetry.ingestion import ReadingInput

router = APIRouter(prefix="/devices", tags=["devices"])

AdminUser = Annotated[User, Depends(require_roles(UserRole.CLINIC_ADMIN, UserRole.PLATFORM_ADMIN))]
ReaderUser = Annotated[
    User,
    Depends(require_roles(UserRole.CLINICIAN, UserRole.CLINIC_ADMIN, UserRole.PLATFORM_ADMIN)),
]

_BUCKET_UNITS = {"s": 1, "m": 60, "h": 3600, "d": 86400}
_MAX_BUCKET_SECONDS = 31 * 86400


def _parse_bucket(value: str) -> int:
    """Parse a duration like ``5m`` / ``30s`` / ``1h`` / ``1d`` into seconds."""
    text = value.strip().lower()
    if len(text) < 2 or text[-1] not in _BUCKET_UNITS or not text[:-1].isdigit():
        raise ValidationProblem("bucket must look like '30s', '5m', '1h', or '1d'.")
    magnitude = int(text[:-1])
    if magnitude <= 0:
        raise ValidationProblem("bucket must be a positive duration.")
    seconds = magnitude * _BUCKET_UNITS[text[-1]]
    if seconds > _MAX_BUCKET_SECONDS:
        raise ValidationProblem("bucket may not exceed 31 days.")
    return seconds


def _new_secret() -> str:
    return secrets.token_urlsafe(32)


@router.post("", response_model=DeviceCredentialOut, status_code=status.HTTP_201_CREATED)
async def provision_device(
    body: DeviceCreate,
    _admin: AdminUser,
    repo: DeviceRepoDep,
) -> DeviceCredentialOut:
    """Register a device and issue its telemetry credential (returned once)."""
    secret = _new_secret()
    device = Device(
        clinic_id=body.clinic_id,
        location_label=body.location_label,
        hardware_version=body.hardware_version,
        firmware_version=body.firmware_version,
        secret_hash=hash_password(secret),
    )
    await repo.add(device)
    return DeviceCredentialOut(device=DeviceOut.model_validate(device), secret=secret)


@router.post("/{device_id}/credentials:rotate", response_model=DeviceCredentialOut)
async def rotate_credentials(
    device_id: uuid.UUID,
    _admin: AdminUser,
    repo: DeviceRepoDep,
) -> DeviceCredentialOut:
    """Rotate a device's telemetry secret. The old secret stops working immediately."""
    device = await repo.get(device_id)
    if device is None:
        raise NotFoundError("No such device.")
    secret = _new_secret()
    await repo.set_secret_hash(device, hash_password(secret))
    return DeviceCredentialOut(device=DeviceOut.model_validate(device), secret=secret)


@router.get("", response_model=Page[DeviceOut])
async def list_devices(
    _reader: ReaderUser,
    repo: DeviceRepoDep,
    page: PageParams,
    clinic_id: Annotated[uuid.UUID | None, Query()] = None,
) -> Page[DeviceOut]:
    limit, cursor = page
    rows, has_more = await repo.list(limit=limit, cursor=cursor, clinic_id=clinic_id)
    return build_page(rows, has_more, DeviceOut.model_validate)


@router.get("/{device_id}", response_model=DeviceOut)
async def get_device(
    device_id: uuid.UUID,
    _reader: ReaderUser,
    repo: DeviceRepoDep,
) -> Device:
    device = await repo.get(device_id)
    if device is None:
        raise NotFoundError("No such device.")
    return device


@router.post("/{device_id}/telemetry:batch", response_model=TelemetryBatchResult)
async def ingest_telemetry_batch(
    device_id: uuid.UUID,
    body: TelemetryBatchIn,
    device: AuthenticatedDevice,
    service: IngestionServiceDep,
) -> TelemetryBatchResult:
    """Bulk, idempotent telemetry ingestion with a per-item result array (spec §5).

    Duplicate readings (by ``sequence``) are dropped silently, clock-skewed readings are stored and
    flagged, future readings are rejected — each item's fate is reported back individually.
    """
    if body.schema_version not in SUPPORTED_SCHEMA_VERSIONS:
        raise ValidationProblem(
            f"Unsupported telemetry schema version {body.schema_version}; "
            f"this API accepts {sorted(SUPPORTED_SCHEMA_VERSIONS)}."
        )
    items = [
        ReadingInput(
            sequence=item.sequence,
            measured_at=item.reading.measured_at,
            temperature_c=item.reading.temperature_c,
            humidity_pct=item.reading.humidity_pct,
            battery_pct=item.reading.battery_pct,
        )
        for item in body.readings
    ]
    outcome = await service.ingest(device.id, items)
    return TelemetryBatchResult(
        device_id=device.id,
        accepted=outcome.accepted,
        duplicates=outcome.duplicates,
        rejected=outcome.rejected,
        open_excursions=outcome.open_excursions,
        results=[
            TelemetryBatchItemResult(sequence=r.sequence, status=r.status, detail=r.detail)
            for r in outcome.results
        ],
    )


@router.get("/{device_id}/telemetry", response_model=TimeSeriesOut)
async def device_time_series(
    device_id: uuid.UUID,
    _reader: ReaderUser,
    devices: DeviceRepoDep,
    telemetry: TelemetryRepoDep,
    bucket: Annotated[str, Query()] = "5m",
    agg: Annotated[Literal["avg", "min", "max"], Query()] = "avg",
    start: Annotated[str | None, Query()] = None,
    end: Annotated[str | None, Query()] = None,
) -> TimeSeriesOut:
    """Downsampled temperature series: ``?bucket=5m&agg=avg|min|max`` with optional start/end."""
    device = await devices.get(device_id)
    if device is None:
        raise NotFoundError("No such device.")
    bucket_seconds = _parse_bucket(bucket)
    start_dt = _parse_instant("start", start)
    end_dt = _parse_instant("end", end)
    rows = await telemetry.bucketed(
        device_id, bucket_seconds=bucket_seconds, agg=agg, start=start_dt, end=end_dt
    )
    points = [
        TimeSeriesPoint(
            bucket_start=row.bucket, value=float(row.value), sample_count=row.sample_count
        )
        for row in rows
    ]
    return TimeSeriesOut(device_id=device_id, bucket_seconds=bucket_seconds, agg=agg, points=points)


@router.get("/{device_id}/health", response_model=DeviceHealthOut)
async def device_health(
    device_id: uuid.UUID,
    _reader: ReaderUser,
    devices: DeviceRepoDep,
    telemetry: TelemetryRepoDep,
    excursions: ExcursionRepoDep,
) -> DeviceHealthOut:
    device = await devices.get(device_id)
    if device is None:
        raise NotFoundError("No such device.")
    latest = await telemetry.latest_for_device(device_id)
    return DeviceHealthOut(
        device_id=device_id,
        status=device.status,
        last_seen_at=device.last_seen_at,
        last_temperature_c=latest.temperature_c if latest else None,
        last_battery_pct=latest.battery_pct if latest else None,
        open_excursions=await excursions.open_count(device_id),
    )


@router.get("/{device_id}/excursions", response_model=Page[ExcursionOut])
async def list_device_excursions(
    device_id: uuid.UUID,
    _reader: ReaderUser,
    repo: ExcursionRepoDep,
    page: PageParams,
    open_only: Annotated[bool, Query()] = False,
) -> Page[ExcursionOut]:
    limit, cursor = page
    rows, has_more = await repo.list_for_device(
        device_id, limit=limit, cursor=cursor, open_only=open_only
    )
    return build_page(rows, has_more, ExcursionOut.model_validate)


def _parse_instant(name: str, value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise ValidationProblem(f"{name} must be an ISO-8601 timestamp.") from exc
    if parsed.tzinfo is None:
        raise ValidationProblem(f"{name} must be timezone-aware.")
    return parsed
