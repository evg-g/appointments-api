"""Declarative base and reusable column mixins.

Every table gets a UUID primary key and (except the append-only audit log) created/updated
timestamps. Keeping these in mixins means each model file stays about the columns that make it
different, not the boilerplate every table shares.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Uuid, func
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Base class all ORM models inherit from; owns the shared metadata Alembic reads."""


class UUIDPrimaryKeyMixin:
    """A random (v4) UUID primary key, generated in Python so tests need no database."""

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True, default=uuid.uuid4)


class TimestampMixin:
    """``created_at``/``updated_at`` maintained by the database clock, not the app clock."""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )
