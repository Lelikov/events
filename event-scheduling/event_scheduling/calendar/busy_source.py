from collections.abc import Sequence
from uuid import UUID

from event_scheduling.interfaces.busy_times import BusyInterval, TimeWindow
from event_scheduling.interfaces.sql import ISqlExecutor


_BUSY_SQL = """
    SELECT e.busy_start, e.busy_end
    FROM external_calendar_event e
    JOIN external_calendar c ON c.id = e.calendar_id
    WHERE c.enabled
      AND c.host_user_id = ANY(:users)
      AND tstzrange(e.busy_start, e.busy_end) && tstzrange(:lo, :hi)
"""


class ExternalCalendarBusyTimesSource:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def get_busy(self, user_ids: Sequence[UUID], window: TimeWindow) -> list[BusyInterval]:
        if not user_ids:
            return []
        rows = await self._sql.fetch_all(_BUSY_SQL, {"users": list(user_ids), "lo": window.start, "hi": window.end})
        return [BusyInterval(r["busy_start"], r["busy_end"]) for r in rows]
