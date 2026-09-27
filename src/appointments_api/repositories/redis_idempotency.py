"""Redis-backed idempotency key store implementing ``services.idempotency.IdempotencyKeyStore``.

The Redis client is created with ``decode_responses=True`` (see ``db.create_redis``), so values come
back as ``str``.
"""

from __future__ import annotations

from redis.asyncio import Redis


class RedisIdempotencyStore:
    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def put_if_absent(self, key: str, value: str, ttl_seconds: int) -> bool:
        # SET key value NX EX ttl — atomic claim; returns None if the key already existed.
        result = await self._r.set(key, value, nx=True, ex=ttl_seconds)
        return bool(result)

    async def get(self, key: str) -> str | None:
        value: str | None = await self._r.get(key)
        return value

    async def put(self, key: str, value: str, ttl_seconds: int) -> None:
        await self._r.set(key, value, ex=ttl_seconds)

    async def delete_if_matches(self, key: str, value: str) -> None:
        current: str | None = await self._r.get(key)
        if current == value:
            await self._r.delete(key)
