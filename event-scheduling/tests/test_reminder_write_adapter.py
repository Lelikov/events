"""ReminderWriteAdapter.mark_sent stamps reminder_sent_at, guarded against overwrite."""

import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.reminders.write_adapter import ReminderWriteAdapter


T1 = dt.datetime(2026, 10, 1, 8, 5, tzinfo=dt.UTC)
T2 = dt.datetime(2026, 10, 1, 8, 9, tzinfo=dt.UTC)


async def _mk_booking(s):
    et, bid = uuid4(), uuid4()
    await s.execute(
        text("INSERT INTO event_type (id, slug, title, duration_minutes) VALUES (:id,:slug,'t',60)"),
        {"id": et, "slug": f"et-{et}"},
    )
    await s.execute(
        text(
            "INSERT INTO booking (id, event_type_id, host_user_id, client_user_id, start_time, end_time, "
            "attendee_time_zone) VALUES (:id,:et,:h,:c,'2026-10-01T09:00+00','2026-10-01T10:00+00','UTC')"
        ),
        {"id": bid, "et": et, "h": uuid4(), "c": uuid4()},
    )
    return bid


@pytest.mark.asyncio
async def test_mark_sent_stamps_and_is_idempotent(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        bid = await _mk_booking(s)
        await s.commit()

    async with sessionmaker_fixture() as s:
        await ReminderWriteAdapter(SqlExecutor(s)).mark_sent(bid, T1)
        await s.commit()

    async with sessionmaker_fixture() as s:
        first = (await s.execute(text("SELECT reminder_sent_at FROM booking WHERE id=:id"), {"id": bid})).scalar_one()
        assert first is not None

    # Second call must NOT overwrite (guard: reminder_sent_at IS NULL)
    async with sessionmaker_fixture() as s:
        await ReminderWriteAdapter(SqlExecutor(s)).mark_sent(bid, T2)
        await s.commit()

    async with sessionmaker_fixture() as s:
        second = (await s.execute(text("SELECT reminder_sent_at FROM booking WHERE id=:id"), {"id": bid})).scalar_one()
    assert second == first  # unchanged — guard held
