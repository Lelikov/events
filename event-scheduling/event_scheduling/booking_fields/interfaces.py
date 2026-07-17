from typing import Protocol
from uuid import UUID

from event_scheduling.booking_fields.dto import BookingFieldDTO, UpsertBookingFieldDTO


class IBookingFieldAdapter(Protocol):
    async def list_for(self, event_type_id: UUID) -> list[BookingFieldDTO]: ...
    async def replace(
        self, event_type_id: UUID, items: list[UpsertBookingFieldDTO], keys: list[str]
    ) -> list[BookingFieldDTO]: ...
    async def event_type_exists(self, event_type_id: UUID) -> bool: ...


class IBookingFieldController(Protocol):
    async def list_for(self, event_type_id: UUID) -> list[BookingFieldDTO]: ...
    async def replace(self, event_type_id: UUID, items: list[UpsertBookingFieldDTO]) -> list[BookingFieldDTO]: ...
