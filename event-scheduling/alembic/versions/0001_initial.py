"""initial schema — 8 tables.

Revision ID: 0001
Revises:
Create Date: 2026-07-03 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "schedule",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_user_id", _UUID, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("time_zone", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", name="uq_schedule_owner"),
    )

    op.create_table(
        "weekly_hours",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("schedule_id", _UUID, nullable=False),
        sa.Column("day_of_week", sa.SmallInteger(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=False),
        sa.Column("end_time", sa.Time(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["schedule_id"], ["schedule.id"], ondelete="CASCADE"),
        sa.CheckConstraint("day_of_week BETWEEN 1 AND 7", name="ck_weekly_hours_dow"),
        sa.CheckConstraint("end_time > start_time", name="ck_weekly_hours_range"),
    )

    op.create_table(
        "date_override",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("schedule_id", _UUID, nullable=False),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("start_time", sa.Time(), nullable=True),
        sa.Column("end_time", sa.Time(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["schedule_id"], ["schedule.id"], ondelete="CASCADE"),
        sa.CheckConstraint(
            "(start_time IS NULL AND end_time IS NULL) OR "
            "(start_time IS NOT NULL AND end_time IS NOT NULL AND end_time > start_time)",
            name="ck_date_override_range",
        ),
    )

    op.create_table(
        "travel_schedule",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("schedule_id", _UUID, nullable=False),
        sa.Column("time_zone", sa.Text(), nullable=False),
        sa.Column("start_date", sa.Date(), nullable=False),
        sa.Column("end_date", sa.Date(), nullable=True),
        sa.Column("prev_time_zone", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["schedule_id"], ["schedule.id"], ondelete="CASCADE"),
    )

    op.create_table(
        "event_type",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("slug", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("scheduling_type", sa.Text(), server_default=sa.text("'round_robin'"), nullable=False),
        sa.Column("duration_minutes", sa.Integer(), nullable=False),
        sa.Column("slot_interval_minutes", sa.Integer(), nullable=True),
        sa.Column("min_booking_notice_minutes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("buffer_before_minutes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("buffer_after_minutes", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("slug", name="uq_event_type_slug"),
    )

    op.create_table(
        "host",
        sa.Column("event_type_id", _UUID, nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("schedule_id", _UUID, nullable=False),
        sa.PrimaryKeyConstraint("event_type_id", "user_id"),
        sa.ForeignKeyConstraint(["event_type_id"], ["event_type.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["schedule_id"], ["schedule.id"], ondelete="RESTRICT"),
    )

    op.create_table(
        "booking_limit",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_type_id", _UUID, nullable=False),
        sa.Column("limit_type", sa.Text(), nullable=False),
        sa.Column("period", sa.Text(), nullable=False),
        sa.Column("value", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["event_type_id"], ["event_type.id"], ondelete="CASCADE"),
        sa.CheckConstraint("value > 0", name="ck_booking_limit_value"),
        sa.UniqueConstraint("event_type_id", "limit_type", "period", name="uq_booking_limit"),
    )

    op.create_table(
        "schedule_change_log",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_user_id", _UUID, nullable=False),
        sa.Column("schedule_id", _UUID, nullable=False),
        sa.Column("actor_source", sa.Text(), nullable=False),
        sa.Column("actor_user_id", _UUID, nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("snapshot", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )


def downgrade() -> None:
    for tbl in (
        "schedule_change_log",
        "booking_limit",
        "host",
        "event_type",
        "travel_schedule",
        "date_override",
        "weekly_hours",
        "schedule",
    ):
        op.drop_table(tbl)
