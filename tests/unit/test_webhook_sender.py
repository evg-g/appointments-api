"""Unit tests for the HTTP webhook sender.

Technique: **HTTP mocking with respx**. We drive success, a 500, and a timeout with no real server,
and confirm the request carries a signature that verifies against the subscription secret.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from appointments_api.services.webhooks.events import WebhookEvent
from appointments_api.services.webhooks.queue import DeliveryJob
from appointments_api.services.webhooks.sender import DeliveryOutcome, HttpWebhookSender
from appointments_api.services.webhooks.signing import verify

URL = "https://example.test/hook"
SECRET = "secret-at-least-16-chars"
NOW = 1_800_000_000


def _job(attempt: int = 0) -> DeliveryJob:
    event = WebhookEvent(
        id="e1",
        type="appointment.created",
        occurred_at=datetime(2027, 1, 1, tzinfo=UTC),
        data={"appointment": {"id": "abc"}},
    )
    return DeliveryJob(
        id="job1", subscription_id="sub1", url=URL, secret=SECRET, event=event, attempt=attempt
    )


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as c:
        yield c


async def test_2xx_is_success(client: httpx.AsyncClient) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.post(URL).mock(return_value=httpx.Response(200))
        sender = HttpWebhookSender(client, timeout_seconds=5)
        result = await sender.send(_job(), now=NOW)
    assert result.outcome is DeliveryOutcome.SUCCESS
    assert result.status_code == 200


async def test_request_signature_verifies(client: httpx.AsyncClient) -> None:
    captured: dict[str, Any] = {}

    def responder(request: httpx.Request) -> httpx.Response:
        captured["ts"] = int(request.headers["X-Webhook-Timestamp"])
        captured["sig"] = request.headers["X-Signature"]
        captured["body"] = request.content
        return httpx.Response(200)

    with respx.mock(assert_all_called=False) as router:
        router.post(URL).mock(side_effect=responder)
        sender = HttpWebhookSender(client, timeout_seconds=5)
        await sender.send(_job(attempt=2), now=NOW)

    assert verify(
        SECRET,
        captured["ts"],
        captured["body"],
        captured["sig"],
        now=NOW,
        tolerance_seconds=300,
    )


async def test_500_is_failure(client: httpx.AsyncClient) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.post(URL).mock(return_value=httpx.Response(500))
        sender = HttpWebhookSender(client, timeout_seconds=5)
        result = await sender.send(_job(), now=NOW)
    assert result.outcome is DeliveryOutcome.FAILURE
    assert result.status_code == 500


async def test_timeout_is_failure(client: httpx.AsyncClient) -> None:
    with respx.mock(assert_all_called=False) as router:
        router.post(URL).mock(side_effect=httpx.TimeoutException("timed out"))
        sender = HttpWebhookSender(client, timeout_seconds=5)
        result = await sender.send(_job(), now=NOW)
    assert result.outcome is DeliveryOutcome.FAILURE
    assert result.status_code is None
