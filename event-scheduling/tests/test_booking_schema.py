import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


_INSERT = (
    "INSERT INTO booking (event_type_id, host_user_id, client_user_id, start_time, end_time, "
    "status, attendee_time_zone) VALUES (:et, :h, :c, :s, :e, :st, 'Europe/Berlin')"
)


async def _seed_event_type_row(conn, et_id) -> None:
    await conn.execute(
        text("INSERT INTO event_type (id, slug, title, duration_minutes) VALUES (:id, :slug, 't', 60)"),
        {"id": et_id, "slug": f"et-{et_id}"},
    )


@pytest.mark.asyncio
async def test_overlapping_confirmed_same_host_rejected(_migrated: str) -> None:
    eng = create_async_engine(_migrated)
    et, host = uuid4(), uuid4()
    async with eng.begin() as conn:
        await _seed_event_type_row(conn, et)
        await conn.execute(
            text(_INSERT),
            {
                "et": et,
                "h": host,
                "c": uuid4(),
                "s": dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC),
                "e": dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC),
                "st": "confirmed",
            },
        )
    async with eng.begin() as conn:
        with pytest.raises(Exception):  # noqa: B017,PT011 - ExclusionViolation; match not useful here
            await conn.execute(
                text(_INSERT),
                {
                    "et": et,
                    "h": host,
                    "c": uuid4(),
                    "s": dt.datetime(2026, 10, 1, 9, 30, tzinfo=dt.UTC),
                    "e": dt.datetime(2026, 10, 1, 10, 30, tzinfo=dt.UTC),
                    "st": "confirmed",
                },
            )
    await eng.dispose()


@pytest.mark.asyncio
async def test_cancelled_does_not_block(_migrated: str) -> None:
    eng = create_async_engine(_migrated)
    et, host = uuid4(), uuid4()
    async with eng.begin() as conn:
        await _seed_event_type_row(conn, et)
        await conn.execute(
            text(_INSERT),
            {
                "et": et,
                "h": host,
                "c": uuid4(),
                "s": dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC),
                "e": dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC),
                "st": "cancelled",
            },
        )
        # overlapping confirmed is allowed because the other row is cancelled
        await conn.execute(
            text(_INSERT),
            {
                "et": et,
                "h": host,
                "c": uuid4(),
                "s": dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC),
                "e": dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC),
                "st": "confirmed",
            },
        )
    await eng.dispose()
