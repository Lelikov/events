from datetime import date, time
from uuid import UUID

from pydantic import BaseModel

from event_scheduling.dto.schedule import (
    DateOverrideDTO,
    ScheduleBundleDTO,
    TravelDTO,
    UpsertScheduleDTO,
    WeeklyHourDTO,
)


class WeeklyHourModel(BaseModel):
    day_of_week: int
    start_time: time
    end_time: time


class DateOverrideModel(BaseModel):
    date: date
    start_time: time | None = None
    end_time: time | None = None


class TravelModel(BaseModel):
    time_zone: str
    start_date: date
    end_date: date | None = None
    prev_time_zone: str | None = None


class ReplaceTravelRequest(BaseModel):
    travel_schedules: list[TravelModel]

    def to_dtos(self) -> list[TravelDTO]:
        return [TravelDTO(t.time_zone, t.start_date, t.end_date, t.prev_time_zone) for t in self.travel_schedules]


class UpsertScheduleRequest(BaseModel):
    name: str
    time_zone: str
    weekly_hours: list[WeeklyHourModel]
    date_overrides: list[DateOverrideModel]

    def to_dto(self) -> UpsertScheduleDTO:
        return UpsertScheduleDTO(
            name=self.name,
            time_zone=self.time_zone,
            weekly_hours=[WeeklyHourDTO(w.day_of_week, w.start_time, w.end_time) for w in self.weekly_hours],
            date_overrides=[DateOverrideDTO(o.date, o.start_time, o.end_time) for o in self.date_overrides],
        )


class ScheduleModel(BaseModel):
    id: UUID
    owner_user_id: UUID
    name: str
    time_zone: str


class ScheduleBundleResponse(BaseModel):
    schedule: ScheduleModel
    weekly_hours: list[WeeklyHourModel]
    date_overrides: list[DateOverrideModel]
    travel_schedules: list[TravelModel]

    @classmethod
    def from_dto(cls, b: ScheduleBundleDTO) -> ScheduleBundleResponse:
        return cls(
            schedule=ScheduleModel(
                id=b.schedule.id,
                owner_user_id=b.schedule.owner_user_id,
                name=b.schedule.name,
                time_zone=b.schedule.time_zone,
            ),
            weekly_hours=[
                WeeklyHourModel(day_of_week=w.day_of_week, start_time=w.start_time, end_time=w.end_time)
                for w in b.weekly_hours
            ],
            date_overrides=[
                DateOverrideModel(date=o.date, start_time=o.start_time, end_time=o.end_time) for o in b.date_overrides
            ],
            travel_schedules=[
                TravelModel(
                    time_zone=t.time_zone,
                    start_date=t.start_date,
                    end_date=t.end_date,
                    prev_time_zone=t.prev_time_zone,
                )
                for t in b.travel_schedules
            ],
        )
