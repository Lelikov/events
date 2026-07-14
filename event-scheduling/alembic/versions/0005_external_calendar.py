"""external_calendar + external_calendar_event (slice 5, calendar-sync).

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "external_calendar",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("host_user_id", _UUID, nullable=False),
        sa.Column("kind", sa.Text(), server_default=sa.text("'ical_url'"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("kind IN ('ical_url')", name="ck_external_calendar_kind"),
        sa.UniqueConstraint("host_user_id", "url", name="uq_external_calendar_host_url"),
    )
    op.execute("CREATE INDEX ix_external_calendar_enabled ON external_calendar (host_user_id) WHERE enabled")
    op.create_table(
        "external_calendar_event",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("calendar_id", _UUID, nullable=False),
        sa.Column("busy_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("busy_end", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["calendar_id"], ["external_calendar.id"], ondelete="CASCADE"),
        sa.CheckConstraint("busy_end > busy_start", name="ck_ext_cal_event_range"),
    )
    op.create_index("ix_ext_cal_event_window", "external_calendar_event", ["calendar_id", "busy_start", "busy_end"])


def downgrade() -> None:
    op.drop_table("external_calendar_event")
    op.drop_index("ix_external_calendar_enabled", table_name="external_calendar")
    op.drop_table("external_calendar")
