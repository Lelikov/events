from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from event_scheduling.calendar.dto import ExternalCalendarDTO
from event_scheduling.calendar.read_adapter import _to_dto
from event_scheduling.errors import ConflictError
from event_scheduling.interfaces.busy_times import BusyInterval
from event_scheduling.interfaces.sql import ISqlExecutor


_COLS = "id, host_user_id, kind, url, enabled, last_synced_at, last_error"


class CalendarWriteAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def create(self, host_user_id: UUID, url: str) -> ExternalCalendarDTO:
        # SAVEPOINT so the uq_external_calendar_host_url IntegrityError doesn't abort
        # the outer transaction — same pattern as booking/write_adapter.py::insert.
        try:
            async with self._sql.begin_nested():
                row = await self._sql.fetch_one(
                    f"INSERT INTO external_calendar (host_user_id, url) VALUES (:h, :u) RETURNING {_COLS}",  # noqa: S608
                    {"h": host_user_id, "u": url},
                )
        except IntegrityError as exc:
            raise ConflictError("calendar already connected for this url") from exc
        return _to_dto(row)

    async def delete(self, calendar_id: UUID) -> None:
        await self._sql.execute("DELETE FROM external_calendar WHERE id=:id", {"id": calendar_id})

    async def replace_cache(self, calendar_id: UUID, events: Sequence[BusyInterval]) -> None:
        await self._sql.execute("DELETE FROM external_calendar_event WHERE calendar_id=:c", {"c": calendar_id})
        for ev in events:
            await self._sql.execute(
                "INSERT INTO external_calendar_event (calendar_id, busy_start, busy_end) VALUES (:c, :s, :e)",
                {"c": calendar_id, "s": ev.start, "e": ev.end},
            )

    async def mark_synced(self, calendar_id: UUID, now: datetime) -> None:
        await self._sql.execute(
            "UPDATE external_calendar SET last_synced_at=:n, last_error=NULL, updated_at=now() WHERE id=:id",
            {"n": now, "id": calendar_id},
        )

    async def mark_error(self, calendar_id: UUID, now: datetime, err: str) -> None:
        await self._sql.execute(
            "UPDATE external_calendar SET last_synced_at=:n, last_error=:e, updated_at=now() WHERE id=:id",
            {"n": now, "e": err, "id": calendar_id},
        )
