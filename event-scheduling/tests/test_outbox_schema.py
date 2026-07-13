from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_outbox_insert_and_status_check(_migrated: str) -> None:
    eng = create_async_engine(_migrated)
    async with eng.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO outbox (event_ce_id, event_type, booking_uid, payload) "
                "VALUES (:ce, 'booking.created', :uid, CAST(:p AS jsonb))"
            ),
            {"ce": uuid4(), "uid": str(uuid4()), "p": '{"start_time":"x"}'},
        )
        row = (await conn.execute(text("SELECT status, attempts FROM outbox"))).one()
        assert row.status == "pending"
        assert row.attempts == 0
    async with eng.begin() as conn:
        with pytest.raises(Exception):  # noqa: B017,PT011 - CheckViolation; match not useful here
            await conn.execute(
                text(
                    "INSERT INTO outbox (event_ce_id, event_type, booking_uid, payload, status) "
                    "VALUES (:ce, 'booking.created', :uid, CAST('{}' AS jsonb), 'bogus')"
                ),
                {"ce": uuid4(), "uid": str(uuid4())},
            )
    await eng.dispose()
