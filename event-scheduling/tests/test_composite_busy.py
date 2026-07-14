import datetime as dt
from uuid import uuid4

import pytest

from event_scheduling.calendar.composite_busy import CompositeBusyTimesSource
from event_scheduling.interfaces.busy_times import BusyInterval, TimeWindow


WIN = TimeWindow(dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC))
A = BusyInterval(dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC), dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC))
B = BusyInterval(dt.datetime(2026, 10, 1, 11, tzinfo=dt.UTC), dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC))


class _Booking:
    def __init__(self) -> None:
        self.exclude_seen = "unset"

    async def get_busy(self, user_ids, window, exclude_booking_id=None):
        self.exclude_seen = exclude_booking_id
        return [A]


class _External:
    async def get_busy(self, user_ids, window):
        return [B]


@pytest.mark.asyncio
async def test_unions_both_sources_and_forwards_exclude() -> None:
    booking = _Booking()
    comp = CompositeBusyTimesSource(booking, _External())
    excl = uuid4()
    out = await comp.get_busy([uuid4()], WIN, excl)
    assert out == [A, B]
    assert booking.exclude_seen == excl


@pytest.mark.asyncio
async def test_default_exclude_is_none() -> None:
    booking = _Booking()
    await CompositeBusyTimesSource(booking, _External()).get_busy([uuid4()], WIN)
    assert booking.exclude_seen is None
