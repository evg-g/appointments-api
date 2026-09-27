"""Refresh-token rotation with reuse detection.

Refresh tokens are opaque random strings, not JWTs, so they can be revoked. Each login starts a
token *family*. On every refresh the presented token is consumed (single-use) and a new one is
issued in the same family. If a token that was already consumed is presented again — the classic
sign of a stolen token being replayed — the whole family is revoked, logging out the attacker and
the victim together. Storage sits behind a Protocol so unit tests use a fake and integration
tests use real Redis.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True, slots=True)
class RefreshRecord:
    user_id: uuid.UUID
    family: str


class RefreshTokenReuseError(Exception):
    """A consumed refresh token was presented again; the family has been revoked."""


class InvalidRefreshTokenError(Exception):
    """The refresh token is unknown or expired."""


class RefreshTokenStore(Protocol):
    async def store(self, token: str, record: RefreshRecord, ttl_seconds: int) -> None: ...
    async def get(self, token: str) -> RefreshRecord | None: ...
    async def consume(self, token: str, family: str, used_ttl_seconds: int) -> None: ...
    async def family_of_consumed(self, token: str) -> str | None: ...
    async def revoke_family(self, family: str) -> None: ...


class TokenService:
    """Issues, rotates, and revokes refresh tokens against a :class:`RefreshTokenStore`."""

    def __init__(self, store: RefreshTokenStore, *, ttl_seconds: int) -> None:
        self._store = store
        self._ttl = ttl_seconds

    @staticmethod
    def _new_token() -> str:
        return secrets.token_urlsafe(48)

    async def issue(self, user_id: uuid.UUID) -> str:
        """Start a new family and return its first refresh token."""
        token = self._new_token()
        record = RefreshRecord(user_id=user_id, family=uuid.uuid4().hex)
        await self._store.store(token, record, self._ttl)
        return token

    async def rotate(self, token: str) -> tuple[uuid.UUID, str]:
        """Consume ``token`` and return ``(user_id, new_token)`` in the same family.

        Raises :class:`RefreshTokenReuseError` if the token was already consumed (family is then
        revoked), or :class:`InvalidRefreshTokenError` if it is unknown/expired.
        """
        record = await self._store.get(token)
        if record is None:
            family = await self._store.family_of_consumed(token)
            if family is not None:
                await self._store.revoke_family(family)
                raise RefreshTokenReuseError(token)
            raise InvalidRefreshTokenError(token)

        await self._store.consume(token, record.family, self._ttl)
        new_token = self._new_token()
        await self._store.store(new_token, record, self._ttl)
        return record.user_id, new_token

    async def revoke(self, token: str) -> None:
        """Log out: revoke the token's whole family (idempotent for unknown tokens)."""
        record = await self._store.get(token)
        family = record.family if record else await self._store.family_of_consumed(token)
        if family is not None:
            await self._store.revoke_family(family)
