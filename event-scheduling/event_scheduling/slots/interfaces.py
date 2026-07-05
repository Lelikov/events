from datetime import datetime
from typing import Protocol
from uuid import UUID

from event_scheduling.slots.dto import SlotBundle


class ISlotsReadAdapter(Protocol):
    async def load(self, event_type_id: UUID) -> SlotBundle | None: ...


class Clock(Protocol):
    def now(self) -> datetime: ...


class ISlotService(Protocol):
    async def available_slots(
        self, event_type_id: UUID, window_start: datetime, window_end: datetime, time_zone: str
    ) -> dict[str, list[str]]: ...
