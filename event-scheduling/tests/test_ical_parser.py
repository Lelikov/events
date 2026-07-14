import datetime as dt

from event_scheduling.calendar.ical_parser import ICalParser
from event_scheduling.interfaces.busy_times import TimeWindow


WIN = TimeWindow(dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 31, tzinfo=dt.UTC))


def _ics(body: str) -> bytes:
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n{body}\r\nEND:VCALENDAR\r\n".encode()


def _event(uid: str, extra: str) -> str:
    return f"BEGIN:VEVENT\r\nUID:{uid}\r\n{extra}\r\nEND:VEVENT"


def test_single_timed_event() -> None:
    ics = _ics(_event("e1", "DTSTART:20261005T090000Z\r\nDTEND:20261005T100000Z"))
    out = ICalParser().expand(ics, WIN)
    assert len(out) == 1
    assert out[0].start == dt.datetime(2026, 10, 5, 9, tzinfo=dt.UTC)
    assert out[0].end == dt.datetime(2026, 10, 5, 10, tzinfo=dt.UTC)


def test_weekly_recurrence_expanded_in_window() -> None:
    ics = _ics(_event("e2", "DTSTART:20261001T090000Z\r\nDTEND:20261001T093000Z\r\nRRULE:FREQ=WEEKLY;COUNT=3"))
    out = ICalParser().expand(ics, WIN)
    starts = sorted(b.start for b in out)
    assert starts == [
        dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC),
        dt.datetime(2026, 10, 8, 9, tzinfo=dt.UTC),
        dt.datetime(2026, 10, 15, 9, tzinfo=dt.UTC),
    ]


def test_all_day_event_is_full_utc_day() -> None:
    ics = _ics(_event("e3", "DTSTART;VALUE=DATE:20261010\r\nDTEND;VALUE=DATE:20261011"))
    out = ICalParser().expand(ics, WIN)
    assert out[0].start == dt.datetime(2026, 10, 10, tzinfo=dt.UTC)
    assert out[0].end == dt.datetime(2026, 10, 11, tzinfo=dt.UTC)


def test_transparent_and_cancelled_are_skipped() -> None:
    ics = _ics(
        _event("e4", "DTSTART:20261005T090000Z\r\nDTEND:20261005T100000Z\r\nTRANSP:TRANSPARENT")
        + "\r\n"
        + _event("e5", "DTSTART:20261006T090000Z\r\nDTEND:20261006T100000Z\r\nSTATUS:CANCELLED")
    )
    assert ICalParser().expand(ics, WIN) == []


def test_out_of_window_event_excluded() -> None:
    ics = _ics(_event("e6", "DTSTART:20261115T090000Z\r\nDTEND:20261115T100000Z"))
    assert ICalParser().expand(ics, WIN) == []
