from datetime import datetime
from typing import Protocol

from event_scheduling.booking.dto import BookingDTO


class IOutboxWriter(Protocol):
    async def write(
        self,
        event_type: str,
        booking: BookingDTO,
        *,
        previous_start_time: datetime | None = None,
        cancellation_reason: str | None = None,
    ) -> None: ...
