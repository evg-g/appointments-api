"""Clinician model and their weekly working hours.

Working hours are stored one row per (weekday, shift) rather than as a JSON blob. That keeps
them queryable and lets a clinician have split shifts (e.g. 09:00-12:00 and 14:00-17:00) on the
same day without any special casing.
"""

from __future__ import annotations

import uuid
from datetime import time

from sqlalchemy import CheckConstraint, ForeignKey, Integer, SmallInteger, String, Time
from sqlalchemy.orm import Mapped, mapped_column, relationship

from appointments_api.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Clinician(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "clinicians"

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinics.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # A clinician is also a user (they log in). One user maps to at most one clinician.
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    specialty: Mapped[str] = mapped_column(String(100), nullable=False)
    # Minutes of protected gap the clinician needs before and after each appointment.
    buffer_minutes: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0", nullable=False
    )

    working_hours: Mapped[list[ClinicianWorkingHours]] = relationship(
        back_populates="clinician", cascade="all, delete-orphan"
    )


class ClinicianWorkingHours(Base, UUIDPrimaryKeyMixin):
    __tablename__ = "clinician_working_hours"
    __table_args__ = (
        CheckConstraint("weekday >= 0 AND weekday <= 6", name="ck_working_hours_weekday_range"),
        CheckConstraint("end_time > start_time", name="ck_working_hours_time_order"),
    )

    clinician_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinicians.id", ondelete="CASCADE"), nullable=False, index=True
    )
    # 0 = Monday .. 6 = Sunday, matching datetime.date.weekday().
    weekday: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    # Local wall-clock times in the clinic's timezone, not UTC.
    start_time: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)
    end_time: Mapped[time] = mapped_column(Time(timezone=False), nullable=False)

    clinician: Mapped[Clinician] = relationship(back_populates="working_hours")
