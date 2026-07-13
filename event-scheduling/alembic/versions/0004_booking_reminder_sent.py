"""booking.reminder_sent_at + partial index (slice 4a.3).

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op


revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("booking", sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "CREATE INDEX ix_booking_reminder ON booking (start_time) "
        "WHERE status = 'confirmed' AND reminder_sent_at IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_booking_reminder", table_name="booking")
    op.drop_column("booking", "reminder_sent_at")
