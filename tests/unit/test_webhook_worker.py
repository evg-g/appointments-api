"""Unit tests for the webhook worker's fan-out, retry, and dead-letter logic.

All collaborators are fakes and time is a fixed clock we advance by hand, so there is no network and
no real waiting — the retry and dead-letter behaviour is exercised deterministically.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from random import Random

from appointments_api.services.webhooks.events import WebhookEvent
from appointments_api.services.webhooks.queue import DeliveryJob
from appointments_api.services.webhooks.sender import DeliveryOutcome, DeliveryResult
from appointments_api.services.webhooks.worker import SubscriptionInfo, WebhookWorker
from tests.fakes.clock import FixedClock

T0 = datetime(2027, 6, 1, 12, 0, tzinfo=UTC)


class FakeEventQueue:
    def __init__(self, events: list[WebhookEvent]) -> None:
        self._events = list(events)

    async def publish(self, event: WebhookEvent) -> None:
        self._events.append(event)

    async def next_event(self, timeout_seconds: float) -> WebhookEvent | None:
        return self._events.pop(0) if self._events else None


class FakeDeliveryQueue:
    def __init__(self) -> None:
        self.scheduled: list[tuple[float, DeliveryJob]] = []
        self.dead: list[DeliveryJob] = []

    async def schedule(self, job: DeliveryJob, due_at: float) -> None:
        self.scheduled.append((due_at, job))

    async def due_jobs(self, now: float, limit: int) -> list[DeliveryJob]:
        due = [(d, j) for (d, j) in self.scheduled if d <= now][:limit]
        for item in due:
            self.scheduled.remove(item)
        return [job for (_, job) in due]

    async def dead_letter(self, job: DeliveryJob) -> None:
        self.dead.append(job)

    async def pending_count(self) -> int:
        return len(self.scheduled)

    async def dead_count(self) -> int:
        return len(self.dead)


class FakeSubscriptionSource:
    def __init__(self, subs: list[SubscriptionInfo]) -> None:
        self._subs = subs

    async def for_event(self, event_type: str) -> list[SubscriptionInfo]:
        return list(self._subs)


class ScriptedSender:
    def __init__(self, outcomes: list[DeliveryOutcome]) -> None:
        self._outcomes = list(outcomes)
        self.calls: list[DeliveryJob] = []

    async def send(self, job: DeliveryJob, *, now: int) -> DeliveryResult:
        self.calls.append(job)
        outcome = self._outcomes.pop(0) if self._outcomes else DeliveryOutcome.SUCCESS
        return DeliveryResult(outcome)


def _event() -> WebhookEvent:
    return WebhookEvent(
        id="evt-1",
        type="appointment.created",
        occurred_at=T0,
        data={"appointment": {"id": "abc"}},
    )


def _worker(
    *,
    events: FakeEventQueue,
    deliveries: FakeDeliveryQueue,
    subs: FakeSubscriptionSource,
    sender: ScriptedSender,
    clock: FixedClock,
    max_attempts: int = 5,
) -> WebhookWorker:
    return WebhookWorker(
        event_queue=events,
        delivery_queue=deliveries,
        subscriptions=subs,
        sender=sender,
        clock=clock,
        max_attempts=max_attempts,
        backoff_base=1.0,
        backoff_cap=300.0,
        rng=Random(0),
        event_poll_timeout=0.01,
    )


async def test_successful_delivery_to_all_subscriptions_no_retry() -> None:
    deliveries = FakeDeliveryQueue()
    sender = ScriptedSender([DeliveryOutcome.SUCCESS, DeliveryOutcome.SUCCESS])
    subs = FakeSubscriptionSource(
        [
            SubscriptionInfo("s1", "https://a.test/hook", "secret-aaaaaaaaaaaa"),
            SubscriptionInfo("s2", "https://b.test/hook", "secret-bbbbbbbbbbbb"),
        ]
    )
    worker = _worker(
        events=FakeEventQueue([_event()]),
        deliveries=deliveries,
        subs=subs,
        sender=sender,
        clock=FixedClock(T0),
    )

    handled = await worker.process_events_once()

    assert handled == 1
    assert len(sender.calls) == 2
    assert await deliveries.pending_count() == 0
    assert await deliveries.dead_count() == 0


async def test_failed_delivery_is_scheduled_for_retry() -> None:
    deliveries = FakeDeliveryQueue()
    sender = ScriptedSender([DeliveryOutcome.FAILURE])
    subs = FakeSubscriptionSource(
        [SubscriptionInfo("s1", "https://a.test/hook", "secret-aaaaaaaa")]
    )
    worker = _worker(
        events=FakeEventQueue([_event()]),
        deliveries=deliveries,
        subs=subs,
        sender=sender,
        clock=FixedClock(T0),
    )

    await worker.process_events_once()

    assert len(sender.calls) == 1
    assert await deliveries.pending_count() == 1
    assert await deliveries.dead_count() == 0
    due_at, job = deliveries.scheduled[0]
    assert job.attempt == 1  # the retry is the second attempt
    assert due_at > T0.timestamp()  # scheduled into the future by the backoff


async def test_retry_then_success_clears_the_queue() -> None:
    deliveries = FakeDeliveryQueue()
    sender = ScriptedSender([DeliveryOutcome.FAILURE, DeliveryOutcome.SUCCESS])
    subs = FakeSubscriptionSource(
        [SubscriptionInfo("s1", "https://a.test/hook", "secret-aaaaaaaa")]
    )
    clock = FixedClock(T0)
    worker = _worker(
        events=FakeEventQueue([_event()]),
        deliveries=deliveries,
        subs=subs,
        sender=sender,
        clock=clock,
    )

    await worker.process_events_once()  # fails, schedules a retry
    clock.instant = T0 + timedelta(hours=1)  # move past the backoff
    tried = await worker.process_retries_once()  # succeeds

    assert tried == 1
    assert len(sender.calls) == 2
    assert await deliveries.pending_count() == 0
    assert await deliveries.dead_count() == 0


async def test_dead_letter_after_max_attempts() -> None:
    deliveries = FakeDeliveryQueue()
    sender = ScriptedSender([DeliveryOutcome.FAILURE] * 5)
    subs = FakeSubscriptionSource(
        [SubscriptionInfo("s1", "https://a.test/hook", "secret-aaaaaaaa")]
    )
    clock = FixedClock(T0)
    worker = _worker(
        events=FakeEventQueue([_event()]),
        deliveries=deliveries,
        subs=subs,
        sender=sender,
        clock=clock,
        max_attempts=3,
    )

    await worker.process_events_once()  # attempt 0 fails -> schedule attempt 1
    for hours in (1, 2, 3):
        clock.instant = T0 + timedelta(hours=hours)
        await worker.process_retries_once()

    assert len(sender.calls) == 3  # attempts 0, 1, 2 — then dead-lettered, no 4th send
    assert await deliveries.pending_count() == 0
    assert await deliveries.dead_count() == 1


async def test_process_events_once_returns_zero_on_empty_poll() -> None:
    worker = _worker(
        events=FakeEventQueue([]),
        deliveries=FakeDeliveryQueue(),
        subs=FakeSubscriptionSource([]),
        sender=ScriptedSender([]),
        clock=FixedClock(T0),
    )
    assert await worker.process_events_once() == 0


async def test_run_loop_processes_a_pass_then_stops_when_signalled() -> None:
    stop = asyncio.Event()

    class StoppingEventQueue(FakeEventQueue):
        async def next_event(self, timeout_seconds: float) -> WebhookEvent | None:
            # Let one full pass happen, then ask the loop to stop.
            stop.set()
            return await super().next_event(timeout_seconds)

    deliveries = FakeDeliveryQueue()
    sender = ScriptedSender([])
    worker = _worker(
        events=StoppingEventQueue([]),
        deliveries=deliveries,
        subs=FakeSubscriptionSource([]),
        sender=sender,
        clock=FixedClock(T0),
    )

    # Completes because the queue sets `stop` during the first pass.
    await asyncio.wait_for(worker.run(stop), timeout=1.0)
    assert stop.is_set()
