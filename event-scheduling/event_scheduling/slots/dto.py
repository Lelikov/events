from dataclasses import dataclass
from uuid import UUID

from event_scheduling.dto.schedule import DateOverrideDTO, TravelDTO, WeeklyHourDTO


@dataclass(frozen=True)
class EventTypeConfig:
    duration_minutes: int
    slot_interval_minutes: int | None
    min_booking_notice_minutes: int
    buffer_before_minutes: int
    buffer_after_minutes: int


@dataclass(frozen=True)
class HostSchedule:
    user_id: UUID
    time_zone: str
    weekly_hours: list[WeeklyHourDTO]
    date_overrides: list[DateOverrideDTO]
    travels: list[TravelDTO]


@dataclass(frozen=True)
class Interval:
    """Half-open [start, end) in epoch minutes (UTC)."""

    start: int
    end: int


@dataclass(frozen=True)
class SlotBundle:
    event_type: EventTypeConfig
    hosts: list[HostSchedule]
