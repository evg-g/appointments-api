"""Unit test for the webhook dispatcher.

Technique: **async mock with an interaction assertion**. The dispatcher's only job is to publish the
event exactly once, so we verify the call rather than any state.
"""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

from appointments_api.services.webhooks.dispatcher import WebhookDispatcher
from appointments_api.services.webhooks.events import WebhookEvent
from appointments_api.services.webhooks.queue import EventQueue


async def test_dispatch_publishes_the_event_once() -> None:
    queue = AsyncMock(spec=EventQueue)
    dispatcher = WebhookDispatcher(queue)
    event = WebhookEvent(
        id="e1",
        type="appointment.created",
        occurred_at=datetime(2027, 1, 1, tzinfo=UTC),
        data={"appointment": {"id": "abc"}},
    )

    await dispatcher.dispatch(event)

    queue.publish.assert_awaited_once_with(event)
