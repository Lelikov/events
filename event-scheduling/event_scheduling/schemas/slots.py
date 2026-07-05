from uuid import UUID

from pydantic import BaseModel


class SlotsResponse(BaseModel):
    event_type_id: UUID
    time_zone: str
    slots: dict[str, list[str]]
