import datetime as dt
import json
from uuid import uuid4

import httpx
import pytest

from event_booker.adapters.scheduling_client import SchedulingClient
from event_booker.errors import NotFoundError, SlotUnavailableError, UpstreamError


BASE = "http://scheduling.test"
KEY = "sched-key"


def _client(handler) -> SchedulingClient:
    return SchedulingClient(BASE, KEY, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_event_types_projects_public_fields() -> None:
    et_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.url.path == "/api/v1/event-types"
        return httpx.Response(
            200,
            json={
                "items": [
                    {
                        "id": str(et_id),
                        "slug": "intro",
                        "title": "Intro",
                        "duration_minutes": 30,
                        "scheduling_type": "collective",
                        "hosts": [],
                        "booking_limits": [],
                    }
                ]
            },
        )

    out = await _client(handler).list_event_types()
    assert len(out) == 1
    assert out[0].id == et_id
    assert out[0].slug == "intro"
    assert out[0].title == "Intro"
    assert out[0].duration_minutes == 30


@pytest.mark.asyncio
async def test_get_event_type_404_raises_not_found() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "nope"})

    with pytest.raises(NotFoundError):
        await _client(handler).get_event_type(uuid4())


@pytest.mark.asyncio
async def test_get_slots_passthrough() -> None:
    et_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/slots"
        assert request.url.params["event_type_id"] == str(et_id)
        assert request.url.params["time_zone"] == "Europe/Berlin"
        return httpx.Response(
            200,
            json={
                "event_type_id": str(et_id),
                "time_zone": "Europe/Berlin",
                "slots": {"2026-10-01": ["2026-10-01T09:00:00Z"]},
            },
        )

    out = await _client(handler).get_slots(
        et_id, dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC), "Europe/Berlin"
    )
    assert out.time_zone == "Europe/Berlin"
    assert out.slots == {"2026-10-01": ["2026-10-01T09:00:00Z"]}


@pytest.mark.asyncio
async def test_create_booking_success_and_sets_actor_header() -> None:
    et_id, client_id, booking_id = uuid4(), uuid4(), uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/v1/bookings"
        assert request.headers["actor_source"] == "booker"
        body = json.loads(request.content)
        assert body == {
            "event_type_id": str(et_id),
            "client_user_id": str(client_id),
            "start_time": "2026-10-01T09:00:00+00:00",
            "attendee_time_zone": "Europe/Berlin",
            "field_answers": [],
        }
        return httpx.Response(
            201,
            json={
                "id": str(booking_id),
                "event_type_id": str(et_id),
                "host_user_id": str(uuid4()),
                "client_user_id": str(client_id),
                "start_time": "2026-10-01T09:00:00Z",
                "end_time": "2026-10-01T09:30:00Z",
                "status": "confirmed",
                "attendee_time_zone": "Europe/Berlin",
                "created_at": "2026-09-01T00:00:00Z",
            },
        )

    out = await _client(handler).create_booking(
        et_id, client_id, dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC), "Europe/Berlin"
    )
    assert out.id == booking_id
    assert out.status == "confirmed"


@pytest.mark.asyncio
async def test_create_booking_409_raises_slot_unavailable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "slot taken"})

    with pytest.raises(SlotUnavailableError):
        await _client(handler).create_booking(uuid4(), uuid4(), dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC), "UTC")


@pytest.mark.asyncio
async def test_create_booking_5xx_raises_upstream() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="boom")

    with pytest.raises(UpstreamError):
        await _client(handler).create_booking(uuid4(), uuid4(), dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC), "UTC")
