"""Password hashing (Argon2) and JWT access tokens.

Pure functions over settings — no database, no framework. Access tokens are short-lived JWTs
carrying the user id and role; refresh tokens are handled separately (opaque, rotated, in Redis)
by ``services.tokens``.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError

from appointments_api.config import Settings, get_settings
from appointments_api.enums import UserRole

_hasher = PasswordHasher()


def hash_password(plain: str) -> str:
    return _hasher.hash(plain)


def verify_password(hashed: str, plain: str) -> bool:
    try:
        return _hasher.verify(hashed, plain)
    except VerifyMismatchError:
        return False


class TokenError(Exception):
    """A JWT could not be decoded, was expired, or had the wrong shape."""


def create_access_token(
    *, user_id: uuid.UUID, role: UserRole, settings: Settings | None = None
) -> str:
    settings = settings or get_settings()
    now = datetime.now(UTC)
    claims: dict[str, Any] = {
        "sub": str(user_id),
        "role": role.value,
        "type": "access",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=settings.access_token_ttl_seconds)).timestamp()),
    }
    return jwt.encode(claims, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def decode_access_token(token: str, *, settings: Settings | None = None) -> dict[str, Any]:
    settings = settings or get_settings()
    try:
        claims: dict[str, Any] = jwt.decode(
            token, settings.jwt_secret, algorithms=[settings.jwt_algorithm]
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    if claims.get("type") != "access":
        raise TokenError("not an access token")
    return claims
