from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class ParticipantInfo:
    email: str
    time_zone: str | None
    name: str | None = None
    locale: str | None = None


@dataclass(frozen=True)
class OutboxRow:
    id: UUID
    event_ce_id: UUID
    event_type: str
    booking_uid: str
    payload: dict
    status: str
    attempts: int
    next_attempt_at: datetime
