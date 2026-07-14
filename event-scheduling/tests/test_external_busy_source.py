import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.calendar.busy_source import ExternalCalendarBusyTimesSource
from event_scheduling.interfaces.busy_times import TimeWindow


WIN = TimeWindow(dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC))


async def _seed(
    s,
    host,
    *,
    enabled=True,
    busy=(dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC), dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC)),
):
    cal = uuid4()
    await s.execute(
        text("INSERT INTO external_calendar (id, host_user_id, url, enabled) VALUES (:id,:h,:u,:en)"),
        {"id": cal, "h": host, "u": f"https://c/{cal}.ics", "en": enabled},
    )
    await s.execute(
        text("INSERT INTO external_calendar_event (calendar_id, busy_start, busy_end) VALUES (:c,:s,:e)"),
        {"c": cal, "s": busy[0], "e": busy[1]},
    )
    return cal


@pytest.mark.asyncio
async def test_returns_busy_for_host_in_window(sessionmaker_fixture) -> None:
    host = uuid4()
    async with sessionmaker_fixture() as s:
        await _seed(s, host)
        await s.commit()
    async with sessionmaker_fixture() as s:
        out = await ExternalCalendarBusyTimesSource(SqlExecutor(s)).get_busy([host], WIN)
    assert len(out) == 1


@pytest.mark.asyncio
async def test_excludes_disabled_and_other_host_and_out_of_window(sessionmaker_fixture) -> None:
    host, other = uuid4(), uuid4()
    async with sessionmaker_fixture() as s:
        await _seed(s, host, enabled=False)
        await _seed(s, other)
        out_of_window = (dt.datetime(2026, 11, 1, 9, tzinfo=dt.UTC), dt.datetime(2026, 11, 1, 10, tzinfo=dt.UTC))
        await _seed(s, host, busy=out_of_window)
        await s.commit()
    async with sessionmaker_fixture() as s:
        out = await ExternalCalendarBusyTimesSource(SqlExecutor(s)).get_busy([host], WIN)
    assert out == []
