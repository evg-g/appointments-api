"""Per-principal rate limiting with a fixed window (spec §5).

The rule: a principal may make at most ``limit`` requests per ``window`` seconds. We count requests
in Redis with ``INCR``; the first request in a window also sets the key's TTL, so the window runs
from the first hit and the count resets automatically when it expires.

A fixed window is the simplest correct algorithm and the easiest to explain: it can allow a short
burst at a window boundary (up to about twice the limit across two adjacent windows), an acceptable
trade-off here that the ADR calls out. The counting primitive sits behind a Protocol so unit tests
use ``fakeredis`` and the app uses the real client.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class RateLimitStore(Protocol):
    async def incr_with_ttl(self, key: str, window_seconds: int) -> tuple[int, int]:
        """Increment ``key`` and return ``(count_in_window, seconds_until_reset)``.

        The store must set the TTL to ``window_seconds`` on the first increment and leave it alone
        afterwards, so the window is anchored to the first request.
        """
        ...


@dataclass(frozen=True, slots=True)
class RateLimitDecision:
    allowed: bool
    limit: int
    remaining: int
    reset_seconds: int  # seconds until the window resets (for RateLimit-Reset)
    retry_after: int  # seconds the client should wait before retrying (only meaningful on a block)


class FixedWindowRateLimiter:
    def __init__(self, store: RateLimitStore, *, limit: int, window_seconds: int) -> None:
        self._store = store
        self._limit = limit
        self._window = window_seconds

    async def check(self, principal: str) -> RateLimitDecision:
        count, ttl = await self._store.incr_with_ttl(f"rl:{principal}", self._window)
        # A new key can briefly report ttl -1/-2 before EXPIRE lands; clamp to the window.
        reset = ttl if ttl >= 0 else self._window
        remaining = max(0, self._limit - count)
        allowed = count <= self._limit
        return RateLimitDecision(
            allowed=allowed,
            limit=self._limit,
            remaining=remaining,
            reset_seconds=reset,
            retry_after=reset if not allowed else 0,
        )
