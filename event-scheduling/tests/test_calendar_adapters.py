import datetime as dt
from uuid import uuid4

import pytest

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.calendar.read_adapter import CalendarReadAdapter
from event_scheduling.calendar.write_adapter import CalendarWriteAdapter
from event_scheduling.errors import ConflictError
from event_scheduling.interfaces.busy_times import BusyInterval


NOW = dt.datetime(2026, 9, 1, 12, tzinfo=dt.UTC)


@pytest.mark.asyncio
async def test_create_list_get_and_duplicate(sessionmaker_fixture) -> None:
    host = uuid4()
    async with sessionmaker_fixture() as s:
        w = CalendarWriteAdapter(SqlExecutor(s))
        created = await w.create(host, "https://cal/x.ics")
        await s.commit()
        assert created.host_user_id == host
        assert created.enabled is True
        assert created.kind == "ical_url"

    async with sessionmaker_fixture() as s:
        r = CalendarReadAdapter(SqlExecutor(s))
        by_host = await r.list_by_host(host)
        assert [c.id for c in by_host] == [created.id]
        assert (await r.get(created.id)).url == "https://cal/x.ics"
        assert await r.list_enabled()  # non-empty

    async with sessionmaker_fixture() as s:
        w = CalendarWriteAdapter(SqlExecutor(s))
        with pytest.raises(ConflictError):
            await w.create(host, "https://cal/x.ics")  # (host,url) unique


@pytest.mark.asyncio
async def test_replace_cache_and_mark(sessionmaker_fixture) -> None:
    host = uuid4()
    async with sessionmaker_fixture() as s:
        w = CalendarWriteAdapter(SqlExecutor(s))
        cal = await w.create(host, "https://cal/y.ics")
        await w.replace_cache(cal.id, [BusyInterval(NOW, NOW + dt.timedelta(hours=1))])
        await w.mark_synced(cal.id, NOW)
        await s.commit()

    async with sessionmaker_fixture() as s:
        from sqlalchemy import text

        result = await s.execute(
            text("SELECT count(*) FROM external_calendar_event WHERE calendar_id=:c"), {"c": cal.id}
        )
        assert result.scalar_one() == 1
        r = CalendarReadAdapter(SqlExecutor(s))
        assert (await r.get(cal.id)).last_synced_at is not None

    # replace overwrites (delete+insert)
    async with sessionmaker_fixture() as s:
        w = CalendarWriteAdapter(SqlExecutor(s))
        await w.replace_cache(cal.id, [])
        await w.mark_error(cal.id, NOW, "boom")
        await s.commit()
    async with sessionmaker_fixture() as s:
        from sqlalchemy import text

        result = await s.execute(
            text("SELECT count(*) FROM external_calendar_event WHERE calendar_id=:c"), {"c": cal.id}
        )
        assert result.scalar_one() == 0
        r = CalendarReadAdapter(SqlExecutor(s))
        assert (await r.get(cal.id)).last_error == "boom"
