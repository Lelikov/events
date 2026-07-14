"""Migration 0005: external_calendar + external_calendar_event."""

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_external_calendar_tables_exist(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        cols = await s.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='external_calendar' ORDER BY column_name"
            )
        )
        names = {r[0] for r in cols}
        assert {"id", "host_user_id", "kind", "url", "enabled", "last_synced_at", "last_error"} <= names

        kind_ck = await s.execute(text("SELECT 1 FROM pg_constraint WHERE conname='ck_external_calendar_kind'"))
        assert kind_ck.first() is not None

        ev = await s.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name='external_calendar_event'")
        )
        assert {"calendar_id", "busy_start", "busy_end"} <= {r[0] for r in ev}


@pytest.mark.asyncio
async def test_event_cache_cascades_on_calendar_delete(sessionmaker_fixture) -> None:
    from uuid import uuid4

    cal_id, host = uuid4(), uuid4()
    async with sessionmaker_fixture() as s:
        await s.execute(
            text("INSERT INTO external_calendar (id, host_user_id, url) VALUES (:id,:h,'https://x/c.ics')"),
            {"id": cal_id, "h": host},
        )
        await s.execute(
            text(
                "INSERT INTO external_calendar_event (calendar_id, busy_start, busy_end) "
                "VALUES (:c, '2026-10-01T09:00+00','2026-10-01T10:00+00')"
            ),
            {"c": cal_id},
        )
        await s.commit()
    async with sessionmaker_fixture() as s:
        await s.execute(text("DELETE FROM external_calendar WHERE id=:id"), {"id": cal_id})
        await s.commit()
    async with sessionmaker_fixture() as s:
        left = await s.execute(text("SELECT count(*) FROM external_calendar_event WHERE calendar_id=:c"), {"c": cal_id})
        assert left.scalar_one() == 0
