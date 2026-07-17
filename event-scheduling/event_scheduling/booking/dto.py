from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID

from event_scheduling.booking_fields.dto import AnswerDTO, AnsweredFieldDTO


@dataclass(frozen=True)
class CreateBookingDTO:
    event_type_id: UUID
    client_user_id: UUID
    start_time: datetime
    attendee_time_zone: str
    field_answers: list[AnswerDTO] = field(default_factory=list)


@dataclass(frozen=True)
class BookingDTO:
    id: UUID
    event_type_id: UUID
    host_user_id: UUID
    client_user_id: UUID
    start_time: datetime
    end_time: datetime
    status: str
    attendee_time_zone: str
    created_at: datetime
    field_answers: list[AnsweredFieldDTO] = field(default_factory=list)


@dataclass(frozen=True)
class HostStat:
    user_id: UUID
    future_count: int
    last_assigned_at: datetime | None


@dataclass(frozen=True)
class ParticipantDetail:
    email: str
    name: str | None
    time_zone: str | None
    locale: str | None


@dataclass(frozen=True)
class BookingDetailDTO:
    uid: str
    title: str
    start_time: datetime
    end_time: datetime
    status: str
    host: ParticipantDetail
    client: ParticipantDetail


@dataclass(frozen=True)
class BookingChangeEntryDTO:
    kind: str
    from_start: datetime | None
    from_end: datetime | None
    to_start: datetime | None
    to_end: datetime | None
    actor_source: str
    actor_user_id: UUID | None
    at: datetime
