from uuid import UUID

from event_scheduling.calendar.dto import ExternalCalendarDTO
from event_scheduling.interfaces.sql import ISqlExecutor


_COLS = "id, host_user_id, kind, url, enabled, last_synced_at, last_error"


def _to_dto(r: dict) -> ExternalCalendarDTO:
    return ExternalCalendarDTO(
        id=r["id"],
        host_user_id=r["host_user_id"],
        kind=r["kind"],
        url=r["url"],
        enabled=r["enabled"],
        last_synced_at=r["last_synced_at"],
        last_error=r["last_error"],
    )


class CalendarReadAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def list_enabled(self) -> list[ExternalCalendarDTO]:
        rows = await self._sql.fetch_all(
            f"SELECT {_COLS} FROM external_calendar WHERE enabled ORDER BY created_at",  # noqa: S608
            {},
        )
        return [_to_dto(r) for r in rows]

    async def list_by_host(self, host_user_id: UUID) -> list[ExternalCalendarDTO]:
        rows = await self._sql.fetch_all(
            f"SELECT {_COLS} FROM external_calendar WHERE host_user_id=:h ORDER BY created_at",  # noqa: S608
            {"h": host_user_id},
        )
        return [_to_dto(r) for r in rows]

    async def get(self, calendar_id: UUID) -> ExternalCalendarDTO | None:
        row = await self._sql.fetch_one(
            f"SELECT {_COLS} FROM external_calendar WHERE id=:id",  # noqa: S608
            {"id": calendar_id},
        )
        if row is None:
            return None
        return _to_dto(row)
