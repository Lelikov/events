from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class BusyInterval:
    start: datetime
    end: datetime


class BusyTimesSource(Protocol):
    async def get_busy(self, user_ids: Sequence[UUID], window: TimeWindow) -> list[BusyInterval]: ...


class StubBusyTimesSource:
    """Slice-1 placeholder — no busy times until slice 3 backs this with the `booking` table."""

    async def get_busy(self, _user_ids: Sequence[UUID], _window: TimeWindow) -> list[BusyInterval]:
        return []
