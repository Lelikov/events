"""booking write-side — booking + exclusion constraint + change log.

Revision ID: 0002
Revises: 0001
Create Date: 2026-07-12 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "booking",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_type_id", _UUID, nullable=False),
        sa.Column("host_user_id", _UUID, nullable=False),
        sa.Column("client_user_id", _UUID, nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'confirmed'"), nullable=False),
        sa.Column("attendee_time_zone", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["event_type_id"], ["event_type.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("end_time > start_time", name="ck_booking_range"),
        sa.CheckConstraint("status IN ('confirmed','cancelled')", name="ck_booking_status"),
    )
    op.create_index("ix_booking_host", "booking", ["host_user_id", "status", "start_time"])
    op.create_index("ix_booking_event_type", "booking", ["event_type_id", "status", "start_time"])
    op.create_index("ix_booking_client", "booking", ["client_user_id"])
    op.execute(
        "ALTER TABLE booking ADD CONSTRAINT ex_booking_no_overlap "
        "EXCLUDE USING gist (host_user_id WITH =, tstzrange(start_time, end_time) WITH &&) "
        "WHERE (status = 'confirmed')"
    )
    op.create_table(
        "booking_change_log",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("booking_id", _UUID, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("from_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("from_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("to_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("to_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor_source", sa.Text(), nullable=False),
        sa.Column("actor_user_id", _UUID, nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("kind IN ('created','rescheduled','cancelled')", name="ck_booking_log_kind"),
    )


def downgrade() -> None:
    op.drop_table("booking_change_log")
    op.drop_table("booking")  # drops ex_booking_no_overlap + indexes with it
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
