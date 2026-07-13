"""ReminderReadAdapter.due_bookings: confirmed ∧ window ∧ not-yet-reminded."""

import datetime as dt
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.reminders.read_adapter import ReminderReadAdapter


NOW = dt.datetime(2026, 10, 1, 8, 0, tzinfo=dt.UTC)  # reminders fire for starts in [NOW+55m, NOW+65m]


async def _mk_event_type(s) -> UUID:
    et = uuid4()
    await s.execute(
        text("INSERT INTO event_type (id, slug, title, duration_minutes) VALUES (:id,:slug,'Intro call',60)"),
        {"id": et, "slug": f"et-{et}"},
    )
    return et


async def _mk_booking(s, et, *, start, status="confirmed", reminded=False) -> UUID:
    bid = uuid4()
    await s.execute(
        text(
            "INSERT INTO booking (id, event_type_id, host_user_id, client_user_id, start_time, end_time, "
            "status, attendee_time_zone, reminder_sent_at) VALUES "
            "(:id,:et,:h,:c,:st,:en,:status,'Europe/Berlin',:rem)"
        ),
        {
            "id": bid,
            "et": et,
            "h": uuid4(),
            "c": uuid4(),
            "st": start,
            "en": start + dt.timedelta(hours=1),
            "status": status,
            "rem": (start - dt.timedelta(minutes=30)) if reminded else None,
        },
    )
    return bid


@pytest.mark.asyncio
async def test_due_bookings_selects_only_confirmed_in_window_not_reminded(_clean_db, sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et = await _mk_event_type(s)
        due = await _mk_booking(s, et, start=NOW + dt.timedelta(minutes=60))  # in window
        await _mk_booking(s, et, start=NOW + dt.timedelta(minutes=30))  # too soon
        await _mk_booking(s, et, start=NOW + dt.timedelta(minutes=120))  # too far
        await _mk_booking(s, et, start=NOW + dt.timedelta(minutes=60), status="cancelled")
        await _mk_booking(s, et, start=NOW + dt.timedelta(minutes=60), reminded=True)
        await s.commit()

    async with sessionmaker_fixture() as s:
        rows = await ReminderReadAdapter(SqlExecutor(s)).due_bookings(
            now=NOW, shift_from_minutes=55, shift_to_minutes=65, limit=100
        )

    assert [r.id for r in rows] == [due]
    assert rows[0].title == "Intro call"
    assert rows[0].attendee_time_zone == "Europe/Berlin"


@pytest.mark.asyncio
async def test_due_bookings_respects_limit(_clean_db, sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et = await _mk_event_type(s)
        for _ in range(3):
            await _mk_booking(s, et, start=NOW + dt.timedelta(minutes=60))
        await s.commit()

    async with sessionmaker_fixture() as s:
        rows = await ReminderReadAdapter(SqlExecutor(s)).due_bookings(
            now=NOW, shift_from_minutes=55, shift_to_minutes=65, limit=2
        )
    assert len(rows) == 2
