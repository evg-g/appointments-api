"""Redis-backed rate-limit counter implementing ``services.rate_limit.RateLimitStore``."""

from __future__ import annotations

from redis.asyncio import Redis


class RedisRateLimitStore:
    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def incr_with_ttl(self, key: str, window_seconds: int) -> tuple[int, int]:
        # One round-trip: INCR, set EXPIRE only when the key is new (nx), then read the TTL. The
        # NX guard means later requests in the window never push the reset time forward.
        async with self._r.pipeline(transaction=True) as pipe:
            pipe.incr(key)
            pipe.expire(key, window_seconds, nx=True)
            pipe.ttl(key)
            count, _expire_set, ttl = await pipe.execute()
        return int(count), int(ttl)
