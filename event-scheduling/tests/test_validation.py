import datetime as dt

import pytest

from event_scheduling.dto.event_type import BookingLimitDTO
from event_scheduling.dto.schedule import DateOverrideDTO, WeeklyHourDTO
from event_scheduling.errors import ValidationError
from event_scheduling.validation import (
    validate_booking_limits,
    validate_date_overrides,
    validate_time_zone,
    validate_weekly_hours,
)


def test_time_zone_valid_and_invalid() -> None:
    validate_time_zone("Europe/Moscow")  # no raise
    with pytest.raises(ValidationError):
        validate_time_zone("Mars/Phobos")


def test_weekly_hours_rejects_bad_day_and_range() -> None:
    with pytest.raises(ValidationError):
        validate_weekly_hours([WeeklyHourDTO(0, dt.time(9), dt.time(17))])
    with pytest.raises(ValidationError):
        validate_weekly_hours([WeeklyHourDTO(1, dt.time(17), dt.time(9))])
    validate_weekly_hours([WeeklyHourDTO(1, dt.time(9), dt.time(17))])  # ok


def test_date_override_null_invariant() -> None:
    validate_date_overrides([DateOverrideDTO(dt.date(2026, 1, 1), None, None)])  # day off ok
    validate_date_overrides([DateOverrideDTO(dt.date(2026, 1, 1), dt.time(9), dt.time(12))])  # window ok
    with pytest.raises(ValidationError):
        validate_date_overrides([DateOverrideDTO(dt.date(2026, 1, 1), dt.time(9), None)])  # mixed
    with pytest.raises(ValidationError):
        validate_date_overrides([DateOverrideDTO(dt.date(2026, 1, 1), dt.time(12), dt.time(9))])  # end<start


def test_booking_limits_validation() -> None:
    validate_booking_limits([BookingLimitDTO("booking_count", "day", 3)])  # ok
    with pytest.raises(ValidationError):
        validate_booking_limits([BookingLimitDTO("booking_count", "day", 0)])  # value>0
    with pytest.raises(ValidationError):
        validate_booking_limits([BookingLimitDTO("nope", "day", 1)])  # bad type
    with pytest.raises(ValidationError):
        validate_booking_limits([BookingLimitDTO("booking_count", "decade", 1)])  # bad period
