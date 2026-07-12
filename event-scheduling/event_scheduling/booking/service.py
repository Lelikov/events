from datetime import UTC, datetime, timedelta
from uuid import UUID

from event_scheduling.booking.assignment import rank_hosts
from event_scheduling.booking.dto import BookingChangeEntryDTO, BookingDTO, CreateBookingDTO
from event_scheduling.booking.interfaces import IBookingReadAdapter, IBookingWriteAdapter
from event_scheduling.booking.limits import limit_exceeded, period_bounds_utc
from event_scheduling.dto.schedule import ActorDTO
from event_scheduling.errors import ConflictError, NotFoundError, ValidationError
from event_scheduling.interfaces.busy_times import BusyTimesSource, TimeWindow
from event_scheduling.slots.domain import host_availability_intervals, subtract_intervals, to_epoch_min
from event_scheduling.slots.dto import HostSchedule, Interval
from event_scheduling.slots.interfaces import Clock, ISlotsReadAdapter
from event_scheduling.validation import validate_time_zone


class BookingService:
    def __init__(
        self,
        slots_read: ISlotsReadAdapter,
        read: IBookingReadAdapter,
        write: IBookingWriteAdapter,
        busy: BusyTimesSource,
        clock: Clock,
    ) -> None:
        self._slots = slots_read
        self._read = read
        self._write = write
        self._busy = busy
        self._clock = clock

    async def _free_host(
        self,
        host: HostSchedule,
        start: datetime,
        end: datetime,
        notice_min: int,
        now: datetime,
        exclude_booking_id: UUID | None,
    ) -> bool:
        if start < now + timedelta(minutes=notice_min):
            return False
        window = TimeWindow(start, end)
        avail = host_availability_intervals(host, start, end)
        busy = await self._busy.get_busy([host.user_id], window, exclude_booking_id=exclude_booking_id)
        busy_iv = [Interval(to_epoch_min(b.start), to_epoch_min(b.end)) for b in busy]
        free = subtract_intervals(avail, busy_iv)
        need = Interval(to_epoch_min(start), to_epoch_min(end))
        return any(iv.start <= need.start and need.end <= iv.end for iv in free)

    async def create(self, dto: CreateBookingDTO, actor: ActorDTO) -> BookingDTO:
        validate_time_zone(dto.attendee_time_zone)
        now = self._clock.now()
        start = dto.start_time.astimezone(UTC)
        if start < now:
            raise ValidationError("start_time is in the past")
        bundle = await self._slots.load(dto.event_type_id)
        if bundle is None:
            raise NotFoundError(f"event_type {dto.event_type_id} not found")
        cfg = bundle.event_type
        end = start + timedelta(minutes=cfg.duration_minutes)

        free_hosts = [
            host
            for host in bundle.hosts
            if await self._free_host(host, start, end, cfg.min_booking_notice_minutes, now, None)
        ]
        if not free_hosts:
            raise ConflictError("no host available for the requested slot")

        stats = await self._read.host_stats([h.user_id for h in free_hosts], now)
        ranked = rank_hosts(stats)

        await self._enforce_limits(dto.event_type_id, ranked[0], start, cfg.duration_minutes, bundle.hosts)

        for host_id in ranked:
            try:
                booking = await self._write.insert(
                    dto.event_type_id, host_id, dto.client_user_id, start, end, dto.attendee_time_zone
                )
            except ConflictError:
                continue
            await self._write.append_log(booking.id, "created", None, None, start, end, actor)
            return booking
        raise ConflictError("slot was taken concurrently")

    async def get(self, booking_id: UUID) -> BookingDTO:
        booking = await self._read.get(booking_id)
        if booking is None:
            raise NotFoundError(f"booking {booking_id} not found")
        return booking

    async def cancel(self, booking_id: UUID, actor: ActorDTO) -> BookingDTO:
        booking = await self.get(booking_id)
        if booking.status == "cancelled":
            return booking  # idempotent, no second log row
        cancelled = await self._write.set_cancelled(booking_id)
        await self._write.append_log(booking_id, "cancelled", booking.start_time, booking.end_time, None, None, actor)
        return cancelled

    async def reschedule(self, booking_id: UUID, new_start: datetime, actor: ActorDTO) -> BookingDTO:
        booking = await self.get(booking_id)
        if booking.status == "cancelled":
            raise ConflictError("cannot reschedule a cancelled booking")
        now = self._clock.now()
        start = new_start.astimezone(UTC)
        if start < now:
            raise ValidationError("start_time is in the past")
        bundle = await self._slots.load(booking.event_type_id)
        if bundle is None:
            raise NotFoundError(f"event_type {booking.event_type_id} not found")
        cfg = bundle.event_type
        end = start + timedelta(minutes=cfg.duration_minutes)
        host = next((h for h in bundle.hosts if h.user_id == booking.host_user_id), None)
        if host is None:
            raise ConflictError("assigned host is no longer on this event type")
        if not await self._free_host(host, start, end, cfg.min_booking_notice_minutes, now, booking_id):
            raise ConflictError("host is not available at the new time")
        updated = await self._write.update_times(booking_id, start, end)
        await self._write.append_log(booking_id, "rescheduled", booking.start_time, booking.end_time, start, end, actor)
        return updated

    async def list_by(
        self,
        host_user_id: UUID | None,
        client_user_id: UUID | None,
        from_utc: datetime | None,
        to_utc: datetime | None,
    ) -> list[BookingDTO]:
        return await self._read.list_by(host_user_id, client_user_id, from_utc, to_utc)

    async def history(self, booking_id: UUID) -> list[BookingChangeEntryDTO]:
        return await self._read.history(booking_id)

    async def _enforce_limits(
        self, event_type_id: UUID, host_id: UUID, start: datetime, duration_min: int, hosts: list[HostSchedule]
    ) -> None:
        limits = await self._read.limits(event_type_id)
        if not limits:
            return
        host_tz = next(h.time_zone for h in hosts if h.user_id == host_id)
        for lim in limits:
            lo, hi = period_bounds_utc(start, lim.period, host_tz)
            count, minutes = await self._read.period_counts(event_type_id, lo, hi)
            if limit_exceeded(lim.limit_type, lim.value, count, minutes, duration_min):
                raise ConflictError(f"booking_limit exceeded: {lim.limit_type}/{lim.period}")
