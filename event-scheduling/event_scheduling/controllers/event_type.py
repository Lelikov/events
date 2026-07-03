from uuid import UUID

from event_scheduling.dto.event_type import EventTypeDTO, UpsertEventTypeDTO
from event_scheduling.errors import NotFoundError
from event_scheduling.interfaces.event_type import IEventTypeDBAdapter
from event_scheduling.validation import validate_booking_limits


class EventTypeController:
    def __init__(self, db: IEventTypeDBAdapter) -> None:
        self._db = db

    async def create(self, dto: UpsertEventTypeDTO) -> EventTypeDTO:
        validate_booking_limits(dto.booking_limits)
        return await self._db.insert(dto)

    async def get(self, event_type_id: UUID) -> EventTypeDTO:
        result = await self._db.get(event_type_id)
        if result is None:
            raise NotFoundError(f"event_type {event_type_id} not found")
        return result

    async def list_all(self) -> list[EventTypeDTO]:
        return await self._db.list_all()

    async def update(self, event_type_id: UUID, dto: UpsertEventTypeDTO) -> EventTypeDTO:
        validate_booking_limits(dto.booking_limits)
        result = await self._db.update(event_type_id, dto)
        if result is None:
            raise NotFoundError(f"event_type {event_type_id} not found")
        return result

    async def delete(self, event_type_id: UUID) -> None:
        found = await self._db.delete(event_type_id)
        if not found:
            raise NotFoundError(f"event_type {event_type_id} not found")
