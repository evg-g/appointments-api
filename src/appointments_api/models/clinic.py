"""Clinic model. A clinic owns its own timezone and its own cancellation policy."""

from __future__ import annotations

from sqlalchemy import Integer, String
from sqlalchemy.orm import Mapped, mapped_column

from appointments_api.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Clinic(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "clinics"

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # IANA timezone name, e.g. "America/New_York". Every appointment time this clinic sets is
    # interpreted in this zone before being stored as UTC.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    address: Mapped[str] = mapped_column(String(500), nullable=False)
    # How many hours before the start a patient may still cancel on their own.
    cancellation_cutoff_hours: Mapped[int] = mapped_column(
        Integer, default=24, server_default="24", nullable=False
    )
