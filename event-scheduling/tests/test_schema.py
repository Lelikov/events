import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


EXPECTED_TABLES = {
    "schedule",
    "weekly_hours",
    "date_override",
    "travel_schedule",
    "event_type",
    "host",
    "booking_limit",
    "schedule_change_log",
}


@pytest.mark.asyncio
async def test_all_tables_exist(_migrated: str) -> None:
    eng = create_async_engine(_migrated)
    async with eng.connect() as conn:
        rows = await conn.execute(text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'"))
        tables = {r[0] for r in rows}
    await eng.dispose()
    assert tables >= EXPECTED_TABLES


@pytest.mark.asyncio
async def test_day_of_week_check_rejects_zero(_migrated: str) -> None:
    eng = create_async_engine(_migrated)
    async with eng.begin() as conn:
        sched = (
            await conn.execute(
                text(
                    "INSERT INTO schedule (owner_user_id, name, time_zone) "
                    "VALUES (gen_random_uuid(), 'x', 'Europe/Moscow') RETURNING id"
                )
            )
        ).scalar()
        with pytest.raises(Exception):  # noqa: B017,PT011 - CheckViolation; match not useful here
            await conn.execute(
                text(
                    "INSERT INTO weekly_hours (schedule_id, day_of_week, start_time, end_time) "
                    "VALUES (:s, 0, '09:00', '17:00')"
                ),
                {"s": sched},
            )
    await eng.dispose()
