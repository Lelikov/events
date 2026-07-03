from dataclasses import dataclass
from datetime import date, datetime, time
from uuid import UUID


@dataclass(frozen=True)
class WeeklyHourDTO:
    day_of_week: int
    start_time: time
    end_time: time


@dataclass(frozen=True)
class DateOverrideDTO:
    date: date
    start_time: time | None
    end_time: time | None


@dataclass(frozen=True)
class ScheduleDTO:
    id: UUID
    owner_user_id: UUID
    name: str
    time_zone: str


@dataclass(frozen=True)
class TravelDTO:
    time_zone: str
    start_date: date
    end_date: date | None
    prev_time_zone: str | None


@dataclass(frozen=True)
class ScheduleBundleDTO:
    schedule: ScheduleDTO
    weekly_hours: list[WeeklyHourDTO]
    date_overrides: list[DateOverrideDTO]
    travel_schedules: list[TravelDTO]


@dataclass(frozen=True)
class UpsertScheduleDTO:
    name: str
    time_zone: str
    weekly_hours: list[WeeklyHourDTO]
    date_overrides: list[DateOverrideDTO]


@dataclass(frozen=True)
class ActorDTO:
    source: str
    user_id: UUID | None


@dataclass(frozen=True)
class ChangeLogEntryDTO:
    id: UUID
    at: datetime
    actor_source: str
    actor_user_id: UUID | None
    snapshot: dict
