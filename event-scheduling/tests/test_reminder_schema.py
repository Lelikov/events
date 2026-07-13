"""Migration 0004: booking.reminder_sent_at column + partial index; reschedule re-arms it."""

import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.booking.write_adapter import BookingWriteAdapter


@pytest.mark.asyncio
async def test_reminder_column_and_partial_index_exist(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        col = await s.execute(
            text(
                "SELECT is_nullable, data_type FROM information_schema.columns "
                "WHERE table_name='booking' AND column_name='reminder_sent_at'"
            )
        )
        row = col.mappings().one()
        assert row["is_nullable"] == "YES"
        assert row["data_type"] == "timestamp with time zone"

        idx = await s.execute(text("SELECT indexdef FROM pg_indexes WHERE indexname='ix_booking_reminder'"))
        indexdef = idx.scalar_one()
        assert "reminder_sent_at IS NULL" in indexdef
        assert "status" in indexdef
        assert "confirmed" in indexdef


@pytest.mark.asyncio
async def test_reschedule_clears_reminder_marker(sessionmaker_fixture) -> None:
    et_id, host, client = uuid4(), uuid4(), uuid4()
    async with sessionmaker_fixture() as s:
        await s.execute(
            text("INSERT INTO event_type (id, slug, title, duration_minutes) VALUES (:id, :slug, 't', 60)"),
            {"id": et_id, "slug": f"et-{et_id}"},
        )
        booking_id = (
            await s.execute(
                text(
                    "INSERT INTO booking (event_type_id, host_user_id, client_user_id, start_time, end_time, "
                    "attendee_time_zone, reminder_sent_at) VALUES "
                    "(:et,:h,:c, '2026-10-01T09:00+00','2026-10-01T10:00+00','Europe/Berlin', now()) RETURNING id"
                ),
                {"et": et_id, "h": host, "c": client},
            )
        ).scalar_one()
        await s.commit()

    async with sessionmaker_fixture() as s:
        adapter = BookingWriteAdapter(SqlExecutor(s))
        new_start = dt.datetime(2026, 10, 2, 9, tzinfo=dt.UTC)
        new_end = new_start + dt.timedelta(hours=1)
        await adapter.update_times(booking_id, new_start, new_end)
        await s.commit()

    async with sessionmaker_fixture() as s:
        marker = (
            await s.execute(text("SELECT reminder_sent_at FROM booking WHERE id=:id"), {"id": booking_id})
        ).scalar_one()
        assert marker is None
