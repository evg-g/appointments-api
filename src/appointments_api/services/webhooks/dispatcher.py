"""The request-path entry point for webhooks.

Kept deliberately tiny: an API request that changes an appointment calls ``dispatch`` to publish
one event and returns. No subscription lookup and no HTTP happens on the request path — the worker
does the fan-out and delivery — so a slow or failing receiver never slows a booking.
"""

from __future__ import annotations

from appointments_api.services.webhooks.events import WebhookEvent
from appointments_api.services.webhooks.queue import EventQueue


class WebhookDispatcher:
    def __init__(self, queue: EventQueue) -> None:
        self._queue = queue

    async def dispatch(self, event: WebhookEvent) -> None:
        await self._queue.publish(event)
