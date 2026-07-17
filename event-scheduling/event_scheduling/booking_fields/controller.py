from uuid import UUID

from event_scheduling.booking_fields.domain import assign_keys, validate_field_items
from event_scheduling.booking_fields.dto import BookingFieldDTO, UpsertBookingFieldDTO
from event_scheduling.booking_fields.interfaces import IBookingFieldAdapter
from event_scheduling.errors import NotFoundError


class BookingFieldController:
    def __init__(self, adapter: IBookingFieldAdapter) -> None:
        self._adapter = adapter

    async def list_for(self, event_type_id: UUID) -> list[BookingFieldDTO]:
        return await self._adapter.list_for(event_type_id)

    async def replace(self, event_type_id: UUID, items: list[UpsertBookingFieldDTO]) -> list[BookingFieldDTO]:
        validate_field_items(items)
        keys = assign_keys(items)
        if not await self._adapter.event_type_exists(event_type_id):
            raise NotFoundError(f"event_type {event_type_id} not found")
        return await self._adapter.replace(event_type_id, items, keys)
