from dataclasses import dataclass
from datetime import date, time


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
