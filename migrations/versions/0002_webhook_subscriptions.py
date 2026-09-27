"""Webhook subscriptions.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-27

Adds the table backing signed outbound webhooks (spec §4 rule 8). Hand-written to match the style
of 0001; ``event_types`` is a Postgres ``text[]`` so a membership test runs in the database.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "webhook_subscriptions",
        sa.Column("id", sa.Uuid(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            "clinic_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("clinics.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("url", sa.String(length=2048), nullable=False),
        sa.Column("secret", sa.String(length=255), nullable=False),
        sa.Column("event_types", postgresql.ARRAY(sa.String()), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default=sa.text("true"), nullable=False),
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
    op.create_index("ix_webhook_subscriptions_clinic_id", "webhook_subscriptions", ["clinic_id"])


def downgrade() -> None:
    op.drop_index("ix_webhook_subscriptions_clinic_id", table_name="webhook_subscriptions")
    op.drop_table("webhook_subscriptions")
