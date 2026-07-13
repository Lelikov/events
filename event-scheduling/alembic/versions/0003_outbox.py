"""outbox — transactional outbox for booking.lifecycle CloudEvents.

Revision ID: 0003
Revises: 0002
Create Date: 2026-07-13 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_ce_id", _UUID, nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("booking_uid", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('pending','sent','failed')", name="ck_outbox_status"),
        sa.CheckConstraint(
            "event_type IN ('booking.created','booking.rescheduled','booking.cancelled')", name="ck_outbox_type"
        ),
    )
    op.create_index("ix_outbox_dispatch", "outbox", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_table("outbox")  # drops ix_outbox_dispatch with it
