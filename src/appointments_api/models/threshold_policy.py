"""ThresholdPolicy model — the safe temperature band and the dwell/recovery timers.

A policy is scoped either to a single device or to a whole clinic (exactly one of the two ids is
set; the check constraint enforces it). Device resolution is "most specific wins": if a device has
its own policy it is used, otherwise its clinic's policy applies. The four numbers here are exactly
the four the device firmware carries (``min``/``max`` temperature, ``dwell_minutes``,
``recovery_minutes``), so the server excursion engine evaluates the stored series against the same
rule the device applied locally (spec §4 rule 11).
"""

from __future__ import annotations

import uuid

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, text
from sqlalchemy.orm import Mapped, mapped_column

from appointments_api.models.base import Base, TimestampMixin, UUIDPrimaryKeyMixin


class ThresholdPolicy(Base, UUIDPrimaryKeyMixin, TimestampMixin):
    __tablename__ = "threshold_policies"
    __table_args__ = (
        # Exactly one scope: a policy is either device-specific or clinic-wide, never both/neither.
        CheckConstraint(
            "(device_id IS NULL) <> (clinic_id IS NULL)",
            name="ck_threshold_policy_one_scope",
        ),
        # At most one policy per scope target (partial unique indexes; the null scope is ignored).
        Index(
            "uq_threshold_policy_device",
            "device_id",
            unique=True,
            postgresql_where=text("device_id IS NOT NULL"),
        ),
        Index(
            "uq_threshold_policy_clinic",
            "clinic_id",
            unique=True,
            postgresql_where=text("clinic_id IS NOT NULL"),
        ),
    )

    clinic_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("clinics.id", ondelete="CASCADE"), nullable=True
    )
    device_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("devices.id", ondelete="CASCADE"), nullable=True
    )
    min_temperature_c: Mapped[float] = mapped_column(Float, nullable=False)
    max_temperature_c: Mapped[float] = mapped_column(Float, nullable=False)
    dwell_minutes: Mapped[float] = mapped_column(Float, nullable=False)
    recovery_minutes: Mapped[float] = mapped_column(Float, nullable=False)
