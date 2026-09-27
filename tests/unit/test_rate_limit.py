"""Unit tests for the fixed-window rate limiter, run over fakeredis (no container, no network)."""

from __future__ import annotations

import fakeredis.aioredis
import pytest

from appointments_api.repositories.redis_rate_limit import RedisRateLimitStore
from appointments_api.services.rate_limit import FixedWindowRateLimiter


@pytest.fixture
def limiter() -> FixedWindowRateLimiter:
    redis = fakeredis.aioredis.FakeRedis(decode_responses=True)
    return FixedWindowRateLimiter(RedisRateLimitStore(redis), limit=2, window_seconds=60)


async def test_requests_up_to_the_limit_are_allowed(limiter: FixedWindowRateLimiter) -> None:
    first = await limiter.check("alice")
    assert first.allowed
    assert first.remaining == 1

    second = await limiter.check("alice")
    assert second.allowed
    assert second.remaining == 0


async def test_request_over_the_limit_is_blocked(limiter: FixedWindowRateLimiter) -> None:
    await limiter.check("alice")
    await limiter.check("alice")
    third = await limiter.check("alice")
    assert not third.allowed
    assert third.remaining == 0
    assert third.retry_after > 0


async def test_reset_is_the_window_on_the_first_hit(limiter: FixedWindowRateLimiter) -> None:
    decision = await limiter.check("bob")
    assert decision.limit == 2
    assert 0 < decision.reset_seconds <= 60


async def test_principals_have_independent_budgets(limiter: FixedWindowRateLimiter) -> None:
    await limiter.check("x")
    await limiter.check("x")
    assert not (await limiter.check("x")).allowed
    # A different principal is unaffected.
    assert (await limiter.check("y")).allowed
