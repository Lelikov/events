from datetime import datetime
from typing import Protocol
from uuid import UUID

from event_scheduling.booking.dto import BookingChangeEntryDTO, BookingDTO, CreateBookingDTO, HostStat
from event_scheduling.dto.event_type import BookingLimitDTO
from event_scheduling.dto.schedule import ActorDTO


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


class IBookingWriteAdapter(Protocol):
    async def insert(
        self, event_type_id: UUID, host_user_id: UUID, client_user_id: UUID, start: datetime, end: datetime, tz: str
    ) -> BookingDTO: ...
    async def update_times(self, booking_id: UUID, start: datetime, end: datetime) -> BookingDTO: ...
    async def set_cancelled(self, booking_id: UUID) -> BookingDTO: ...
    async def append_log(
        self,
        booking_id: UUID,
        kind: str,
        from_start: datetime | None,
        from_end: datetime | None,
        to_start: datetime | None,
        to_end: datetime | None,
        actor: ActorDTO,
    ) -> None: ...


class IBookingService(Protocol):
    async def create(self, dto: CreateBookingDTO, actor: ActorDTO) -> BookingDTO: ...
