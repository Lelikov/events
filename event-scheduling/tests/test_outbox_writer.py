import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.booking.dto import BookingDTO
from event_scheduling.publishing.outbox_writer import OutboxWriter


def _booking(**kw) -> BookingDTO:
    base = {
        "id": uuid4(),
        "event_type_id": uuid4(),
        "host_user_id": uuid4(),
        "client_user_id": uuid4(),
        "start_time": dt.datetime(2026, 10, 1, 7, tzinfo=dt.UTC),
        "end_time": dt.datetime(2026, 10, 1, 8, tzinfo=dt.UTC),
        "status": "confirmed",
        "attendee_time_zone": "Europe/Moscow",
        "created_at": dt.datetime(2026, 7, 1, tzinfo=dt.UTC),
    }
    base.update(kw)
    return BookingDTO(**base)


@pytest.mark.asyncio
async def test_write_created_row(sessionmaker_fixture) -> None:
    b = _booking()
    async with sessionmaker_fixture() as s:
        await OutboxWriter(SqlExecutor(s)).write("booking.created", b)
        await s.commit()
    async with sessionmaker_fixture() as s:
        row = (
            await s.execute(
                text("SELECT event_type, booking_uid, status, payload FROM outbox WHERE booking_uid = :u"),
                {"u": str(b.id)},
            )
        ).one()
    assert row.event_type == "booking.created"
    assert row.status == "pending"
    assert row.payload["host_user_id"] == str(b.host_user_id)
    assert row.payload["attendee_time_zone"] == "Europe/Moscow"


@pytest.mark.asyncio
async def test_write_rescheduled_carries_previous(sessionmaker_fixture) -> None:
    b = _booking()
    prev = dt.datetime(2026, 10, 1, 6, tzinfo=dt.UTC)
    async with sessionmaker_fixture() as s:
        await OutboxWriter(SqlExecutor(s)).write("booking.rescheduled", b, previous_start_time=prev)
        await s.commit()
    async with sessionmaker_fixture() as s:
        payload = (
            await s.execute(text("SELECT payload FROM outbox WHERE booking_uid = :u"), {"u": str(b.id)})
        ).scalar_one()
    assert payload["previous_start_time"] == prev.isoformat()
