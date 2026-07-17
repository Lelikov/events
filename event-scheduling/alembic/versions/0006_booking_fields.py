"""booking_field table + booking.field_answers (configurable booking fields, phase 1).

Revision ID: 0006
Revises: 0005
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op


revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)
_FIELD_TYPES = "('text','textarea','select','radio','checkbox','boolean')"


def upgrade() -> None:
    op.create_table(
        "booking_field",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_type_id", _UUID, nullable=False),
        sa.Column("field_key", sa.Text(), nullable=False),
        sa.Column("field_type", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("placeholder", sa.Text(), nullable=True),
        sa.Column("required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["event_type_id"], ["event_type.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("event_type_id", "field_key", name="uq_booking_field_key"),
        sa.CheckConstraint(f"field_type IN {_FIELD_TYPES}", name="ck_booking_field_type"),
    )
    op.create_index("ix_booking_field_event_type", "booking_field", ["event_type_id", "position"])
    op.add_column(
        "booking",
        sa.Column("field_answers", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("booking", "field_answers")
    op.drop_index("ix_booking_field_event_type", table_name="booking_field")
    op.drop_table("booking_field")
