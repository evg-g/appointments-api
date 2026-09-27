"""Authentication endpoints: login, refresh (with rotation), logout, and whoami."""

from __future__ import annotations

from fastapi import APIRouter, Response, status

from appointments_api.api.deps import CurrentUser, SettingsDep, TokenServiceDep, UserRepoDep
from appointments_api.api.errors import UnauthorizedError
from appointments_api.api.schemas import LoginRequest, RefreshRequest, TokenResponse, UserOut
from appointments_api.models import User
from appointments_api.security import create_access_token, verify_password
from appointments_api.services.tokens import InvalidRefreshTokenError, RefreshTokenReuseError

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    users: UserRepoDep,
    tokens: TokenServiceDep,
    settings: SettingsDep,
) -> TokenResponse:
    user = await users.get_by_email(body.email)
    # Same error whether the email is unknown or the password is wrong: do not leak which.
    if (
        user is None
        or user.hashed_password is None
        or not verify_password(user.hashed_password, body.password)
    ):
        raise UnauthorizedError("Invalid email or password.")
    if not user.is_active:
        raise UnauthorizedError("Account is inactive.")
    access = create_access_token(user_id=user.id, role=user.role, settings=settings)
    refresh = await tokens.issue(user.id)
    return TokenResponse(
        access_token=access,
        refresh_token=refresh,
        expires_in=settings.access_token_ttl_seconds,
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(
    body: RefreshRequest,
    users: UserRepoDep,
    tokens: TokenServiceDep,
    settings: SettingsDep,
) -> TokenResponse:
    try:
        user_id, new_refresh = await tokens.rotate(body.refresh_token)
    except (InvalidRefreshTokenError, RefreshTokenReuseError) as exc:
        raise UnauthorizedError("Refresh token is invalid or has been revoked.") from exc
    user = await users.get(user_id)
    if user is None or not user.is_active:
        raise UnauthorizedError("User no longer exists or is inactive.")
    access = create_access_token(user_id=user.id, role=user.role, settings=settings)
    return TokenResponse(
        access_token=access,
        refresh_token=new_refresh,
        expires_in=settings.access_token_ttl_seconds,
    )


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT, response_class=Response)
async def logout(body: RefreshRequest, tokens: TokenServiceDep) -> Response:
    await tokens.revoke(body.refresh_token)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/me", response_model=UserOut)
async def me(current: CurrentUser) -> User:
    return current
