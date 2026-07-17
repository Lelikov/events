from dataclasses import dataclass, field
from uuid import UUID

from event_scheduling.booking_fields.dto import BookingFieldDTO


@dataclass(frozen=True)
class BookingLimitDTO:
    limit_type: str
    period: str
    value: int


@dataclass(frozen=True)
class HostDTO:
    user_id: UUID
    schedule_id: UUID


@dataclass(frozen=True)
class EventTypeDTO:
    id: UUID
    slug: str
    title: str
    scheduling_type: str
    duration_minutes: int
    slot_interval_minutes: int | None
    min_booking_notice_minutes: int
    buffer_before_minutes: int
    buffer_after_minutes: int
    hosts: list[HostDTO]
    booking_limits: list[BookingLimitDTO]
    booking_fields: list[BookingFieldDTO] = field(default_factory=list)


@dataclass(frozen=True)
class UpsertEventTypeDTO:
    slug: str
    title: str
    scheduling_type: str
    duration_minutes: int
    slot_interval_minutes: int | None
    min_booking_notice_minutes: int
    buffer_before_minutes: int
    buffer_after_minutes: int
    hosts: list[HostDTO]
    booking_limits: list[BookingLimitDTO]
