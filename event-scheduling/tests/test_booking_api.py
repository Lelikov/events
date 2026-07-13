"""Service-level tests for BookingService (slice 3, Tasks 5-6) plus HTTP-level integration tests.

Covers the /api/v1/bookings endpoints (Task 7). The service-level tests drive
BookingService directly with real adapters over a real Postgres session (no
router/DI). The HTTP-level tests below drive the same flows through the
FastAPI app + Dishka DI container via the `client` fixture, proving the full
wiring (including the BookingBusyTimesSource swap).
"""

import asyncio
import datetime as dt
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.booking.busy_source import BookingBusyTimesSource
from event_scheduling.booking.dto import BookingDTO, CreateBookingDTO
from event_scheduling.booking.read_adapter import BookingReadAdapter
from event_scheduling.booking.service import BookingService
from event_scheduling.booking.write_adapter import BookingWriteAdapter
from event_scheduling.dto.schedule import ActorDTO
from event_scheduling.errors import ConflictError, NotFoundError, ValidationError
from event_scheduling.publishing.outbox_writer import OutboxWriter
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
        OutboxWriter(sql),
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


@pytest.mark.asyncio
async def test_cancel_frees_slot_and_is_idempotent(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et, _owner = await _seed_single_host_et(s)

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        dto = CreateBookingDTO(
            event_type_id=et, client_user_id=uuid4(), start_time=START, attendee_time_zone="Europe/Berlin"
        )
        booking = await service.create(dto, ACTOR)
        await s.commit()

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        cancelled = await service.cancel(booking.id, ACTOR)
        await s.commit()
    assert cancelled.status == "cancelled"

    # idempotent: second cancel returns the booking, no error, no second log row
    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        cancelled_again = await service.cancel(booking.id, ACTOR)
        await s.commit()
    assert cancelled_again.status == "cancelled"

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        entries = await service.history(booking.id)
    assert [e.kind for e in entries] == ["created", "cancelled"]

    # slot is free again → re-book succeeds
    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        dto2 = CreateBookingDTO(
            event_type_id=et, client_user_id=uuid4(), start_time=START, attendee_time_zone="Europe/Berlin"
        )
        rebooked = await service.create(dto2, ACTOR)
        await s.commit()
    assert rebooked.status == "confirmed"


@pytest.mark.asyncio
async def test_cancel_unknown_booking_404(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        with pytest.raises(NotFoundError):
            await service.cancel(uuid4(), ACTOR)


@pytest.mark.asyncio
async def test_reschedule_same_host_to_free_slot(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et, owner = await _seed_single_host_et(s)

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        dto = CreateBookingDTO(
            event_type_id=et, client_user_id=uuid4(), start_time=START, attendee_time_zone="Europe/Berlin"
        )
        booking = await service.create(dto, ACTOR)
        await s.commit()

    new_start = START + dt.timedelta(hours=2)
    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        rescheduled = await service.reschedule(booking.id, new_start, ACTOR)
        await s.commit()

    assert rescheduled.start_time == new_start
    assert rescheduled.end_time == new_start + dt.timedelta(minutes=60)
    assert rescheduled.host_user_id == owner


@pytest.mark.asyncio
async def test_reschedule_cancelled_booking_conflicts(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et, _owner = await _seed_single_host_et(s)

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        dto = CreateBookingDTO(
            event_type_id=et, client_user_id=uuid4(), start_time=START, attendee_time_zone="Europe/Berlin"
        )
        booking = await service.create(dto, ACTOR)
        await s.commit()

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        await service.cancel(booking.id, ACTOR)
        await s.commit()

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        with pytest.raises(ConflictError):
            await service.reschedule(booking.id, START + dt.timedelta(hours=2), ACTOR)


@pytest.mark.asyncio
async def test_write_adapter_update_times_maps_integrity_error_to_conflict(sessionmaker_fixture) -> None:
    """BookingWriteAdapter.update_times must map the exclusion IntegrityError to ConflictError, not raise it raw.

    Drives the adapter directly (bypassing BookingService's own pre-check) to
    deterministically force the DB exclusion constraint to fire on the UPDATE
    itself — the scenario a concurrent same-host booking creates between
    BookingService's availability re-check and its UPDATE (final-review FIX-2:
    without the SAVEPOINT + IntegrityError->ConflictError mapping, this
    surfaces as an uncaught IntegrityError / 500 instead of a 409).
    """
    async with sessionmaker_fixture() as s:
        et, owner = await _seed_single_host_et(s)

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        booking_a = await service.create(
            CreateBookingDTO(
                event_type_id=et, client_user_id=uuid4(), start_time=START, attendee_time_zone="Europe/Berlin"
            ),
            ACTOR,
        )
        booking_b = await service.create(
            CreateBookingDTO(
                event_type_id=et,
                client_user_id=uuid4(),
                start_time=START + dt.timedelta(hours=4),
                attendee_time_zone="Europe/Berlin",
            ),
            ACTOR,
        )
        await s.commit()

    # Directly exercise the adapter's UPDATE with a range that overlaps booking_a's
    # still-confirmed slot — the outer transaction must survive (SAVEPOINT rollback)
    # and the error must come back as ConflictError, not a raw IntegrityError.
    async with sessionmaker_fixture() as s:
        write = BookingWriteAdapter(SqlExecutor(s))
        with pytest.raises(ConflictError):
            await write.update_times(booking_b.id, START, START + dt.timedelta(hours=1))
        # outer transaction/session still usable after the SAVEPOINT rollback
        still_there = await BookingReadAdapter(SqlExecutor(s)).get(booking_a.id)
        assert still_there is not None
        assert still_there.host_user_id == owner


@pytest.mark.asyncio
async def test_history_chain(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et, _owner = await _seed_single_host_et(s)

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        dto = CreateBookingDTO(
            event_type_id=et, client_user_id=uuid4(), start_time=START, attendee_time_zone="Europe/Berlin"
        )
        booking = await service.create(dto, ACTOR)
        await s.commit()

    new_start = START + dt.timedelta(hours=2)
    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        await service.reschedule(booking.id, new_start, ACTOR)
        await s.commit()

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        await service.cancel(booking.id, ACTOR)
        await s.commit()

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        entries = await service.history(booking.id)

    assert [e.kind for e in entries] == ["created", "rescheduled", "cancelled"]
    assert entries[1].from_start == START
    assert entries[1].to_start == new_start


@pytest.mark.asyncio
async def test_get_and_list_by(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et, owner = await _seed_single_host_et(s)

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        dto = CreateBookingDTO(
            event_type_id=et, client_user_id=uuid4(), start_time=START, attendee_time_zone="Europe/Berlin"
        )
        booking = await service.create(dto, ACTOR)
        await s.commit()

    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        fetched = await service.get(booking.id)
        listed = await service.list_by(owner, None, None, None)

    assert fetched.id == booking.id
    assert [b.id for b in listed] == [booking.id]


@pytest.mark.asyncio
async def test_get_unknown_booking_404(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        service = _build_service(SqlExecutor(s), NOW)
        with pytest.raises(NotFoundError):
            await service.get(uuid4())


async def _create_and_settle(service, session, dto: CreateBookingDTO) -> BookingDTO | ConflictError:
    """Run create() and commit/rollback its OWN session immediately (not after gather).

    Postgres's exclusion-constraint check waits for the CONFLICTING transaction
    to end (commit/rollback) before it can decide winner/loser (see
    check_exclusion_or_unique_constraint -> XactLockTableWait) — not just for
    the statement to finish. If both sides deferred commit until after
    asyncio.gather() returned, neither INSERT could ever resolve: the loser's
    statement blocks on the winner's still-open transaction, and gather()
    can't return until the loser's coroutine returns. Committing/rolling back
    inline, as each coroutine finishes, is what actually lets the two race.
    """
    try:
        booking = await service.create(dto, ACTOR)
    except ConflictError as e:
        await session.rollback()
        return e
    await session.commit()
    return booking


@pytest.mark.asyncio
async def test_concurrent_create_same_slot_one_wins(sessionmaker_fixture) -> None:
    """Two concurrent BookingService.create calls for the same single-host slot.

    Each service runs over its own session/connection (mirrors two concurrent
    HTTP requests). The DB exclusion constraint (ex_booking_no_overlap)
    serializes the two INSERTs: whichever transaction commits first wins; the
    other's INSERT blocks on the in-flight row, then fails with IntegrityError
    once the winner's row is committed and visible, which
    BookingWriteAdapter.insert maps to ConflictError, which BookingService
    re-raises (single-host event type, no other host to fall back to).
    Exactly one of the two gather results must be a BookingDTO and the other
    a ConflictError.
    """
    async with sessionmaker_fixture() as s:
        et, _owner = await _seed_single_host_et(s)

    async with sessionmaker_fixture() as sa, sessionmaker_fixture() as sb:
        svc_a = _build_service(SqlExecutor(sa), NOW)
        svc_b = _build_service(SqlExecutor(sb), NOW)
        dto_a = CreateBookingDTO(
            event_type_id=et, client_user_id=uuid4(), start_time=START, attendee_time_zone="Europe/Berlin"
        )
        dto_b = CreateBookingDTO(
            event_type_id=et, client_user_id=uuid4(), start_time=START, attendee_time_zone="Europe/Berlin"
        )

        results = await asyncio.gather(
            _create_and_settle(svc_a, sa, dto_a),
            _create_and_settle(svc_b, sb, dto_b),
        )

    successes = [r for r in results if isinstance(r, BookingDTO)]
    conflicts = [r for r in results if isinstance(r, ConflictError)]
    assert len(successes) == 1
    assert len(conflicts) == 1


# ---------------------------------------------------------------------------
# HTTP-level tests (Task 7) — drive the same flows through the FastAPI app +
# Dishka DI container via the `client` fixture (real BookingBusyTimesSource).
# ---------------------------------------------------------------------------

HDRS = {"actor-source": "admin"}


async def _seed_single_host_et_http(client, *, notice: int = 0) -> tuple[str, str]:
    """Seed one schedule (Thu 09:00-17:00 Europe/Berlin) + one event type hosting it, via HTTP."""
    owner = str(uuid4())
    client.put(
        f"/api/v1/schedules/{owner}",
        json={
            "name": "s",
            "time_zone": "Europe/Berlin",
            "weekly_hours": [{"day_of_week": 4, "start_time": "09:00", "end_time": "17:00"}],  # Thursday
            "date_overrides": [],
        },
        headers=HDRS,
    )
    sid = client.get(f"/api/v1/schedules/{owner}").json()["schedule"]["id"]
    body = {
        "slug": f"et-{uuid4().hex[:8]}",
        "title": "Intro",
        "duration_minutes": 60,
        "slot_interval_minutes": 30,
        "min_booking_notice_minutes": notice,
        "buffer_before_minutes": 0,
        "buffer_after_minutes": 0,
        "hosts": [{"user_id": owner, "schedule_id": sid}],
        "booking_limits": [],
    }
    et_id = client.post("/api/v1/event-types", json=body).json()["id"]
    return et_id, owner


HTTP_START = "2026-10-01T09:00:00Z"  # Thursday, within 09:00-17:00 Europe/Berlin (CEST)


@pytest.mark.asyncio
async def test_http_create_booking_assigns_host(client) -> None:
    et, owner = await _seed_single_host_et_http(client)
    resp = client.post(
        "/api/v1/bookings",
        headers=HDRS,
        json={
            "event_type_id": et,
            "client_user_id": str(uuid4()),
            "start_time": HTTP_START,
            "attendee_time_zone": "Europe/Berlin",
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["host_user_id"] == owner
    assert body["status"] == "confirmed"
    assert body["start_time"] == "2026-10-01T09:00:00Z"
    assert body["end_time"] == "2026-10-01T10:00:00Z"


@pytest.mark.asyncio
async def test_http_create_booking_writes_outbox_row(client, sessionmaker_fixture) -> None:
    """Proves the ioc booking->outbox wiring end-to-end.

    Creating a booking via HTTP (real DI, including OutboxWriter) leaves a
    pending outbox row for the dispatcher.
    """
    et, _owner = await _seed_single_host_et_http(client)
    resp = client.post(
        "/api/v1/bookings",
        headers=HDRS,
        json={
            "event_type_id": et,
            "client_user_id": str(uuid4()),
            "start_time": HTTP_START,
            "attendee_time_zone": "Europe/Berlin",
        },
    )
    assert resp.status_code == 201
    booking_id = resp.json()["id"]

    async with sessionmaker_fixture() as s:
        row = (
            await s.execute(
                text("SELECT booking_uid, event_type, status FROM outbox WHERE booking_uid = :uid"),
                {"uid": str(booking_id)},
            )
        ).one()
    assert row.booking_uid == booking_id
    assert row.event_type == "booking.created"
    assert row.status == "pending"


@pytest.mark.asyncio
async def test_http_double_book_same_slot_409(client) -> None:
    et, _owner = await _seed_single_host_et_http(client)
    payload = {
        "event_type_id": et,
        "client_user_id": str(uuid4()),
        "start_time": HTTP_START,
        "attendee_time_zone": "Europe/Berlin",
    }
    first = client.post("/api/v1/bookings", headers=HDRS, json=payload)
    assert first.status_code == 201
    second = client.post(
        "/api/v1/bookings",
        headers=HDRS,
        json={**payload, "client_user_id": str(uuid4())},
    )
    assert second.status_code == 409


@pytest.mark.asyncio
async def test_http_get_unknown_booking_404(client) -> None:
    resp = client.get(f"/api/v1/bookings/{uuid4()}")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_http_create_past_time_422(client) -> None:
    et, _owner = await _seed_single_host_et_http(client)
    resp = client.post(
        "/api/v1/bookings",
        headers=HDRS,
        json={
            "event_type_id": et,
            "client_user_id": str(uuid4()),
            "start_time": "2020-01-01T09:00:00Z",
            "attendee_time_zone": "Europe/Berlin",
        },
    )
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_http_cancel_and_reschedule_and_history(client) -> None:
    et, _owner = await _seed_single_host_et_http(client)
    created = client.post(
        "/api/v1/bookings",
        headers=HDRS,
        json={
            "event_type_id": et,
            "client_user_id": str(uuid4()),
            "start_time": HTTP_START,
            "attendee_time_zone": "Europe/Berlin",
        },
    ).json()
    booking_id = created["id"]

    rescheduled = client.post(
        f"/api/v1/bookings/{booking_id}/reschedule",
        headers=HDRS,
        json={"start_time": "2026-10-01T11:00:00Z"},
    )
    assert rescheduled.status_code == 200
    assert rescheduled.json()["start_time"] == "2026-10-01T11:00:00Z"

    cancelled = client.post(f"/api/v1/bookings/{booking_id}/cancel", headers=HDRS)
    assert cancelled.status_code == 200
    assert cancelled.json()["status"] == "cancelled"

    history = client.get(f"/api/v1/bookings/{booking_id}/history")
    assert history.status_code == 200
    assert [e["kind"] for e in history.json()["entries"]] == ["created", "rescheduled", "cancelled"]


@pytest.mark.asyncio
async def test_http_list_bookings_requires_exactly_one_filter(client) -> None:
    et, owner = await _seed_single_host_et_http(client)
    client.post(
        "/api/v1/bookings",
        headers=HDRS,
        json={
            "event_type_id": et,
            "client_user_id": str(uuid4()),
            "start_time": HTTP_START,
            "attendee_time_zone": "Europe/Berlin",
        },
    )

    neither = client.get("/api/v1/bookings")
    assert neither.status_code == 422

    both = client.get("/api/v1/bookings", params={"host_user_id": owner, "client_user_id": str(uuid4())})
    assert both.status_code == 422

    by_host = client.get("/api/v1/bookings", params={"host_user_id": owner})
    assert by_host.status_code == 200
    assert len(by_host.json()["bookings"]) == 1


@pytest.mark.asyncio
async def test_booking_removes_slot_from_slots_endpoint(client) -> None:
    et, _ = await _seed_single_host_et_http(client)
    before = client.get(
        "/api/v1/slots",
        params={
            "event_type_id": et,
            "start": "2026-10-01T00:00:00Z",
            "end": "2026-10-02T00:00:00Z",
            "time_zone": "Europe/Berlin",
        },
    ).json()["slots"]
    assert "2026-10-01T07:00:00Z" in before["2026-10-01"]  # 09:00 CEST

    client.post(
        "/api/v1/bookings",
        headers=HDRS,
        json={
            "event_type_id": et,
            "client_user_id": str(uuid4()),
            "start_time": "2026-10-01T07:00:00Z",
            "attendee_time_zone": "Europe/Berlin",
        },
    )

    after = client.get(
        "/api/v1/slots",
        params={
            "event_type_id": et,
            "start": "2026-10-01T00:00:00Z",
            "end": "2026-10-02T00:00:00Z",
            "time_zone": "Europe/Berlin",
        },
    ).json()["slots"]
    assert "2026-10-01T07:00:00Z" not in after.get("2026-10-01", [])  # now busy → gone


# ---------------------------------------------------------------------------
# Buffer + booking_limit e2e (Task 8) — configurable seed helper distinct from
# _seed_single_host_et (service-level) and _seed_single_host_et_http (Task 7).
# ---------------------------------------------------------------------------


def _seed_et_http_with(
    client, *, buffers: tuple[int, int] = (0, 0), limits: list[dict] | None = None, notice: int = 0
) -> tuple[str, str]:
    """Seed one schedule (Thu 09:00-17:00 Europe/Berlin) + one configurable event type, via HTTP."""
    owner = str(uuid4())
    client.put(
        f"/api/v1/schedules/{owner}",
        json={
            "name": "s",
            "time_zone": "Europe/Berlin",
            "weekly_hours": [{"day_of_week": 4, "start_time": "09:00", "end_time": "17:00"}],  # Thursday
            "date_overrides": [],
        },
        headers=HDRS,
    )
    sid = client.get(f"/api/v1/schedules/{owner}").json()["schedule"]["id"]
    et_id = client.post(
        "/api/v1/event-types",
        json={
            "slug": f"et-{uuid4().hex[:8]}",
            "title": "t",
            "duration_minutes": 60,
            "slot_interval_minutes": 30,
            "min_booking_notice_minutes": notice,
            "buffer_before_minutes": buffers[0],
            "buffer_after_minutes": buffers[1],
            "hosts": [{"user_id": owner, "schedule_id": sid}],
            "booking_limits": limits or [],
        },
    ).json()["id"]
    return et_id, owner


@pytest.mark.asyncio
async def test_buffer_blocks_adjacent_slot(client) -> None:
    et, _ = _seed_et_http_with(client, buffers=(0, 30))  # 30-min after-buffer
    client.post(
        "/api/v1/bookings",
        headers=HDRS,
        json={
            "event_type_id": et,
            "client_user_id": str(uuid4()),
            "start_time": "2026-10-01T07:00:00Z",
            "attendee_time_zone": "Europe/Berlin",
        },
    )  # 09:00-10:00 CEST

    slots = client.get(
        "/api/v1/slots",
        params={
            "event_type_id": et,
            "start": "2026-10-01T00:00:00Z",
            "end": "2026-10-02T00:00:00Z",
            "time_zone": "Europe/Berlin",
        },
    ).json()["slots"]["2026-10-01"]
    # 10:00 CEST (08:00Z) would start within the 30-min after-buffer of the 10:00 end → not offered
    assert "2026-10-01T08:00:00Z" not in slots
    assert "2026-10-01T08:30:00Z" in slots  # 10:30 CEST is clear


@pytest.mark.asyncio
async def test_booking_count_limit_enforced(client) -> None:
    et, _ = _seed_et_http_with(client, limits=[{"limit_type": "booking_count", "period": "day", "value": 1}])
    first = client.post(
        "/api/v1/bookings",
        headers=HDRS,
        json={
            "event_type_id": et,
            "client_user_id": str(uuid4()),
            "start_time": "2026-10-01T07:00:00Z",
            "attendee_time_zone": "Europe/Berlin",
        },
    )
    second = client.post(
        "/api/v1/bookings",
        headers=HDRS,
        json={
            "event_type_id": et,
            "client_user_id": str(uuid4()),
            "start_time": "2026-10-01T08:00:00Z",
            "attendee_time_zone": "Europe/Berlin",
        },
    )
    assert first.status_code == 201
    assert second.status_code == 409  # day limit of 1 reached


# ---------------------------------------------------------------------------
# Create-path buffer enforcement (final-review FIX-1) — the narrow re-check
# window used by BookingService._free_host on the create path must see
# buffer-expanded neighbor bookings, not just their raw ranges, otherwise it
# diverges from /slots (wide window) and lets an in-buffer booking through.
# ---------------------------------------------------------------------------


def _seed_et_http_buf(client) -> tuple[str, str]:
    """Seed one schedule (Thu 09:00-17:00 Europe/Berlin) + one single-host event type with a 30-min after-buffer."""
    owner = str(uuid4())
    client.put(
        f"/api/v1/schedules/{owner}",
        json={
            "name": "s",
            "time_zone": "Europe/Berlin",
            "weekly_hours": [{"day_of_week": 4, "start_time": "09:00", "end_time": "17:00"}],  # Thursday
            "date_overrides": [],
        },
        headers=HDRS,
    )
    sid = client.get(f"/api/v1/schedules/{owner}").json()["schedule"]["id"]
    et_id = client.post(
        "/api/v1/event-types",
        json={
            "slug": f"et-{uuid4().hex[:8]}",
            "title": "Intro",
            "duration_minutes": 60,
            "slot_interval_minutes": 30,
            "min_booking_notice_minutes": 0,
            "buffer_before_minutes": 0,
            "buffer_after_minutes": 30,
            "hosts": [{"user_id": owner, "schedule_id": sid}],
            "booking_limits": [],
        },
    ).json()["id"]
    return et_id, owner


@pytest.mark.asyncio
async def test_create_path_enforces_buffer_on_neighbor_booking(client) -> None:
    et, _owner = _seed_et_http_buf(client)

    first = client.post(
        "/api/v1/bookings",
        headers=HDRS,
        json={
            "event_type_id": et,
            "client_user_id": str(uuid4()),
            "start_time": "2026-10-01T07:00:00Z",  # 09:00 CEST
            "attendee_time_zone": "Europe/Berlin",
        },
    )
    assert first.status_code == 201

    # 10:00 CEST (08:00Z) starts within the 30-min after-buffer of the first
    # booking's 10:00 end → the narrow create-path re-check must reject it too.
    second = client.post(
        "/api/v1/bookings",
        headers=HDRS,
        json={
            "event_type_id": et,
            "client_user_id": str(uuid4()),
            "start_time": "2026-10-01T08:00:00Z",  # 10:00 CEST
            "attendee_time_zone": "Europe/Berlin",
        },
    )
    assert second.status_code == 409
