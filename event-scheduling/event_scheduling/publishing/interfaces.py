from datetime import datetime
from typing import Protocol
from uuid import UUID

from event_scheduling.booking.dto import BookingDTO
from event_scheduling.publishing.dto import ParticipantInfo


class IOutboxWriter(Protocol):
    async def write(
        self,
        event_type: str,
        booking: BookingDTO,
        *,
        previous_start_time: datetime | None = None,
        cancellation_reason: str | None = None,
    ) -> None: ...


class IReceiverClient(Protocol):
    async def publish(self, ce_headers: dict[str, str], body: dict) -> int: ...


class IUsersClient(Protocol):
    async def by_ids(self, user_ids: list[UUID]) -> dict[UUID, ParticipantInfo]: ...
