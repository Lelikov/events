from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def _local_midnight(d: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(d, time(0), tzinfo=zone)


def _day_range(local: datetime) -> tuple[date, date]:
    d = local.date()
    return d, d + timedelta(days=1)


def _week_range(local: datetime) -> tuple[date, date]:
    monday = local.date() - timedelta(days=local.date().weekday())  # ISO Monday
    return monday, monday + timedelta(days=7)


def _month_range(local: datetime) -> tuple[date, date]:
    first = local.date().replace(day=1)
    next_first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, next_first


def _year_range(local: datetime) -> tuple[date, date]:
    return date(local.year, 1, 1), date(local.year + 1, 1, 1)


_PERIODS = {"day": _day_range, "week": _week_range, "month": _month_range, "year": _year_range}


def period_bounds_utc(start: datetime, period: str, tz: str) -> tuple[datetime, datetime]:
    ranger = _PERIODS.get(period)
    if ranger is None:
        msg = f"unknown period: {period!r}"
        raise ValueError(msg)
    zone = ZoneInfo(tz)
    local = start.astimezone(zone)
    lo_date, hi_date = ranger(local)
    return _local_midnight(lo_date, zone).astimezone(UTC), _local_midnight(hi_date, zone).astimezone(UTC)


def limit_exceeded(
    limit_type: str, value: int, current_count: int, current_duration_min: int, new_duration_min: int
) -> bool:
    if limit_type == "booking_count":
        return current_count >= value
    if limit_type == "booking_duration":
        return current_duration_min + new_duration_min > value
    return False
