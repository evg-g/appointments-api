"""Shared FastAPI dependencies: settings, repositories, auth, RBAC, pagination params.

Everything a router needs arrives via ``Annotated[..., Depends(...)]`` aliases defined here, so
routers stay declarative and tests can override any single dependency.
"""

from __future__ import annotations

import uuid
from collections.abc import Awaitable, Callable
from typing import Annotated

from fastapi import Depends, Header, Query
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession

from appointments_api.api.errors import ForbiddenError, UnauthorizedError
from appointments_api.api.pagination import Cursor, decode_cursor
from appointments_api.config import Settings, get_settings
from appointments_api.db import get_redis, get_session
from appointments_api.enums import UserRole
from appointments_api.models import Device, User
from appointments_api.repositories.appointments import AppointmentRepository
from appointments_api.repositories.audit_log import AuditLogRepository
from appointments_api.repositories.clinicians import ClinicianRepository
from appointments_api.repositories.clinics import ClinicRepository
from appointments_api.repositories.devices import DeviceRepository
from appointments_api.repositories.excursions import ExcursionRepository
from appointments_api.repositories.redis_idempotency import RedisIdempotencyStore
from appointments_api.repositories.redis_telemetry_stream import RedisTelemetryStream
from appointments_api.repositories.redis_tokens import RedisRefreshTokenStore
from appointments_api.repositories.redis_webhooks import RedisEventQueue
from appointments_api.repositories.services import ServiceRepository
from appointments_api.repositories.telemetry import TelemetryReadingRepository
from appointments_api.repositories.threshold_policies import ThresholdPolicyRepository
from appointments_api.repositories.users import UserRepository
from appointments_api.repositories.webhooks import WebhookSubscriptionRepository
from appointments_api.security import TokenError, decode_access_token, verify_password
from appointments_api.services.clock import Clock, SystemClock
from appointments_api.services.idempotency import IdempotencyService
from appointments_api.services.telemetry.ingestion import TelemetryIngestionService
from appointments_api.services.tokens import TokenService
from appointments_api.services.webhooks.dispatcher import WebhookDispatcher
from appointments_api.telemetry_wiring import build_ingestion_service

SessionDep = Annotated[AsyncSession, Depends(get_session)]
RedisDep = Annotated[Redis, Depends(get_redis)]
SettingsDep = Annotated[Settings, Depends(get_settings)]


def get_user_repository(session: SessionDep) -> UserRepository:
    return UserRepository(session)


def get_clinic_repository(session: SessionDep) -> ClinicRepository:
    return ClinicRepository(session)


def get_clinician_repository(session: SessionDep) -> ClinicianRepository:
    return ClinicianRepository(session)


def get_service_repository(session: SessionDep) -> ServiceRepository:
    return ServiceRepository(session)


def get_appointment_repository(session: SessionDep) -> AppointmentRepository:
    return AppointmentRepository(session)


def get_audit_log_repository(session: SessionDep) -> AuditLogRepository:
    return AuditLogRepository(session)


def get_token_service(redis: RedisDep, settings: SettingsDep) -> TokenService:
    store = RedisRefreshTokenStore(redis)
    return TokenService(store, ttl_seconds=settings.refresh_token_ttl_seconds)


def get_idempotency_service(redis: RedisDep, settings: SettingsDep) -> IdempotencyService:
    store = RedisIdempotencyStore(redis)
    return IdempotencyService(store, ttl_seconds=settings.idempotency_ttl_seconds)


def get_webhook_subscription_repository(session: SessionDep) -> WebhookSubscriptionRepository:
    return WebhookSubscriptionRepository(session)


def get_webhook_dispatcher(redis: RedisDep) -> WebhookDispatcher:
    return WebhookDispatcher(RedisEventQueue(redis))


def get_clock() -> Clock:
    return SystemClock()


ClockDep = Annotated[Clock, Depends(get_clock)]


# ---- telemetry (milestone 10) ----


def get_device_repository(session: SessionDep) -> DeviceRepository:
    return DeviceRepository(session)


def get_telemetry_repository(session: SessionDep) -> TelemetryReadingRepository:
    return TelemetryReadingRepository(session)


def get_threshold_policy_repository(session: SessionDep) -> ThresholdPolicyRepository:
    return ThresholdPolicyRepository(session)


def get_excursion_repository(session: SessionDep) -> ExcursionRepository:
    return ExcursionRepository(session)


