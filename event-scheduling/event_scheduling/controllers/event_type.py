from uuid import UUID

from event_scheduling.dto.event_type import EventTypeDTO, HostDTO, UpsertEventTypeDTO
from event_scheduling.errors import NotFoundError, ValidationError
from event_scheduling.interfaces.event_type import IEventTypeDBAdapter
from event_scheduling.validation import validate_booking_limits, validate_hosts


class EventTypeController:
    def __init__(self, db: IEventTypeDBAdapter) -> None:
        self._db = db

    async def _validate_host_schedules(self, hosts: list[HostDTO]) -> None:
        for h in hosts:
            owner = await self._db.get_schedule_owner(h.schedule_id)
            if owner is None:
                raise ValidationError(f"host schedule {h.schedule_id} not found")
            if owner != h.user_id:
                raise ValidationError(f"host schedule {h.schedule_id} does not belong to host user {h.user_id}")

    async def create(self, dto: UpsertEventTypeDTO) -> EventTypeDTO:
        validate_hosts(dto.hosts)
        validate_booking_limits(dto.booking_limits)
        await self._validate_host_schedules(dto.hosts)
        return await self._db.insert(dto)

    async def get(self, event_type_id: UUID) -> EventTypeDTO:
        result = await self._db.get(event_type_id)
        if result is None:
            raise NotFoundError(f"event_type {event_type_id} not found")
        return result

    async def list_all(self) -> list[EventTypeDTO]:
        return await self._db.list_all()

    async def update(self, event_type_id: UUID, dto: UpsertEventTypeDTO) -> EventTypeDTO:
        validate_hosts(dto.hosts)
        validate_booking_limits(dto.booking_limits)
        await self._validate_host_schedules(dto.hosts)
        result = await self._db.update(event_type_id, dto)
        if result is None:
            raise NotFoundError(f"event_type {event_type_id} not found")
        return result

    async def delete(self, event_type_id: UUID) -> None:
        found = await self._db.delete(event_type_id)
        if not found:
            raise NotFoundError(f"event_type {event_type_id} not found")
