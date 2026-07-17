import datetime as dt
from collections.abc import Generator
from uuid import uuid4

import pytest

from event_booker.dto import BookingResult, EventTypeDTO, SlotsResult
from event_booker.errors import NotFoundError, SlotUnavailableError, UpstreamError


ET_ID = uuid4()


class _FakeScheduling:
    def __init__(self, *, mode: str = "ok") -> None:
        self._mode = mode

    async def list_event_types(self):
        return [EventTypeDTO(id=ET_ID, slug="intro", title="Intro", duration_minutes=30)]

    async def get_event_type(self, event_type_id):
        if self._mode == "missing":
            raise NotFoundError("event type not found")
        return EventTypeDTO(id=event_type_id, slug="intro", title="Intro", duration_minutes=30)

    async def get_slots(self, event_type_id, start, end, time_zone):
        if self._mode == "upstream":
            raise UpstreamError("event-scheduling returned 503")
        return SlotsResult(
            event_type_id=event_type_id, time_zone=time_zone, slots={"2026-10-01": ["2026-10-01T09:00:00Z"]}
        )

    async def create_booking(self, event_type_id, client_user_id, start_time, attendee_time_zone, field_answers=None):
        if self._mode == "conflict":
            raise SlotUnavailableError("slot no longer available")
        return BookingResult(
            id=uuid4(), start_time=start_time, end_time=start_time + dt.timedelta(minutes=30), status="confirmed"
        )


class _FakeUsers:
    async def get_client_by_email(self, email):
        return uuid4()

    async def create_client(self, email, name, time_zone):
        return uuid4()


def _make_client(mode: str = "ok"):
    from dishka import Provider, Scope, make_async_container, provide
    from dishka.integrations.fastapi import FastapiProvider, setup_dishka
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from starlette.testclient import TestClient

    from event_booker.errors import ConflictError, NotFoundError, SlotUnavailableError, UpstreamError, ValidationError
    from event_booker.interfaces.clients import ISchedulingClient, IUsersClient
    from event_booker.ioc import AppProvider
    from event_booker.routers.public import public_router
    from event_booker.routes import root_router
    from event_booker.services.guest_booking import GuestBookingService

    class FakeProvider(Provider):
        @provide(scope=Scope.APP, override=True)
        def sched(self) -> ISchedulingClient:
            return _FakeScheduling(mode=mode)

        @provide(scope=Scope.APP, override=True)
        def users(self) -> IUsersClient:
            return _FakeUsers()

        @provide(scope=Scope.APP, override=True)
        def svc(self, scheduling: ISchedulingClient, users: IUsersClient) -> GuestBookingService:
            return GuestBookingService(scheduling, users)

    container = make_async_container(AppProvider(), FakeProvider(), FastapiProvider())
    app = FastAPI()
    setup_dishka(container=container, app=app)
    app.include_router(root_router)
    app.include_router(public_router)

    status = {
        ValidationError: 422,
        NotFoundError: 404,
        ConflictError: 409,
        SlotUnavailableError: 409,
        UpstreamError: 502,
    }

    async def handler(_: Request, exc: Exception) -> JSONResponse:
        code = next((c for t, c in status.items() if isinstance(exc, t)), 500)
        return JSONResponse(status_code=code, content={"detail": str(exc)})

    for err in (ValidationError, NotFoundError, ConflictError, SlotUnavailableError, UpstreamError):
        app.add_exception_handler(err, handler)
    return TestClient(app)


@pytest.fixture
def booker() -> Generator:
    with _make_client() as c:
        yield c


def test_list_event_types(booker) -> None:
    r = booker.get("/api/public/event-types")
    assert r.status_code == 200
    assert r.json()["items"][0]["title"] == "Intro"


def test_get_slots(booker) -> None:
    r = booker.get(
        "/api/public/slots",
        params={
            "event_type_id": str(ET_ID),
            "start": "2026-10-01T00:00:00Z",
            "end": "2026-10-02T00:00:00Z",
            "time_zone": "UTC",
        },
    )
    assert r.status_code == 200
    assert r.json()["slots"] == {"2026-10-01": ["2026-10-01T09:00:00Z"]}


def test_create_booking_confirmation_hides_internal_ids(booker) -> None:
    r = booker.post(
        "/api/public/bookings",
        json={
            "event_type_id": str(ET_ID),
            "name": "A",
            "email": "a@b.io",
            "start_time": "2026-10-01T09:00:00Z",
            "time_zone": "Europe/Berlin",
        },
    )
    assert r.status_code == 201
    payload = r.json()
    assert payload["event_type_title"] == "Intro"
    assert payload["status"] == "confirmed"
    assert "client_user_id" not in payload
    assert "host_user_id" not in payload


def test_create_booking_slot_conflict_returns_409() -> None:
    with _make_client(mode="conflict") as c:
        r = c.post(
            "/api/public/bookings",
            json={
                "event_type_id": str(ET_ID),
                "name": "A",
                "email": "a@b.io",
                "start_time": "2026-10-01T09:00:00Z",
                "time_zone": "UTC",
            },
        )
    assert r.status_code == 409


def test_create_booking_bad_email_returns_422(booker) -> None:
    r = booker.post(
        "/api/public/bookings",
        json={
            "event_type_id": str(ET_ID),
            "name": "A",
            "email": "not-an-email",
            "start_time": "2026-10-01T09:00:00Z",
            "time_zone": "UTC",
        },
    )
    assert r.status_code == 422


def test_get_slots_upstream_error_returns_502() -> None:
    with _make_client(mode="upstream") as c:
        r = c.get(
            "/api/public/slots",
            params={
                "event_type_id": str(ET_ID),
                "start": "2026-10-01T00:00:00Z",
                "end": "2026-10-02T00:00:00Z",
                "time_zone": "UTC",
            },
        )
    assert r.status_code == 502
