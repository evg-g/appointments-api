"""Webhook delivery worker entry point.

Run it as its own process against the same Redis and database as the API::

    # WSL (Ubuntu-24.04)
    python -m appointments_api.workers.webhooks

It wires the real Redis queues, a DB-backed subscription lookup, and an HTTP sender, then loops
until it receives SIGINT/SIGTERM. ``build_worker`` is shared with the API's in-process worker so
both are configured identically.
"""

from __future__ import annotations

import asyncio
import contextlib
import signal

import httpx
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from appointments_api.config import Settings, get_settings
from appointments_api.db import create_engine, create_redis, create_sessionmaker
from appointments_api.repositories.redis_webhooks import RedisDeliveryQueue, RedisEventQueue
from appointments_api.repositories.webhooks import DbSubscriptionSource
from appointments_api.services.clock import SystemClock
from appointments_api.services.webhooks.sender import HttpWebhookSender
from appointments_api.services.webhooks.worker import WebhookWorker


def build_worker(
    *,
    redis: Redis,
    sessionmaker: async_sessionmaker[AsyncSession],
    http_client: httpx.AsyncClient,
    settings: Settings,
) -> WebhookWorker:
    return WebhookWorker(
        event_queue=RedisEventQueue(redis),
        delivery_queue=RedisDeliveryQueue(redis),
        subscriptions=DbSubscriptionSource(sessionmaker),
        sender=HttpWebhookSender(
            http_client, timeout_seconds=settings.webhook_delivery_timeout_seconds
        ),
        clock=SystemClock(),
        max_attempts=settings.webhook_max_attempts,
        backoff_base=settings.webhook_backoff_base_seconds,
        backoff_cap=settings.webhook_backoff_cap_seconds,
    )


async def run(settings: Settings | None = None) -> None:
    settings = settings or get_settings()
    engine = create_engine(settings)
    sessionmaker = create_sessionmaker(engine)
    redis = create_redis(settings)
    http_client = httpx.AsyncClient()
    worker = build_worker(
        redis=redis, sessionmaker=sessionmaker, http_client=http_client, settings=settings
    )

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        # add_signal_handler is unavailable on some platforms (e.g. Windows); degrade gracefully.
        with contextlib.suppress(NotImplementedError):
            loop.add_signal_handler(sig, stop.set)

    try:
        await worker.run(stop)
    finally:
        await engine.dispose()
        await redis.aclose()
        await http_client.aclose()


def main() -> None:
    asyncio.run(run())


if __name__ == "__main__":
    main()
