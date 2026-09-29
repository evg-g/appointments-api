"""The webhook delivery worker: fan-out, delivery, retry with backoff, dead-letter.

The worker owns everything that must not happen on the request path. One "tick" is two steps:

1. **process_retries_once** — deliver any jobs whose backoff has elapsed.
2. **process_events_once** — pop one new event, look up the subscriptions that want it, and try to
   deliver to each.

A failed delivery is rescheduled with exponential backoff; once it has used up ``max_attempts`` it
is moved to the dead-letter list instead of retrying forever. Every collaborator is injected — the
queues, the sender, the clock, the RNG — so the whole thing is unit-tested with fakes and a fixed
clock, no network and no real time.
"""

from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, replace
from random import Random
from typing import Protocol

from appointments_api.services.clock import Clock
from appointments_api.services.webhooks.backoff import backoff_delay
from appointments_api.services.webhooks.queue import DeliveryJob, DeliveryQueue, EventQueue
from appointments_api.services.webhooks.sender import DeliveryOutcome, WebhookSender


@dataclass(frozen=True, slots=True)
class SubscriptionInfo:
    id: str
    url: str
    secret: str


class SubscriptionSource(Protocol):
    async def for_event(self, event_type: str) -> list[SubscriptionInfo]: ...


class WebhookWorker:
    def __init__(
        self,
        *,
        event_queue: EventQueue,
        delivery_queue: DeliveryQueue,
        subscriptions: SubscriptionSource,
        sender: WebhookSender,
        clock: Clock,
        max_attempts: int,
        backoff_base: float,
        backoff_cap: float,
        rng: Random | None = None,
        event_poll_timeout: float = 1.0,
        retry_batch: int = 100,
    ) -> None:
        self._events = event_queue
        self._deliveries = delivery_queue
        self._subs = subscriptions
        self._sender = sender
        self._clock = clock
        self._max_attempts = max_attempts
        self._base = backoff_base
        self._cap = backoff_cap
        self._rng = rng or Random()
        self._poll = event_poll_timeout
        self._batch = retry_batch

    async def process_events_once(self) -> int:
        """Pop at most one event and try to deliver it to every matching subscription.

        Returns 1 if an event was handled, 0 if the poll timed out with nothing waiting.
        """
        event = await self._events.next_event(self._poll)
        if event is None:
            return 0
        for sub in await self._subs.for_event(event.type):
            job = DeliveryJob(
                id=uuid.uuid4().hex,
                subscription_id=sub.id,
                url=sub.url,
                secret=sub.secret,
                event=event,
                attempt=0,
            )
            await self._attempt(job)
        return 1

    async def process_retries_once(self) -> int:
        """Deliver every job whose backoff has elapsed. Returns how many were tried."""
        now = self._clock.now().timestamp()
        jobs = await self._deliveries.due_jobs(now, self._batch)
        for job in jobs:
            await self._attempt(job)
        return len(jobs)

    async def _attempt(self, job: DeliveryJob) -> None:
        now = self._clock.now().timestamp()
        result = await self._sender.send(job, now=int(now))
        if result.outcome is DeliveryOutcome.SUCCESS:
            return
        next_attempt = job.attempt + 1
        if next_attempt >= self._max_attempts:
            await self._deliveries.dead_letter(job)
            return
        delay = backoff_delay(job.attempt, base=self._base, cap=self._cap, rng=self._rng)
        await self._deliveries.schedule(replace(job, attempt=next_attempt), now + delay)

    async def run(self, stop: asyncio.Event) -> None:
        """Loop until ``stop`` is set. Each pass retries due jobs, then waits briefly for one new
        event (the wait is what keeps the loop from spinning)."""
        while not stop.is_set():
            await self.process_retries_once()
            await self.process_events_once()
