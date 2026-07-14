from collections.abc import Sequence
from uuid import UUID

from event_scheduling.interfaces.busy_times import BusyInterval, BusyTimesSource, TimeWindow


class CompositeBusyTimesSource:
    """Unions the booking-based busy source with the external-calendar one.

    Satisfies BusyTimesSource. The optional exclude_booking_id is forwarded ONLY to the
    booking source (booking-create passes it; the slot engine does not).
    """

    def __init__(self, booking: BusyTimesSource, external: BusyTimesSource) -> None:
        self._booking = booking
        self._external = external

    async def get_busy(
        self, user_ids: Sequence[UUID], window: TimeWindow, exclude_booking_id: UUID | None = None
    ) -> list[BusyInterval]:
        booking_busy = await self._booking.get_busy(user_ids, window, exclude_booking_id)
        external_busy = await self._external.get_busy(user_ids, window)
        return [*booking_busy, *external_busy]
