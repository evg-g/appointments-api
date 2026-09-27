"""Redis-backed refresh-token store implementing ``services.tokens.RefreshTokenStore``."""

from __future__ import annotations

import json
import uuid

from redis.asyncio import Redis

from appointments_api.services.tokens import RefreshRecord


class RedisRefreshTokenStore:
    """Keys: ``rt:<token>`` (active), ``rtused:<token>`` (consumed marker), ``rtfam:<family>``
    (the set of tokens in a family, used to revoke it wholesale)."""

    def __init__(self, redis: Redis) -> None:
        self._r = redis

    @staticmethod
    def _active(token: str) -> str:
        return f"rt:{token}"

    @staticmethod
    def _used(token: str) -> str:
        return f"rtused:{token}"

    @staticmethod
    def _family(family: str) -> str:
        return f"rtfam:{family}"

    async def store(self, token: str, record: RefreshRecord, ttl_seconds: int) -> None:
        payload = json.dumps({"user_id": str(record.user_id), "family": record.family})
        await self._r.set(self._active(token), payload, ex=ttl_seconds)
        # redis-py types set-commands as sync|async unions; the async client returns awaitables.
        await self._r.sadd(self._family(record.family), token)  # type: ignore[misc]
        await self._r.expire(self._family(record.family), ttl_seconds)

    async def get(self, token: str) -> RefreshRecord | None:
        raw = await self._r.get(self._active(token))
        if raw is None:
            return None
        data = json.loads(raw)
        return RefreshRecord(user_id=uuid.UUID(data["user_id"]), family=data["family"])

    async def consume(self, token: str, family: str, used_ttl_seconds: int) -> None:
        await self._r.delete(self._active(token))
        await self._r.set(self._used(token), family, ex=used_ttl_seconds)

    async def family_of_consumed(self, token: str) -> str | None:
        value: str | None = await self._r.get(self._used(token))
        return value

    async def revoke_family(self, family: str) -> None:
        members: set[str] = await self._r.smembers(self._family(family))  # type: ignore[misc]
        keys = [self._active(t) for t in members]
        keys.append(self._family(family))
        if keys:
            await self._r.delete(*keys)
