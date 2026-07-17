"""BookingWriteAdapter.insert SAVEPOINT behaviour (slice 3, Task 5).

The exclusion constraint (ex_booking_no_overlap) makes a conflicting insert raise
IntegrityError, which aborts the OUTER transaction unless the attempt runs inside
its own SAVEPOINT (session.begin_nested()). These tests prove both halves of that
contract: (1) a conflicting insert is surfaced as ConflictError, and (2) the same
session/transaction stays usable for further statements afterwards — the property
BookingService.create's retry-over-ranked-hosts loop depends on.
"""

import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.booking.write_adapter import BookingWriteAdapter
from event_scheduling.dto.schedule import ActorDTO
from event_scheduling.errors import ConflictError


START = dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC)
END = dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC)
ACTOR = ActorDTO(source="api", user_id=None)


@pytest.mark.asyncio
async def test_conflicting_insert_raises_conflict_and_session_stays_usable(sessionmaker_fixture) -> None:
    et_id = uuid4()
    host = uuid4()

    async with sessionmaker_fixture() as s:
        await s.execute(
            text("INSERT INTO event_type (id, slug, title, duration_minutes) VALUES (:id, :slug, 't', 60)"),
            {"id": et_id, "slug": f"et-{et_id}"},
        )
        await s.commit()

    async with sessionmaker_fixture() as s:
        adapter = BookingWriteAdapter(SqlExecutor(s))

        first = await adapter.insert(et_id, host, uuid4(), START, END, "Europe/Berlin", [])
        assert first.status == "confirmed"

        # Same host, overlapping window -> exclusion constraint -> IntegrityError -> ConflictError.
        # This must NOT abort the outer transaction (proves the SAVEPOINT rolled back cleanly).
        with pytest.raises(ConflictError):
            await adapter.insert(et_id, host, uuid4(), START, END, "Europe/Berlin", [])

        # The session must still be usable for further statements in the SAME transaction —
        # this is exactly what BookingService.create relies on to retry the next ranked host.
        other_host = uuid4()
        second = await adapter.insert(et_id, other_host, uuid4(), START, END, "Europe/Berlin", [])
        assert second.status == "confirmed"
        assert second.host_user_id == other_host

        await adapter.append_log(second.id, "created", None, None, START, END, actor=ACTOR)
        await s.commit()
