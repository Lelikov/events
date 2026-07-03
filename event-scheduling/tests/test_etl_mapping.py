import datetime as dt

from event_scheduling.dto.event_type import BookingLimitDTO
from scripts.etl_mapping import expand_booking_limits, expand_weekly, remap_day_of_week, resolve_time_zone


def test_remap_day_sunday_zero_to_seven() -> None:
    assert remap_day_of_week(0) == 7
    assert remap_day_of_week(1) == 1
    assert remap_day_of_week(6) == 6


def test_resolve_time_zone_prefers_schedule_then_user() -> None:
    assert resolve_time_zone("Europe/Berlin", "Europe/Moscow") == "Europe/Berlin"
    assert resolve_time_zone(None, "Europe/Moscow") == "Europe/Moscow"


def test_expand_weekly_one_row_per_day() -> None:
    rows = expand_weekly([0, 1, 3], dt.time(9), dt.time(17))
    assert sorted(r.day_of_week for r in rows) == [1, 3, 7]  # 0→7 remap
    assert all(r.start_time == dt.time(9) and r.end_time == dt.time(17) for r in rows)


def test_expand_booking_limits_json_to_rows() -> None:
    rows = expand_booking_limits({"PER_DAY": 3, "PER_WEEK": 10}, "booking_count")
    assert BookingLimitDTO("booking_count", "day", 3) in rows
    assert BookingLimitDTO("booking_count", "week", 10) in rows
