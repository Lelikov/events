from collections.abc import Sequence
from datetime import time

from event_scheduling.dto.event_type import BookingLimitDTO
from event_scheduling.dto.schedule import WeeklyHourDTO

# cal.com bookingLimits/durationLimits JSON keys → domain period
_PERIOD_MAP = {"PER_DAY": "day", "PER_WEEK": "week", "PER_MONTH": "month", "PER_YEAR": "year"}


def remap_day_of_week(calcom_day: int) -> int:
    """cal.com: 0=Sunday..6=Saturday → ISO: 1=Monday..7=Sunday."""
    if calcom_day == 0:
        return 7
    return calcom_day


def resolve_time_zone(schedule_tz: str | None, user_tz: str) -> str:
    if schedule_tz is not None:
        return schedule_tz
    return user_tz


def expand_weekly(days: Sequence[int], start: time, end: time) -> list[WeeklyHourDTO]:
    return [WeeklyHourDTO(remap_day_of_week(d), start, end) for d in days]


def expand_booking_limits(limits_json: dict, limit_type: str) -> list[BookingLimitDTO]:
    rows: list[BookingLimitDTO] = []
    for key, value in limits_json.items():
        period = _PERIOD_MAP.get(key)
        if period is None:
            continue
        rows.append(BookingLimitDTO(limit_type, period, int(value)))
    return rows