def get_telemetry_stream(redis: RedisDep) -> RedisTelemetryStream:
    return RedisTelemetryStream(redis)


def get_ingestion_service(
    session: SessionDep,
    redis: RedisDep,
    settings: SettingsDep,
    clock: ClockDep,
) -> TelemetryIngestionService:
    """Assemble the ingestion service (shared with the MQTT worker via the composition root)."""
    return build_ingestion_service(session=session, redis=redis, settings=settings, clock=clock)


async def get_authenticated_device(
    device_id: uuid.UUID,
    devices: Annotated[DeviceRepository, Depends(get_device_repository)],
    x_device_secret: Annotated[str | None, Header()] = None,
) -> Device:
    """Authenticate a device by its per-device secret (the ``X-Device-Secret`` header).

    Telemetry-posting devices carry no user JWT; they present the secret issued at provisioning. A
    missing device and a bad secret both return 401 so the endpoint does not leak which device ids
    exist.
    """
    if x_device_secret is None:
        raise UnauthorizedError("Missing X-Device-Secret header.")
    device = await devices.get(device_id)
    if device is None or not verify_password(device.secret_hash, x_device_secret):
        raise UnauthorizedError("Invalid device credentials.")
    return device


AuthenticatedDevice = Annotated[Device, Depends(get_authenticated_device)]


DeviceRepoDep = Annotated[DeviceRepository, Depends(get_device_repository)]
TelemetryRepoDep = Annotated[TelemetryReadingRepository, Depends(get_telemetry_repository)]
ThresholdPolicyRepoDep = Annotated[
    ThresholdPolicyRepository, Depends(get_threshold_policy_repository)
]
ExcursionRepoDep = Annotated[ExcursionRepository, Depends(get_excursion_repository)]
TelemetryStreamDep = Annotated[RedisTelemetryStream, Depends(get_telemetry_stream)]
IngestionServiceDep = Annotated[TelemetryIngestionService, Depends(get_ingestion_service)]


UserRepoDep = Annotated[UserRepository, Depends(get_user_repository)]
ClinicRepoDep = Annotated[ClinicRepository, Depends(get_clinic_repository)]
ClinicianRepoDep = Annotated[ClinicianRepository, Depends(get_clinician_repository)]
ServiceRepoDep = Annotated[ServiceRepository, Depends(get_service_repository)]
AppointmentRepoDep = Annotated[AppointmentRepository, Depends(get_appointment_repository)]
AuditLogRepoDep = Annotated[AuditLogRepository, Depends(get_audit_log_repository)]
TokenServiceDep = Annotated[TokenService, Depends(get_token_service)]
IdempotencyServiceDep = Annotated[IdempotencyService, Depends(get_idempotency_service)]
WebhookSubscriptionRepoDep = Annotated[
    WebhookSubscriptionRepository, Depends(get_webhook_subscription_repository)
]
WebhookDispatcherDep = Annotated[WebhookDispatcher, Depends(get_webhook_dispatcher)]


async def get_current_user(
    users: UserRepoDep,
    settings: SettingsDep,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise UnauthorizedError("Missing or malformed Authorization header.")
    token = authorization[len("bearer ") :].strip()
    try:
        claims = decode_access_token(token, settings=settings)
    except TokenError as exc:
        raise UnauthorizedError("Invalid or expired access token.") from exc
    user = await users.get(uuid.UUID(claims["sub"]))
    if user is None or not user.is_active:
        raise UnauthorizedError("User no longer exists or is inactive.")
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: UserRole) -> Callable[[User], Awaitable[User]]:
    """Build a dependency that allows only the given roles, else raises 403."""

    async def checker(user: CurrentUser) -> User:
        if user.role not in roles:
            raise ForbiddenError(f"Requires one of: {', '.join(r.value for r in roles)}.")
        return user

    return checker


def pagination_params(
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    cursor: Annotated[str | None, Query()] = None,
) -> tuple[int, Cursor | None]:
    if cursor is None:
        return limit, None
    try:
        return limit, decode_cursor(cursor)
    except ValueError as exc:
        # Surface as a validation problem rather than a 500.
        from appointments_api.api.errors import ValidationProblem

        raise ValidationProblem("The 'cursor' query parameter is malformed.") from exc


PageParams = Annotated[tuple[int, Cursor | None], Depends(pagination_params)]
