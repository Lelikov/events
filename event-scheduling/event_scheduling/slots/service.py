from datetime import UTC, datetime
from uuid import UUID

from event_scheduling.errors import NotFoundError
from event_scheduling.interfaces.busy_times import BusyTimesSource, TimeWindow
from event_scheduling.slots.domain import (
    from_epoch_min,
    host_availability_intervals,
    merge_intervals,
    slice_into_slots,
    subtract_intervals,
    to_epoch_min,
)
from event_scheduling.slots.dto import Interval
from event_scheduling.slots.interfaces import Clock, ISlotsReadAdapter
from event_scheduling.slots.timezones import group_slots_by_local_date


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SlotService:
    def __init__(self, read_adapter: ISlotsReadAdapter, busy_source: BusyTimesSource, clock: Clock) -> None:
        self._read = read_adapter
        self._busy = busy_source
        self._clock = clock

    async def available_slots(
        self, event_type_id: UUID, window_start: datetime, window_end: datetime, time_zone: str
    ) -> dict[str, list[str]]:
        bundle = await self._read.load(event_type_id)
        if bundle is None:
            raise NotFoundError(f"event_type {event_type_id} not found")

        window = TimeWindow(window_start, window_end)
        free: list[Interval] = []
        for host in bundle.hosts:
            intervals = host_availability_intervals(host, window_start, window_end)
            busy = await self._busy.get_busy([host.user_id], window)
            busy_iv = [Interval(to_epoch_min(b.start), to_epoch_min(b.end)) for b in busy]
            free.extend(subtract_intervals(intervals, busy_iv))

        union = merge_intervals(free)
        cfg = bundle.event_type
        step = cfg.slot_interval_minutes or cfg.duration_minutes
        not_before = to_epoch_min(self._clock.now()) + cfg.min_booking_notice_minutes
        slot_mins = slice_into_slots(union, cfg.duration_minutes, step, not_before)
        return group_slots_by_local_date([from_epoch_min(m) for m in slot_mins], time_zone)
