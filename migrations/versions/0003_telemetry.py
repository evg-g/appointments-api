"""Telemetry: devices, readings, threshold policies, excursions.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-27

Milestone 10. Hand-written to match 0001/0002 and to make the two things that matter explicit: the
**BRIN** index on ``telemetry_readings.measured_at`` (cheap time-window scans on an append-heavy,
time-clustered table — the "no TimescaleDB" requirement) and the ``(device_id, sequence)`` unique
constraint that makes ingestion idempotent.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Enum types created explicitly (create_type=False on the columns), matching 0001's style.
_DEVICE_STATUS = postgresql.ENUM(
    "PROVISIONED",
    "ACTIVE",
    "DISABLED",
    "RETIRED",
    name="device_status",
    create_type=False,
)
_EXCURSION_DIRECTION = postgresql.ENUM(
    "low",
    "high",
    name="excursion_direction",
    create_type=False,
)

_TIMESTAMPS = (
    sa.Column(
        "created_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    ),
    sa.Column(
        "updated_at",
        sa.DateTime(timezone=True),
        server_default=sa.text("now()"),
        nullable=False,
    ),
)


def upgrade() -> None:
    op.execute("CREATE TYPE device_status AS ENUM ('PROVISIONED', 'ACTIVE', 'DISABLED', 'RETIRED')")
    op.execute("CREATE TYPE excursion_direction AS ENUM ('low', 'high')")

    op.create_table(
        "devices",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "clinic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clinics.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("location_label", sa.String(200), nullable=False),
        sa.Column("hardware_version", sa.String(64), nullable=False),
        sa.Column("firmware_version", sa.String(64), nullable=False),
        sa.Column(
            "status",
            _DEVICE_STATUS,
            server_default=sa.text("'PROVISIONED'"),
            nullable=False,
        ),
        sa.Column("secret_hash", sa.String(255), nullable=False),
        sa.Column("last_seen_at", sa.DateTime(timezone=True), nullable=True),
        *_TIMESTAMPS,
    )
    op.create_index("ix_devices_clinic_id", "devices", ["clinic_id"])

    op.create_table(
        "telemetry_readings",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("measured_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("temperature_c", sa.Float(), nullable=False),
        sa.Column("humidity_pct", sa.Float(), nullable=True),
        sa.Column("battery_pct", sa.Float(), nullable=True),
        sa.Column("sequence", sa.BigInteger(), nullable=False),
        sa.Column(
            "clock_skew_flagged",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.UniqueConstraint("device_id", "sequence", name="uq_reading_device_sequence"),
        *_TIMESTAMPS,
    )
    # BRIN over the physical timeline: cheap time-window scans at a fraction of a b-tree's size.
    op.create_index(
        "ix_telemetry_measured_at_brin",
        "telemetry_readings",
        ["measured_at"],
        postgresql_using="brin",
    )
    # The per-device series read the excursion engine and time-series endpoint run most often.
    op.create_index(
        "ix_telemetry_device_measured_at", "telemetry_readings", ["device_id", "measured_at"]
    )

    op.create_table(
        "threshold_policies",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "clinic_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("clinics.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("min_temperature_c", sa.Float(), nullable=False),
        sa.Column("max_temperature_c", sa.Float(), nullable=False),
        sa.Column("dwell_minutes", sa.Float(), nullable=False),
        sa.Column("recovery_minutes", sa.Float(), nullable=False),
        sa.CheckConstraint(
            "(device_id IS NULL) <> (clinic_id IS NULL)",
            name="ck_threshold_policy_one_scope",
        ),
        *_TIMESTAMPS,
    )
    op.create_index(
        "uq_threshold_policy_device",
        "threshold_policies",
        ["device_id"],
        unique=True,
        postgresql_where=sa.text("device_id IS NOT NULL"),
    )
    op.create_index(
        "uq_threshold_policy_clinic",
        "threshold_policies",
        ["clinic_id"],
        unique=True,
        postgresql_where=sa.text("clinic_id IS NOT NULL"),
    )

    op.create_table(
        "excursions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "device_id",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("devices.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("direction", _EXCURSION_DIRECTION, nullable=False),
        sa.Column("peak_temperature_c", sa.Float(), nullable=False),
        sa.Column(
            "acknowledged_by",
            postgresql.UUID(as_uuid=True),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("acknowledged_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("device_id", "started_at", name="uq_excursion_device_started"),
        *_TIMESTAMPS,
    )
    op.create_index("ix_excursions_device_id", "excursions", ["device_id"])


def downgrade() -> None:
    op.drop_table("excursions")
    op.drop_table("threshold_policies")
    op.drop_table("telemetry_readings")
    op.drop_table("devices")
    op.execute("DROP TYPE IF EXISTS excursion_direction")
    op.execute("DROP TYPE IF EXISTS device_status")
