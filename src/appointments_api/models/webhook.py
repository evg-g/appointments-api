"""Webhook subscription model.

A subscription is a target the platform should notify when certain events happen: a URL, a shared
``secret`` used to sign deliveries (HMAC-SHA256), and the list of event types it wants. An optional
``clinic_id`` scopes it to one clinic; null means all clinics. The secret is write-only at the API
edge — it is never returned in a response.
"""

from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, String, text
from sqlalchemy.dialects.postgresql import ARRAY
from sqlalchemy.orm import Mapped, mapped_column

from appointments_api.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class WebhookSubscription(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "webhook_subscriptions"

    # Null clinic_id = a platform-wide subscription (all clinics). CASCADE so removing a clinic
    # cleans up its subscriptions.
    clinic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("clinics.id", ondelete="CASCADE"), nullable=True, index=True
    )
    url: Mapped[str] = mapped_column(String(2048), nullable=False)
    secret: Mapped[str] = mapped_column(String(255), nullable=False)
    # Event type strings (e.g. "appointment.created"). A Postgres text[] so a membership test can
    # run in the database.
    event_types: Mapped[list[str]] = mapped_column(ARRAY(String), nullable=False)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default=text("true"), nullable=False
    )
