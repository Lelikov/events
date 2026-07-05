from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from event_scheduling.dto.schedule import TravelDTO


def effective_time_zone(day: date, base_tz: str, travels: Sequence[TravelDTO]) -> str:
    for t in travels:
        if t.start_date <= day and (t.end_date is None or day <= t.end_date):
            return t.time_zone
    return base_tz


def local_interval_to_utc(day: date, start: time, end: time, tz: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(tz)
    start_utc = datetime.combine(day, start, tzinfo=zone).astimezone(UTC)
    end_utc = datetime.combine(day, end, tzinfo=zone).astimezone(UTC)
    return start_utc, end_utc


def group_slots_by_local_date(slots_utc: Sequence[datetime], tz: str) -> dict[str, list[str]]:
    zone = ZoneInfo(tz)
    grouped: dict[str, list[str]] = {}
    for slot in sorted(slots_utc):
        local_date = slot.astimezone(zone).date().isoformat()
        iso_z = slot.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        grouped.setdefault(local_date, []).append(iso_z)
    return grouped
