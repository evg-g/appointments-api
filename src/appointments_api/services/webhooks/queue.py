"""Queue ports and the delivery job, with JSON (de)serialization.

Two queues, both backed by Redis in production (see ``repositories.redis_webhooks``):

- **EventQueue** — the request path publishes a state-change event here; the worker pops it.
- **DeliveryQueue** — a per-subscription job that failed is scheduled here (a due-time sorted set)
  and retried when due; a job that exhausts its attempts is moved to the dead-letter list.

Both are Protocols so the worker's unit tests use in-memory fakes and only the integration tier
touches Redis.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Protocol

from appointments_api.services.webhooks.events import WebhookEvent, event_from_dict, event_to_dict


@dataclass(frozen=True, slots=True)
class DeliveryJob:
    """One attempt to deliver one event to one subscription. ``attempt`` counts deliveries already
    made (0 = not yet tried), which drives the backoff and the dead-letter cutoff."""

    id: str
    subscription_id: str
    url: str
    secret: str
    event: WebhookEvent
    attempt: int


def job_to_json(job: DeliveryJob) -> str:
    return json.dumps(
        {
            "id": job.id,
            "subscription_id": job.subscription_id,
            "url": job.url,
            "secret": job.secret,
            "attempt": job.attempt,
            "event": event_to_dict(job.event),
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def job_from_json(raw: str) -> DeliveryJob:
    data: dict[str, Any] = json.loads(raw)
    return DeliveryJob(
        id=data["id"],
        subscription_id=data["subscription_id"],
        url=data["url"],
        secret=data["secret"],
        event=event_from_dict(data["event"]),
        attempt=data["attempt"],
    )


class EventQueue(Protocol):
    async def publish(self, event: WebhookEvent) -> None: ...
    async def next_event(self, timeout_seconds: float) -> WebhookEvent | None: ...


class DeliveryQueue(Protocol):
    async def schedule(self, job: DeliveryJob, due_at: float) -> None: ...
    async def due_jobs(self, now: float, limit: int) -> list[DeliveryJob]: ...
    async def dead_letter(self, job: DeliveryJob) -> None: ...
    async def pending_count(self) -> int: ...
    async def dead_count(self) -> int: ...
