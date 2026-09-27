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
from appointments_api.models import User
from appointments_api.repositories.appointments import AppointmentRepository
from appointments_api.repositories.clinicians import ClinicianRepository
from appointments_api.repositories.clinics import ClinicRepository
from appointments_api.repositories.redis_tokens import RedisRefreshTokenStore
from appointments_api.repositories.services import ServiceRepository
from appointments_api.repositories.users import UserRepository
from appointments_api.security import TokenError, decode_access_token
from appointments_api.services.clock import Clock, SystemClock
from appointments_api.services.tokens import TokenService

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


def get_token_service(redis: RedisDep, settings: SettingsDep) -> TokenService:
    store = RedisRefreshTokenStore(redis)
    return TokenService(store, ttl_seconds=settings.refresh_token_ttl_seconds)


def get_clock() -> Clock:
    return SystemClock()


ClockDep = Annotated[Clock, Depends(get_clock)]


UserRepoDep = Annotated[UserRepository, Depends(get_user_repository)]
ClinicRepoDep = Annotated[ClinicRepository, Depends(get_clinic_repository)]
ClinicianRepoDep = Annotated[ClinicianRepository, Depends(get_clinician_repository)]
ServiceRepoDep = Annotated[ServiceRepository, Depends(get_service_repository)]
AppointmentRepoDep = Annotated[AppointmentRepository, Depends(get_appointment_repository)]
TokenServiceDep = Annotated[TokenService, Depends(get_token_service)]


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
