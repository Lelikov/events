from collections.abc import Sequence
from zoneinfo import ZoneInfo, available_timezones

from event_scheduling.dto.event_type import BookingLimitDTO
from event_scheduling.dto.schedule import DateOverrideDTO, WeeklyHourDTO
from event_scheduling.errors import ValidationError


_LIMIT_TYPES = {"booking_count", "booking_duration"}
_PERIODS = {"day", "week", "month", "year"}
_TZ_NAMES = available_timezones()


def validate_time_zone(tz: str) -> None:
    if tz not in _TZ_NAMES:
        raise ValidationError(f"Unknown time zone: {tz!r}")
    ZoneInfo(tz)  # cheap sanity, raises if the tzdata is unusable


def validate_weekly_hours(rows: Sequence[WeeklyHourDTO]) -> None:
    for r in rows:
        if not 1 <= r.day_of_week <= 7:
            raise ValidationError(f"day_of_week must be 1..7, got {r.day_of_week}")
        if r.end_time <= r.start_time:
            raise ValidationError(f"weekly_hours end_time must be > start_time (day {r.day_of_week})")


def validate_date_overrides(rows: Sequence[DateOverrideDTO]) -> None:
    for r in rows:
        both_null = r.start_time is None and r.end_time is None
        both_set = r.start_time is not None and r.end_time is not None
        if not (both_null or both_set):
            raise ValidationError(f"date_override {r.date}: start/end must both be null or both set")
        if both_set and r.end_time <= r.start_time:
            raise ValidationError(f"date_override {r.date}: end_time must be > start_time")


def validate_booking_limits(rows: Sequence[BookingLimitDTO]) -> None:
    for r in rows:
        if r.limit_type not in _LIMIT_TYPES:
            raise ValidationError(f"bad limit_type: {r.limit_type!r}")
        if r.period not in _PERIODS:
            raise ValidationError(f"bad period: {r.period!r}")
        if r.value <= 0:
            raise ValidationError("booking_limit value must be > 0")
