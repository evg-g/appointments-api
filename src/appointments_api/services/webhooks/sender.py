"""HTTP delivery of a signed webhook.

The sender turns a :class:`DeliveryJob` into a signed POST. It never raises for a delivery failure:
a timeout, a connection error, or a non-2xx status all come back as a ``FAILURE`` result, so the
worker can decide whether to retry or dead-letter. This is the seam the unit tests drive with
``respx`` (success, 500, timeout, connection error).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum, auto
from typing import Protocol

import httpx

from appointments_api.services.webhooks.events import event_to_json
from appointments_api.services.webhooks.queue import DeliveryJob
from appointments_api.services.webhooks.signing import signature_header


class DeliveryOutcome(Enum):
    SUCCESS = auto()
    FAILURE = auto()


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    outcome: DeliveryOutcome
    status_code: int | None = None
    error: str | None = None


class WebhookSender(Protocol):
    async def send(self, job: DeliveryJob, *, now: int) -> DeliveryResult: ...


class HttpWebhookSender:
    def __init__(self, client: httpx.AsyncClient, *, timeout_seconds: float) -> None:
        self._client = client
        self._timeout = timeout_seconds

    async def send(self, job: DeliveryJob, *, now: int) -> DeliveryResult:
        body = event_to_json(job.event).encode("utf-8")
        headers = {
            "Content-Type": "application/json",
            "X-Webhook-Id": job.event.id,
            "X-Webhook-Event": job.event.type,
            "X-Webhook-Timestamp": str(now),
            "X-Webhook-Attempt": str(job.attempt + 1),
            "X-Signature": signature_header(job.secret, now, body),
        }
        try:
            response = await self._client.post(
                job.url, content=body, headers=headers, timeout=self._timeout
            )
        except httpx.HTTPError as exc:
            return DeliveryResult(DeliveryOutcome.FAILURE, error=repr(exc))
        if 200 <= response.status_code < 300:
            return DeliveryResult(DeliveryOutcome.SUCCESS, status_code=response.status_code)
        return DeliveryResult(
            DeliveryOutcome.FAILURE,
            status_code=response.status_code,
            error=f"HTTP {response.status_code}",
        )
