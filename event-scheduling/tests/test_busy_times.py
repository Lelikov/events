import datetime as dt
from uuid import uuid4

import pytest

from event_scheduling.interfaces.busy_times import StubBusyTimesSource, TimeWindow


@pytest.mark.asyncio
async def test_stub_returns_empty() -> None:
    src = StubBusyTimesSource()
    window = TimeWindow(dt.datetime(2026, 1, 1), dt.datetime(2026, 1, 2))
    assert await src.get_busy([uuid4()], window) == []
