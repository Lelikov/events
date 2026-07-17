"""Migration 0006: booking_field table + booking.field_answers (configurable booking fields, phase 1)."""

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_booking_field_table_and_answers_column_exist(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        # booking_field columns
        cols = (
            (
                await s.execute(
                    text("SELECT column_name FROM information_schema.columns WHERE table_name='booking_field'")
                )
            )
            .scalars()
            .all()
        )
        for c in (
            "id",
            "event_type_id",
            "field_key",
            "field_type",
            "label",
            "placeholder",
            "required",
            "options",
            "position",
        ):
            assert c in cols

        # field_answers on booking, defaulting to []
        default = (
            await s.execute(
                text(
                    "SELECT column_default FROM information_schema.columns "
                    "WHERE table_name='booking' AND column_name='field_answers'"
                )
            )
        ).scalar_one()
        assert "'[]'" in default

        # CHECK rejects an unknown type
        with pytest.raises(Exception):  # noqa: B017,PT011 - CheckViolation; match not useful here
            await s.execute(
                text(
                    "INSERT INTO booking_field (event_type_id, field_key, field_type, label, position) "
                    "VALUES (gen_random_uuid(), 'k', 'bogus', 'L', 0)"
                )
            )
