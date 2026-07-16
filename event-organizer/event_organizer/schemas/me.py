from __future__ import annotations

from datetime import date, time

from pydantic import BaseModel


class WeeklyHourModel(BaseModel):
    day_of_week: int
    start_time: time
    end_time: time


class DateOverrideModel(BaseModel):
    date: date
    start_time: time | None = None
    end_time: time | None = None


class SchedulePutRequest(BaseModel):
    time_zone: str
    weekly_hours: list[WeeklyHourModel]
    date_overrides: list[DateOverrideModel]


class ProfileResponse(BaseModel):
    name: str | None
    email: str
    time_zone: str | None


class ProfilePutRequest(BaseModel):
    name: str
    time_zone: str


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str


class BookingItem(BaseModel):
    id: str
    start_time: str
    end_time: str
    status: str
