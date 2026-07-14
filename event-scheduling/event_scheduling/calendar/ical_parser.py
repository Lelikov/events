from datetime import UTC, date, datetime, time, timedelta

import icalendar
import recurring_ical_events

from event_scheduling.interfaces.busy_times import BusyInterval, TimeWindow


def _to_utc(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return datetime.combine(value, time.min, tzinfo=UTC)


class ICalParser:
    def expand(self, ics_bytes: bytes, window: TimeWindow) -> list[BusyInterval]:
        calendar = icalendar.Calendar.from_ical(ics_bytes)
        events = recurring_ical_events.of(calendar).between(window.start, window.end)
        win_lo = _to_utc(window.start)
        win_hi = _to_utc(window.end)
        out: list[BusyInterval] = []
        for event in events:
            interval = self._to_interval(event, win_lo, win_hi)
            if interval is not None:
                out.append(interval)
        return out

    @staticmethod
    def _to_interval(event: object, win_lo: datetime, win_hi: datetime) -> BusyInterval | None:
        if str(event.get("TRANSP", "")).upper() == "TRANSPARENT":
            return None
        if str(event.get("STATUS", "")).upper() == "CANCELLED":
            return None
        dtstart = event.get("DTSTART")
        if dtstart is None:
            return None
        start = _to_utc(dtstart.dt)
        end = ICalParser._end_of(event, dtstart)
        if end is None or end <= start:
            return None
        clipped_start = max(start, win_lo)
        clipped_end = min(end, win_hi)
        if clipped_end <= clipped_start:
            return None
        return BusyInterval(clipped_start, clipped_end)

    @staticmethod
    def _end_of(event: object, dtstart: object) -> datetime | None:
        dtend = event.get("DTEND")
        if dtend is not None:
            return _to_utc(dtend.dt)
        if isinstance(dtstart.dt, datetime):
            return None  # timed event without DTEND → zero-length, skip
        return _to_utc(dtstart.dt) + timedelta(days=1)  # all-day single date → one UTC day
