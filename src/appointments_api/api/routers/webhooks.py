"""Webhook subscription management (admin only).

Create, list, and delete the targets the platform notifies on appointment state changes. The signing
``secret`` is accepted on create but never returned — a subscription output omits it.
"""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, status
from starlette.responses import Response

from appointments_api.api.deps import WebhookSubscriptionRepoDep, require_roles
from appointments_api.api.errors import NotFoundError
from appointments_api.api.schemas import WebhookSubscriptionCreate, WebhookSubscriptionOut
from appointments_api.enums import UserRole
from appointments_api.models import User, WebhookSubscription

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

# Only clinic admins and platform admins manage subscriptions.
AdminUser = Annotated[User, Depends(require_roles(UserRole.CLINIC_ADMIN, UserRole.PLATFORM_ADMIN))]


@router.post(
    "/subscriptions", response_model=WebhookSubscriptionOut, status_code=status.HTTP_201_CREATED
)
async def create_subscription(
    body: WebhookSubscriptionCreate,
    _admin: AdminUser,
    repo: WebhookSubscriptionRepoDep,
) -> WebhookSubscription:
    subscription = WebhookSubscription(
        clinic_id=body.clinic_id,
        url=str(body.url),
        secret=body.secret,
        event_types=[event_type.value for event_type in body.event_types],
    )
    return await repo.add(subscription)


@router.get("/subscriptions", response_model=list[WebhookSubscriptionOut])
async def list_subscriptions(
    _admin: AdminUser,
    repo: WebhookSubscriptionRepoDep,
) -> Sequence[WebhookSubscription]:
    return await repo.list_all()


@router.delete(
    "/subscriptions/{subscription_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_class=Response,
)
async def delete_subscription(
    subscription_id: uuid.UUID,
    _admin: AdminUser,
    repo: WebhookSubscriptionRepoDep,
) -> Response:
    subscription = await repo.get(subscription_id)
    if subscription is None:
        raise NotFoundError("No such webhook subscription.")
    await repo.delete(subscription)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
