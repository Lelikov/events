"""Allow 'reassigned' in booking_change_log.kind (organizer reassignment).

Revision ID: 0007
Revises: 0006
"""

from collections.abc import Sequence

from alembic import op


revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("ck_booking_log_kind", "booking_change_log", type_="check")
    op.create_check_constraint(
        "ck_booking_log_kind",
        "booking_change_log",
        "kind IN ('created','rescheduled','reassigned','cancelled')",
    )
    op.drop_constraint("ck_outbox_type", "outbox", type_="check")
    op.create_check_constraint(
        "ck_outbox_type",
        "outbox",
        "event_type IN ('booking.created','booking.rescheduled','booking.reassigned','booking.cancelled')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_booking_log_kind", "booking_change_log", type_="check")
    op.create_check_constraint(
        "ck_booking_log_kind",
        "booking_change_log",
        "kind IN ('created','rescheduled','cancelled')",
    )
    op.drop_constraint("ck_outbox_type", "outbox", type_="check")
    op.create_check_constraint(
        "ck_outbox_type",
        "outbox",
        "event_type IN ('booking.created','booking.rescheduled','booking.cancelled')",
    )
