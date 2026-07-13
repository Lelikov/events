from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_outbox_insert_and_status_check(_migrated: str) -> None:
    eng = create_async_engine(_migrated)
    async with eng.begin() as conn:
        uid = str(uuid4())
        await conn.execute(
            text(
                "INSERT INTO outbox (event_ce_id, event_type, booking_uid, payload) "
                "VALUES (:ce, 'booking.created', :uid, CAST(:p AS jsonb))"
            ),
            {"ce": uuid4(), "uid": uid, "p": '{"start_time":"x"}'},
        )
        query = text("SELECT status, attempts FROM outbox WHERE booking_uid = :uid")
        row = (await conn.execute(query, {"uid": uid})).one()
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
