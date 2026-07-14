from typing import Protocol

from event_scheduling.interfaces.busy_times import BusyInterval, TimeWindow


class IICalClient(Protocol):
    async def fetch(self, url: str) -> bytes: ...


class IICalParser(Protocol):
    def expand(self, ics_bytes: bytes, window: TimeWindow) -> list[BusyInterval]: ...
