from datetime import datetime
from typing import Protocol
from uuid import UUID

from event_scheduling.booking.dto import BookingChangeEntryDTO, BookingDTO, HostStat
from event_scheduling.dto.event_type import BookingLimitDTO


class IBookingReadAdapter(Protocol):
    async def get(self, booking_id: UUID) -> BookingDTO | None: ...
    async def list_by(
        self,
        host_user_id: UUID | None,
        client_user_id: UUID | None,
        from_utc: datetime | None,
        to_utc: datetime | None,
    ) -> list[BookingDTO]: ...
    async def history(self, booking_id: UUID) -> list[BookingChangeEntryDTO]: ...
    async def limits(self, event_type_id: UUID) -> list[BookingLimitDTO]: ...
    async def host_stats(self, user_ids: list[UUID], now: datetime) -> list[HostStat]: ...
    async def period_counts(self, event_type_id: UUID, lo: datetime, hi: datetime) -> tuple[int, int]: ...
