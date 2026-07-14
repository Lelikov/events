import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.calendar.sync_service import sync_calendar
from event_scheduling.calendar.write_adapter import CalendarWriteAdapter
from event_scheduling.interfaces.busy_times import BusyInterval


NOW = dt.datetime(2026, 9, 1, 12, tzinfo=dt.UTC)


class _Clock:
    def now(self):
        return NOW


class _OkClient:
    async def fetch(self, url):
        return b"ICS-BYTES"


class _BoomClient:
    async def fetch(self, url):
        raise RuntimeError("network down")


class _Parser:
    def expand(self, ics_bytes, window):
        return [BusyInterval(NOW + dt.timedelta(hours=1), NOW + dt.timedelta(hours=2))]


async def _mk_cal(s):
    return await CalendarWriteAdapter(SqlExecutor(s)).create(uuid4(), f"https://c/{uuid4()}.ics")


@pytest.mark.asyncio
async def test_sync_success_replaces_cache_and_marks(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        cal = await _mk_cal(s)
        await s.commit()
    async with sessionmaker_fixture() as s:
        await sync_calendar(SqlExecutor(s), _OkClient(), _Parser(), _Clock(), cal, window_days=62)
        await s.commit()
    async with sessionmaker_fixture() as s:
        n = (
            await s.execute(text("SELECT count(*) FROM external_calendar_event WHERE calendar_id=:c"), {"c": cal.id})
        ).scalar_one()
        assert n == 1
        row = (
            (
                await s.execute(
                    text("SELECT last_synced_at, last_error FROM external_calendar WHERE id=:c"), {"c": cal.id}
                )
            )
            .mappings()
            .one()
        )
        assert row["last_synced_at"] is not None
        assert row["last_error"] is None


@pytest.mark.asyncio
async def test_sync_fetch_failure_marks_error_and_keeps_cache(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        cal = await _mk_cal(s)
        await CalendarWriteAdapter(SqlExecutor(s)).replace_cache(
            cal.id, [BusyInterval(NOW, NOW + dt.timedelta(hours=1))]
        )
        await s.commit()
    async with sessionmaker_fixture() as s:
        await sync_calendar(SqlExecutor(s), _BoomClient(), _Parser(), _Clock(), cal, window_days=62)
        await s.commit()
    async with sessionmaker_fixture() as s:
        n = (
            await s.execute(text("SELECT count(*) FROM external_calendar_event WHERE calendar_id=:c"), {"c": cal.id})
        ).scalar_one()
        assert n == 1  # old cache preserved
        err = (
            await s.execute(text("SELECT last_error FROM external_calendar WHERE id=:c"), {"c": cal.id})
        ).scalar_one()
        assert err is not None
