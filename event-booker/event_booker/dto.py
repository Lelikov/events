from dataclasses import dataclass, field
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class OptionDTO:
    value: str
    label: str


@dataclass(frozen=True)
class BookingFieldDTO:
    field_key: str
    field_type: str
    label: str
    placeholder: str | None
    required: bool
    options: list[OptionDTO]


@dataclass(frozen=True)
class AnswerDTO:
    key: str
    value: str | list[str] | bool


@dataclass(frozen=True)
class EventTypeDTO:
    id: UUID
    slug: str
    title: str
    duration_minutes: int
    booking_fields: list[BookingFieldDTO] = field(default_factory=list)


@dataclass(frozen=True)
class SlotsResult:
    event_type_id: UUID
    time_zone: str
    slots: dict[str, list[str]]


@dataclass(frozen=True)
class BookingResult:
    id: UUID
    start_time: datetime
    end_time: datetime
    status: str


@dataclass(frozen=True)
class BookingConfirmation:
    booking_id: UUID
    event_type_title: str
    start_time: datetime
    end_time: datetime
    status: str
    time_zone: str
