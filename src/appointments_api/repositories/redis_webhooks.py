"""Redis-backed webhook queues implementing the ``services.webhooks.queue`` ports.

- Events: a plain list, pushed with LPUSH and popped with a blocking BRPOP (FIFO).
- Retries: a sorted set keyed by due-time; ``due_jobs`` reads the members whose score is ``<= now``
  and removes each with ZREM, so if two workers race, only the ZREM that returns 1 wins the job.
- Dead letters: a list we only ever append to.
"""

from __future__ import annotations

from typing import cast

from redis.asyncio import Redis

from appointments_api.services.webhooks.events import WebhookEvent, event_from_json, event_to_json
from appointments_api.services.webhooks.queue import DeliveryJob, job_from_json, job_to_json


class RedisEventQueue:
    KEY = "webhook:events"

    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def publish(self, event: WebhookEvent) -> None:
        # redis-py types list commands as a sync|async union; the async client returns awaitables.
        await self._r.lpush(self.KEY, event_to_json(event))  # type: ignore[misc]

    async def next_event(self, timeout_seconds: float) -> WebhookEvent | None:
        # BRPOP accepts a single key and a fractional timeout at runtime; the stubs are stricter.
        result = await self._r.brpop(self.KEY, timeout_seconds)  # type: ignore[misc, arg-type]
        if result is None:
            return None
        _key, value = cast("tuple[str, str]", result)
        return event_from_json(value)


class RedisDeliveryQueue:
    RETRIES = "webhook:retries"
    DEAD = "webhook:dead"

    def __init__(self, redis: Redis) -> None:
        self._r = redis

    async def schedule(self, job: DeliveryJob, due_at: float) -> None:
        await self._r.zadd(self.RETRIES, {job_to_json(job): due_at})

    async def due_jobs(self, now: float, limit: int) -> list[DeliveryJob]:
        members = await self._r.zrangebyscore(self.RETRIES, min=0, max=now, start=0, num=limit)
        jobs: list[DeliveryJob] = []
        for member in cast("list[str]", members):
            # ZREM returns the number removed: 1 means we claimed this job, 0 means another worker
            # already took it. Only process what we actually claimed.
            if await self._r.zrem(self.RETRIES, member):
                jobs.append(job_from_json(member))
        return jobs

    async def dead_letter(self, job: DeliveryJob) -> None:
        await self._r.lpush(self.DEAD, job_to_json(job))  # type: ignore[misc]

    async def pending_count(self) -> int:
        return int(await self._r.zcard(self.RETRIES))

    async def dead_count(self) -> int:
        return int(await self._r.llen(self.DEAD))  # type: ignore[misc]
