"""Webhook subscription repository, plus the DB adapter the worker uses to find subscriptions.

``WebhookSubscriptionRepository`` is the request-path repository (create/list/delete, and the
event-match query). ``DbSubscriptionSource`` implements the worker's ``SubscriptionSource`` port by
opening a short-lived session per lookup, so the worker never holds a request's session.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from appointments_api.models import WebhookSubscription
from appointments_api.services.webhooks.worker import SubscriptionInfo


class WebhookSubscriptionRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._s = session

    async def add(self, subscription: WebhookSubscription) -> WebhookSubscription:
        self._s.add(subscription)
        await self._s.flush()
        return subscription

    async def get(self, subscription_id: uuid.UUID) -> WebhookSubscription | None:
        return await self._s.get(WebhookSubscription, subscription_id)

    async def list_all(self) -> Sequence[WebhookSubscription]:
        stmt = select(WebhookSubscription).order_by(WebhookSubscription.created_at.desc())
        return (await self._s.execute(stmt)).scalars().all()

    async def delete(self, subscription: WebhookSubscription) -> None:
        await self._s.delete(subscription)

    async def for_event(self, event_type: str) -> Sequence[WebhookSubscription]:
        # active subscriptions whose event_types array contains this type (Postgres @> operator).
        stmt = select(WebhookSubscription).where(
            WebhookSubscription.is_active.is_(True),
            WebhookSubscription.event_types.contains([event_type]),
        )
        return (await self._s.execute(stmt)).scalars().all()


class DbSubscriptionSource:
    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._maker = sessionmaker

    async def for_event(self, event_type: str) -> list[SubscriptionInfo]:
        async with self._maker() as session:
            subs = await WebhookSubscriptionRepository(session).for_event(event_type)
            return [
                SubscriptionInfo(id=str(sub.id), url=sub.url, secret=sub.secret) for sub in subs
            ]
