from datetime import UTC, datetime

from event_scheduling.slots.dto import Interval


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
