from datetime import datetime, timedelta

from event_scheduling.interfaces.sql import ISqlExecutor
from event_scheduling.reminders.dto import DueBookingDTO


_DUE_SQL = """
    SELECT b.id, b.event_type_id, b.host_user_id, b.client_user_id,
           b.start_time, b.end_time, b.attendee_time_zone, et.title AS title
    FROM booking b
    JOIN event_type et ON et.id = b.event_type_id
    WHERE b.status = 'confirmed'
      AND b.reminder_sent_at IS NULL
      AND b.start_time >= :start_from
      AND b.start_time <= :start_to
    ORDER BY b.start_time ASC
    LIMIT :limit
"""


class ReminderReadAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def due_bookings(
        self, *, now: datetime, shift_from_minutes: int, shift_to_minutes: int, limit: int
    ) -> list[DueBookingDTO]:
        rows = await self._sql.fetch_all(
            _DUE_SQL,
            {
                "start_from": now + timedelta(minutes=shift_from_minutes),
                "start_to": now + timedelta(minutes=shift_to_minutes),
                "limit": limit,
            },
        )
        return [
            DueBookingDTO(
                id=r["id"],
                event_type_id=r["event_type_id"],
                host_user_id=r["host_user_id"],
                client_user_id=r["client_user_id"],
                start_time=r["start_time"],
                end_time=r["end_time"],
                attendee_time_zone=r["attendee_time_zone"],
                title=r["title"],
            )
            for r in rows
        ]
