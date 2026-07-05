import datetime as dt
from uuid import UUID, uuid4

import pytest

from event_scheduling.dto.schedule import WeeklyHourDTO
from event_scheduling.errors import NotFoundError
from event_scheduling.interfaces.busy_times import StubBusyTimesSource
from event_scheduling.slots.dto import EventTypeConfig, HostSchedule, SlotBundle
from event_scheduling.slots.service import SlotService


class _FakeAdapter:
    def __init__(self, bundle: SlotBundle | None) -> None:
        self._bundle = bundle

    async def load(self, event_type_id: UUID) -> SlotBundle | None:
        return self._bundle


class _FixedClock:
    def __init__(self, now: dt.datetime) -> None:
        self._now = now

    def now(self) -> dt.datetime:
        return self._now


def _bundle(hosts: list[HostSchedule], *, step: int | None = 30, notice: int = 0) -> SlotBundle:
    return SlotBundle(
        event_type=EventTypeConfig(60, step, notice, 0, 0),
        hosts=hosts,
    )


def _host(tz: str = "Europe/Berlin") -> HostSchedule:
    return HostSchedule(uuid4(), tz, [WeeklyHourDTO(4, dt.time(9), dt.time(17))], [], [])


@pytest.mark.asyncio
async def test_two_hosts_union_produces_slots() -> None:
    # Two hosts, identical Thu 09:00-17:00 Berlin → union is the same window; 60-min/30-step.
    svc = SlotService(
        _FakeAdapter(_bundle([_host(), _host()])),
        StubBusyTimesSource(),
        _FixedClock(dt.datetime(2026, 9, 1, tzinfo=dt.UTC)),
    )
    grouped = await svc.available_slots(
        uuid4(), dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC), "Europe/Berlin"
    )
    # 09:00 CEST → 07:00Z; last fitting slot 16:00 CEST → 14:00Z (14:00+60 = 15:00 = 17:00 CEST end)
    assert grouped["2026-10-01"][0] == "2026-10-01T07:00:00Z"
    assert grouped["2026-10-01"][-1] == "2026-10-01T14:00:00Z"


@pytest.mark.asyncio
async def test_min_notice_drops_early_slots() -> None:
    # now = 2026-10-01 10:00Z, notice 120 min → earliest slot >= 12:00Z
    svc = SlotService(
        _FakeAdapter(_bundle([_host()], notice=120)),
        StubBusyTimesSource(),
        _FixedClock(dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC)),
    )
    grouped = await svc.available_slots(
        uuid4(), dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC), "Europe/Berlin"
    )
    assert grouped["2026-10-01"][0] == "2026-10-01T12:00:00Z"


@pytest.mark.asyncio
async def test_missing_event_type_raises_not_found() -> None:
    svc = SlotService(_FakeAdapter(None), StubBusyTimesSource(), _FixedClock(dt.datetime(2026, 1, 1, tzinfo=dt.UTC)))
    with pytest.raises(NotFoundError):
        await svc.available_slots(
            uuid4(), dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC), "Europe/Berlin"
        )
