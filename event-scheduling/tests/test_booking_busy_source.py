import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.booking.busy_source import BookingBusyTimesSource
from event_scheduling.booking.read_adapter import BookingReadAdapter
from event_scheduling.interfaces.busy_times import TimeWindow


async def _seed(session, *, buf_before=15, buf_after=15):
    et, host = uuid4(), uuid4()
    await session.execute(
        text(
            "INSERT INTO event_type (id, slug, title, duration_minutes, buffer_before_minutes, buffer_after_minutes) "
            "VALUES (:id, :slug, 't', 60, :bb, :ba)"
        ),
        {"id": et, "slug": f"et-{et}", "bb": buf_before, "ba": buf_after},
    )
    bid = uuid4()
    await session.execute(
        text(
            "INSERT INTO booking (id, event_type_id, host_user_id, client_user_id, start_time, end_time, "
            "status, attendee_time_zone) VALUES (:id, :et, :h, :c, :s, :e, 'confirmed', 'Europe/Berlin')"
        ),
        {
            "id": bid,
            "et": et,
            "h": host,
            "c": uuid4(),
            "s": dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC),
            "e": dt.datetime(2026, 10, 1, 13, tzinfo=dt.UTC),
        },
    )
    await session.commit()
    return et, host, bid


@pytest.mark.asyncio
async def test_busy_expands_by_buffers(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        _et, host, _ = await _seed(s, buf_before=15, buf_after=30)
    async with sessionmaker_fixture() as s:
        busy = await BookingBusyTimesSource(SqlExecutor(s)).get_busy(
            [host], TimeWindow(dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC))
        )
    assert len(busy) == 1
    assert busy[0].start == dt.datetime(2026, 10, 1, 11, 45, tzinfo=dt.UTC)  # 12:00 - 15min
    assert busy[0].end == dt.datetime(2026, 10, 1, 13, 30, tzinfo=dt.UTC)  # 13:00 + 30min


@pytest.mark.asyncio
async def test_busy_excludes_given_booking(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        _et, host, bid = await _seed(s)
    async with sessionmaker_fixture() as s:
        busy = await BookingBusyTimesSource(SqlExecutor(s)).get_busy(
            [host],
            TimeWindow(dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC)),
            exclude_booking_id=bid,
        )
    assert busy == []


@pytest.mark.asyncio
async def test_period_counts(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et, _host, _ = await _seed(s)
        count, minutes = await BookingReadAdapter(SqlExecutor(s)).period_counts(
            et, dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC)
        )
    assert count == 1
    assert minutes == 60
