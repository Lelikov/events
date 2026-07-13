from datetime import datetime
from uuid import UUID

from event_scheduling.interfaces.sql import ISqlExecutor


_MARK_SENT_SQL = "UPDATE booking SET reminder_sent_at=:now WHERE id=:id AND reminder_sent_at IS NULL"


class ReminderWriteAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def mark_sent(self, booking_id: UUID, now: datetime) -> None:
        await self._sql.execute(_MARK_SENT_SQL, {"id": booking_id, "now": now})
