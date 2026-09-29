"""Webhooks end to end: event emission on state change, subscription management, and the worker's
delivery / retry / dead-letter behaviour against the real Redis container and a real DB.

The HTTP *sender* is a capturing double here (we assert on the signed job it receives). Everything
else is real: the event queue, the retry sorted set, the dead-letter list, the subscription lookup.
The sender itself is covered against real HTTP semantics by the respx unit tests.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime, timedelta
from random import Random
from typing import Any
from unittest.mock import AsyncMock

import httpx
from fastapi import FastAPI

from appointments_api.api.deps import get_webhook_dispatcher
from appointments_api.models import WebhookSubscription
from appointments_api.repositories.redis_webhooks import RedisDeliveryQueue, RedisEventQueue
from appointments_api.repositories.webhooks import DbSubscriptionSource
from appointments_api.services.webhooks.events import WebhookEvent, WebhookEventType
from appointments_api.services.webhooks.queue import DeliveryJob
from appointments_api.services.webhooks.sender import DeliveryOutcome, DeliveryResult
from appointments_api.services.webhooks.worker import WebhookWorker
from tests.fakes.clock import FixedClock
from tests.integration.helpers import auth_header

T0 = datetime(2027, 6, 1, 12, 0, tzinfo=UTC)
SECRET = "secret-at-least-16-chars"


class CapturingSender:
    """Records the jobs it is asked to send and returns scripted outcomes (default: success)."""

    def __init__(self, outcomes: list[DeliveryOutcome] | None = None) -> None:
        self._outcomes = list(outcomes or [])
        self.calls: list[DeliveryJob] = []

    async def send(self, job: DeliveryJob, *, now: int) -> DeliveryResult:
        self.calls.append(job)
        outcome = self._outcomes.pop(0) if self._outcomes else DeliveryOutcome.SUCCESS
        return DeliveryResult(outcome)


async def _add_subscription(
    app: FastAPI, *, event_types: list[str], url: str = "https://receiver.test/hook"
) -> uuid.UUID:
    async with app.state.sessionmaker() as session:
        subscription = WebhookSubscription(url=url, secret=SECRET, event_types=event_types)
        session.add(subscription)
        await session.commit()
        return subscription.id


def _make_worker(
    app: FastAPI, sender: CapturingSender, clock: FixedClock, *, max_attempts: int = 5
) -> WebhookWorker:
    redis = app.state.redis
    return WebhookWorker(
        event_queue=RedisEventQueue(redis),
        delivery_queue=RedisDeliveryQueue(redis),
        subscriptions=DbSubscriptionSource(app.state.sessionmaker),
        sender=sender,
        clock=clock,
        max_attempts=max_attempts,
        backoff_base=1.0,
        backoff_cap=300.0,
        rng=Random(0),
        event_poll_timeout=0.1,
    )


def _event(event_type: str = "appointment.created") -> WebhookEvent:
    return WebhookEvent(
        id=uuid.uuid4().hex,
        type=event_type,
        occurred_at=T0,
        data={"appointment": {"id": "abc"}},
    )


# ---- event emission on the request path ----


async def test_creating_an_appointment_enqueues_a_created_event(
    client: httpx.AsyncClient, app: FastAPI, booking_env: dict[str, Any]
) -> None:
    created = await client.post(
        "/api/v1/appointments",
        json={
            "clinic_id": str(booking_env["clinic_id"]),
            "clinician_id": str(booking_env["clinician_id"]),
            "service_id": str(booking_env["service_id"]),
            "starts_at": "2027-06-01T10:00:00+00:00",
        },
        headers=auth_header(booking_env["patient_token"]),
    )
    assert created.status_code == 201

    raw = await app.state.redis.lrange(RedisEventQueue.KEY, 0, -1)
    assert len(raw) == 1
    event = json.loads(raw[0])
    assert event["type"] == WebhookEventType.APPOINTMENT_CREATED.value
    assert event["data"]["appointment"]["id"] == created.json()["id"]


# ---- subscription management ----


async def test_admin_manages_subscriptions_patient_forbidden(
    client: httpx.AsyncClient, booking_env: dict[str, Any]
) -> None:
    payload = {
        "url": "https://receiver.test/hook",
        "secret": SECRET,
        "event_types": ["appointment.created", "appointment.cancelled"],
    }
    created = await client.post(
        "/api/v1/webhooks/subscriptions",
        json=payload,
        headers=auth_header(booking_env["admin_token"]),
    )
    assert created.status_code == 201
    body = created.json()
    assert "secret" not in body  # the secret is never returned
    assert set(body["event_types"]) == {"appointment.created", "appointment.cancelled"}

    listed = await client.get(
        "/api/v1/webhooks/subscriptions", headers=auth_header(booking_env["admin_token"])
    )
    assert listed.status_code == 200
    assert len(listed.json()) == 1

    # A patient may not manage subscriptions.
    forbidden = await client.post(
        "/api/v1/webhooks/subscriptions",
        json=payload,
        headers=auth_header(booking_env["patient_token"]),
    )
    assert forbidden.status_code == 403

    deleted = await client.delete(
        f"/api/v1/webhooks/subscriptions/{body['id']}",
        headers=auth_header(booking_env["admin_token"]),
    )
    assert deleted.status_code == 204


# ---- worker: fan-out, retry, dead-letter (real Redis) ----


async def test_worker_delivers_signed_event_to_matching_subscription(app: FastAPI) -> None:
    subscription_id = await _add_subscription(app, event_types=["appointment.created"])
    await RedisEventQueue(app.state.redis).publish(_event())

    sender = CapturingSender([DeliveryOutcome.SUCCESS])
    worker = _make_worker(app, sender, FixedClock(T0))
    handled = await worker.process_events_once()

    assert handled == 1
    assert len(sender.calls) == 1
    assert sender.calls[0].subscription_id == str(subscription_id)
    delivery_queue = RedisDeliveryQueue(app.state.redis)
    assert await delivery_queue.pending_count() == 0
    assert await delivery_queue.dead_count() == 0


async def test_worker_does_not_deliver_to_non_matching_subscription(app: FastAPI) -> None:
    await _add_subscription(app, event_types=["appointment.cancelled"])  # different event
    await RedisEventQueue(app.state.redis).publish(_event("appointment.created"))

    sender = CapturingSender()
    worker = _make_worker(app, sender, FixedClock(T0))
    await worker.process_events_once()

    assert sender.calls == []


async def test_worker_retries_then_delivers(app: FastAPI) -> None:
    await _add_subscription(app, event_types=["appointment.created"])
    await RedisEventQueue(app.state.redis).publish(_event())

    sender = CapturingSender([DeliveryOutcome.FAILURE, DeliveryOutcome.SUCCESS])
    clock = FixedClock(T0)
    worker = _make_worker(app, sender, clock)
    delivery_queue = RedisDeliveryQueue(app.state.redis)

    await worker.process_events_once()  # fails, schedules a retry
    assert await delivery_queue.pending_count() == 1

    clock.instant = T0 + timedelta(hours=1)  # advance past the backoff
    tried = await worker.process_retries_once()

    assert tried == 1
    assert len(sender.calls) == 2
    assert await delivery_queue.pending_count() == 0
    assert await delivery_queue.dead_count() == 0


async def test_worker_dead_letters_after_max_attempts(app: FastAPI) -> None:
    await _add_subscription(app, event_types=["appointment.created"])
    await RedisEventQueue(app.state.redis).publish(_event())

    sender = CapturingSender([DeliveryOutcome.FAILURE] * 5)
    clock = FixedClock(T0)
    worker = _make_worker(app, sender, clock, max_attempts=3)
    delivery_queue = RedisDeliveryQueue(app.state.redis)

    await worker.process_events_once()
    for hours in (1, 2, 3):
        clock.instant = T0 + timedelta(hours=hours)
        await worker.process_retries_once()

    assert len(sender.calls) == 3
    assert await delivery_queue.pending_count() == 0
    assert await delivery_queue.dead_count() == 1


# ---- interaction test: cancel emits exactly one cancelled event ----


async def test_cancel_calls_the_dispatcher_once(
    client: httpx.AsyncClient, app: FastAPI, booking_env: dict[str, Any]
) -> None:
    # Technique: async mock with an interaction assertion. Override the dispatcher so we can prove
    # the cancel endpoint emits exactly one appointment.cancelled event with the right payload.
    mock_dispatcher = AsyncMock()
    app.dependency_overrides[get_webhook_dispatcher] = lambda: mock_dispatcher

    created = await client.post(
        "/api/v1/appointments",
        json={
            "clinic_id": str(booking_env["clinic_id"]),
            "clinician_id": str(booking_env["clinician_id"]),
            "service_id": str(booking_env["service_id"]),
            "starts_at": "2027-06-05T10:00:00+00:00",  # far off: a patient may cancel
        },
        headers=auth_header(booking_env["patient_token"]),
    )
    assert created.status_code == 201
    mock_dispatcher.dispatch.reset_mock()  # ignore the created event; assert only on cancel

    cancelled = await client.post(
        f"/api/v1/appointments/{created.json()['id']}/cancel",
        json={"reason": "changed my mind"},
        headers={**auth_header(booking_env["patient_token"]), "If-Match": created.headers["ETag"]},
    )
    assert cancelled.status_code == 200

    mock_dispatcher.dispatch.assert_awaited_once()
    event = mock_dispatcher.dispatch.await_args.args[0]
    assert event.type == WebhookEventType.APPOINTMENT_CANCELLED.value
    assert event.data["appointment"]["id"] == created.json()["id"]
