"""Appointment model.

Two protections live here that are easy to miss:

1. **No double-booking, enforced by the database.** A PostgreSQL exclusion constraint refuses
   any two live appointments for the same clinician whose time ranges overlap. This is stronger
   than an application check: it holds even under two concurrent inserts racing each other,
   because the guarantee is the database's, not the app's. Cancelled and no-show appointments
   are excluded from the rule (the ``where`` clause), so freeing a slot really frees it.

2. **Optimistic locking via ``version``.** SQLAlchemy bumps ``version`` on every UPDATE and
   refuses to write if the row's version moved since we read it, turning a lost update into a
   loud error instead of silent data loss.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Enum, ForeignKey, Integer, String, func, literal_column, text
from sqlalchemy.dialects.postgresql import ExcludeConstraint
from sqlalchemy.orm import Mapped, mapped_column

from appointments_api.enums import AppointmentStatus
from appointments_api.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class Appointment(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "appointments"
    __table_args__ = (
        # Exclusion constraint: for rows that are still "live", no two with the same clinician
        # may have overlapping [starts_at, ends_at) ranges. Requires the btree_gist extension
        # (created in the migration) so a plain-equality column and a range can share one GiST
        # index. literal_column keeps the identifiers raw so the DDL reads like hand-written SQL.
        ExcludeConstraint(
            (literal_column("clinician_id"), "="),
            (func.tstzrange(literal_column("starts_at"), literal_column("ends_at"), "[)"), "&&"),
            using="gist",
            where=text("status NOT IN ('CANCELLED', 'NO_SHOW')"),
            name="ck_appointment_no_double_booking",
        ),
    )

    clinic_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinics.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    clinician_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("clinicians.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    patient_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    service_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("services.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    # Both stored as UTC (timezone-aware). The clinic timezone lives on Clinic; here it is
    # always the absolute instant.
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[AppointmentStatus] = mapped_column(
        Enum(AppointmentStatus, name="appointment_status"),
        default=AppointmentStatus.REQUESTED,
        server_default=AppointmentStatus.REQUESTED.value,
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, default=1, server_default="1", nullable=False)
    cancellation_reason: Mapped[str | None] = mapped_column(String(500), nullable=True)

    # Tell the mapper to use `version` for optimistic-lock checking on every UPDATE/DELETE.
    # This is a SQLAlchemy declarative directive, not a mutable class default, so RUF012's
    # ClassVar advice does not apply — and adding ClassVar clashes with DeclarativeBase.
    __mapper_args__ = {"version_id_col": version}  # noqa: RUF012
