from typing import Protocol
from uuid import UUID

from event_scheduling.slots.dto import SlotBundle


class ISlotsReadAdapter(Protocol):
    async def load(self, event_type_id: UUID) -> SlotBundle | None: ...
