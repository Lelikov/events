"""Service-level tests for BookingService.create (slice 3, Task 5).

These drive BookingService directly with real adapters over a real Postgres
session (no router/DI — that's Task 7). Task 7 will extend this file with the
HTTP-level `client`-driven tests.
"""

import datetime as dt
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.booking.busy_source import BookingBusyTimesSource
from event_scheduling.booking.dto import CreateBookingDTO
from event_scheduling.booking.read_adapter import BookingReadAdapter
from event_scheduling.booking.service import BookingService
from event_scheduling.booking.write_adapter import BookingWriteAdapter
from event_scheduling.dto.schedule import ActorDTO
from event_scheduling.errors import ConflictError, NotFoundError, ValidationError
from event_scheduling.slots.read_adapter import SlotsReadAdapter


ACTOR = ActorDTO(source="api", user_id=None)
NOW = dt.datetime(2026, 9, 1, tzinfo=dt.UTC)
START = dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC)  # Thursday, within 09:00-17:00 Europe/Berlin (CEST)


class _FixedClock:
    def __init__(self, now: dt.datetime) -> None:
        self._now = now

    def now(self) -> dt.datetime:
        return self._now


def _build_service(sql: SqlExecutor, now: dt.datetime) -> BookingService:
    return BookingService(
        SlotsReadAdapter(sql),
        BookingReadAdapter(sql),
        BookingWriteAdapter(sql),
        BookingBusyTimesSource(sql),
        _FixedClock(now),
    )


async def _seed_single_host_et(session, *, notice: int = 0) -> tuple[UUID, UUID]:
    owner = uuid4()
    sid = uuid4()
    await session.execute(
        text("INSERT INTO schedule (id, owner_user_id, name, time_zone) VALUES (:id, :o, 's', 'Europe/Berlin')"),
        {"id": sid, "o": owner},
    )
    await session.execute(
        text(
            "INSERT INTO weekly_hours (schedule_id, day_of_week, start_time, end_time) "
            "VALUES (:sid, 4, '09:00', '17:00')"  # Thursday
        ),
        {"sid": sid},
    )
    et_id = uuid4()
    await session.execute(
        text(
            "INSERT INTO event_type (id, slug, title, duration_minutes, slot_interval_minutes, "
            "min_booking_notice_minutes, buffer_before_minutes, buffer_after_minutes) "
            "VALUES (:id, :slug, 'Intro', 60, 30, :notice, 0, 0)"
        ),
        {"id": et_id, "slug": f"et-{et_id}", "notice": notice},
    )
    await session.execute(
        text("INSERT INTO host (event_type_id, user_id, schedule_id) VALUES (:et, :u, :sid)"),
        {"et": et_id, "u": owner, "sid": sid},
    )
    await session.commit()
    return et_id, owner


@pytest.mark.asyncio
async def test_create_booking_assigns_host(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et, owner = await _seed_single_host_et(s)

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        dto = CreateBookingDTO(
            event_type_id=et, client_user_id=uuid4(), start_time=START, attendee_time_zone="Europe/Berlin"
        )
        booking = await service.create(dto, ACTOR)
        await s.commit()

    assert booking.host_user_id == owner
    assert booking.status == "confirmed"
    assert booking.start_time == START


@pytest.mark.asyncio
async def test_double_book_same_slot_conflicts(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et, _owner = await _seed_single_host_et(s)

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        dto = CreateBookingDTO(
            event_type_id=et, client_user_id=uuid4(), start_time=START, attendee_time_zone="Europe/Berlin"
        )
        await service.create(dto, ACTOR)
        await s.commit()

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        dto2 = CreateBookingDTO(
            event_type_id=et, client_user_id=uuid4(), start_time=START, attendee_time_zone="Europe/Berlin"
        )
        with pytest.raises(ConflictError):
            await service.create(dto2, ACTOR)


@pytest.mark.asyncio
async def test_create_unknown_event_type_404(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        dto = CreateBookingDTO(
            event_type_id=uuid4(), client_user_id=uuid4(), start_time=START, attendee_time_zone="Europe/Berlin"
        )
        with pytest.raises(NotFoundError):
            await service.create(dto, ACTOR)


@pytest.mark.asyncio
async def test_create_past_time_422(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et, _owner = await _seed_single_host_et(s)

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        dto = CreateBookingDTO(
            event_type_id=et,
            client_user_id=uuid4(),
            start_time=dt.datetime(2020, 1, 1, 9, tzinfo=dt.UTC),
            attendee_time_zone="Europe/Berlin",
        )
        with pytest.raises(ValidationError):
            await service.create(dto, ACTOR)
