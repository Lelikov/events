from collections.abc import Sequence
from uuid import UUID

from event_scheduling.interfaces.busy_times import BusyInterval, TimeWindow
from event_scheduling.interfaces.sql import ISqlExecutor


class BookingBusyTimesSource:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def get_busy(
        self, user_ids: Sequence[UUID], window: TimeWindow, exclude_booking_id: UUID | None = None
    ) -> list[BusyInterval]:
        rows = await self._sql.fetch_all(
            """
            SELECT b.start_time - make_interval(mins => et.buffer_before_minutes) AS busy_start,
                   b.end_time   + make_interval(mins => et.buffer_after_minutes)  AS busy_end
            FROM booking b
            JOIN event_type et ON et.id = b.event_type_id
            WHERE b.host_user_id = ANY(:users)
              AND b.status = 'confirmed'
              AND tstzrange(b.start_time, b.end_time) && tstzrange(:win_lo, :win_hi)
              AND (CAST(:exclude AS uuid) IS NULL OR b.id <> CAST(:exclude AS uuid))
            """,
            {"users": list(user_ids), "win_lo": window.start, "win_hi": window.end, "exclude": exclude_booking_id},
        )
        return [BusyInterval(r["busy_start"], r["busy_end"]) for r in rows]
