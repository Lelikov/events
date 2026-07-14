from __future__ import annotations
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from event_scheduling.calendar.dto import ExternalCalendarDTO


class CreateCalendarRequest(BaseModel):
    host_user_id: UUID
    url: str


class CalendarResponse(BaseModel):
    id: UUID
    host_user_id: UUID
    kind: str
    url: str
    enabled: bool
    last_synced_at: datetime | None
    last_error: str | None

    @classmethod
    def from_dto(cls, d: ExternalCalendarDTO) -> CalendarResponse:
        return cls(
            id=d.id,
            host_user_id=d.host_user_id,
            kind=d.kind,
            url=d.url,
            enabled=d.enabled,
            last_synced_at=d.last_synced_at,
            last_error=d.last_error,
        )


class CalendarListResponse(BaseModel):
    items: list[CalendarResponse]
