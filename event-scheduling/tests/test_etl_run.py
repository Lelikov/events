"""Integration test: ETL migrates cal.com schedules into the event_scheduling DB."""

from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from scripts.etl_from_calcom import run_etl


@pytest.mark.asyncio
@pytest.mark.usefixtures("_clean_db")
async def test_etl_migrates_default_schedule_and_skips_extra(
    _migrated: str, calcom_dsn: str  # noqa: PT019
) -> None:
    uid = uuid4()
    report = await run_etl(
        calcom_dsn=calcom_dsn,
        target_dsn=_migrated,
        resolve_email_to_uuid=lambda email: uid if email == "org@example.com" else None,
    )
    assert report.migrated["schedule"] == 1
    assert report.skipped["schedule"] >= 1  # non-default schedule skipped
    assert any(reason for entity, reason in report.skips if entity == "schedule")

    eng = create_async_engine(_migrated)
    async with eng.connect() as conn:
        wh = (await conn.execute(text("SELECT day_of_week FROM weekly_hours ORDER BY day_of_week"))).scalars().all()
        assert wh == [1, 3]
        baseline = (
            await conn.execute(text("SELECT count(*) FROM schedule_change_log WHERE actor_source = 'etl'"))
        ).scalar()
        assert baseline == 1  # baseline snapshot
        do_count = (await conn.execute(text("SELECT count(*) FROM date_override"))).scalar()
        assert do_count == 1  # (a) exactly one date-override row for the migrated schedule
        snap = (
            await conn.execute(
                text("SELECT snapshot FROM schedule_change_log WHERE actor_source = 'etl' LIMIT 1")
            )
        ).scalar()
        # asyncpg deserializes jsonb columns to dict automatically
        assert snap["schedule"]["time_zone"]  # (b) time_zone present in snapshot
        assert len(snap["weekly_hours"]) == 2  # (b) two weekly_hours entries matching [1, 3]
    await eng.dispose()
