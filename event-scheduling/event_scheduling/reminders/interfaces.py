from datetime import datetime
from typing import Protocol
from uuid import UUID

from event_scheduling.reminders.dto import DueBookingDTO


class IReminderReadAdapter(Protocol):
    async def due_bookings(
        self, *, now: datetime, shift_from_minutes: int, shift_to_minutes: int, limit: int
    ) -> list[DueBookingDTO]: ...


class IReminderWriteAdapter(Protocol):
    async def mark_sent(self, booking_id: UUID, now: datetime) -> None: ...
