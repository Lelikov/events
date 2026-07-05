from datetime import UTC, date, datetime, time, timedelta

from event_scheduling.slots.dto import HostSchedule, Interval
from event_scheduling.slots.timezones import effective_time_zone, local_interval_to_utc


def to_epoch_min(d: datetime) -> int:
    return int(d.timestamp()) // 60


def from_epoch_min(m: int) -> datetime:
    return datetime.fromtimestamp(m * 60, tz=UTC)


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda i: i.start)
    merged = [ordered[0]]
    for cur in ordered[1:]:
        last = merged[-1]
        if cur.start <= last.end:
            merged[-1] = Interval(last.start, max(last.end, cur.end))
            continue
        merged.append(cur)
    return merged


def subtract_intervals(base: list[Interval], busy: list[Interval]) -> list[Interval]:
    if not busy:
        return list(base)
    busy_sorted = merge_intervals(busy)
    out: list[Interval] = []
    for b in base:
        cursor = b.start
        for x in busy_sorted:
            if x.end <= cursor or x.start >= b.end:
                continue
            if x.start > cursor:
                out.append(Interval(cursor, x.start))
            cursor = max(cursor, x.end)
        if cursor < b.end:
            out.append(Interval(cursor, b.end))
    return out


def slice_into_slots(avail: list[Interval], duration_min: int, step_min: int, not_before_min: int) -> list[int]:
    out: list[int] = []
    for iv in avail:
        t = iv.start
        while t + duration_min <= iv.end:
            if t >= not_before_min:
                out.append(t)
            t += step_min
    return out


def _clip(start_utc: datetime, end_utc: datetime, window_start: datetime, window_end: datetime) -> Interval | None:
    s = max(start_utc, window_start)
    e = min(end_utc, window_end)
    if s >= e:
        return None
    return Interval(to_epoch_min(s), to_epoch_min(e))


def _day_local_intervals(host: HostSchedule, day: date) -> list[tuple[time, time]]:
    overrides = [o for o in host.date_overrides if o.date == day]
    if overrides:
        return [(o.start_time, o.end_time) for o in overrides if o.start_time is not None and o.end_time is not None]
    return [(w.start_time, w.end_time) for w in host.weekly_hours if w.day_of_week == day.isoweekday()]


def host_availability_intervals(host: HostSchedule, window_start: datetime, window_end: datetime) -> list[Interval]:
    out: list[Interval] = []
    day = window_start.date() - timedelta(days=1)
    last = window_end.date() + timedelta(days=1)
    while day <= last:
        tz = effective_time_zone(day, host.time_zone, host.travels)
        for start, end in _day_local_intervals(host, day):
            start_utc, end_utc = local_interval_to_utc(day, start, end, tz)
            clipped = _clip(start_utc, end_utc, window_start, window_end)
            if clipped is not None:
                out.append(clipped)
        day += timedelta(days=1)
    return sorted(out, key=lambda i: i.start)
