import json
from uuid import uuid4

import httpx
import pytest

from event_organizer.adapters.scheduling_client import SchedulingClient
from event_organizer.errors import ConflictError, NotFoundError, UpstreamError, ValidationError

BASE, KEY = "http://sched.test", "k"


def _c(handler):
    return SchedulingClient(BASE, KEY, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_get_schedule_ok_and_bearer() -> None:
    uid = uuid4()

    def h(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == f"Bearer {KEY}"
        assert req.url.path == f"/api/v1/schedules/{uid}"
        return httpx.Response(200, json={"schedule": {"owner_user_id": str(uid)}, "weekly_hours": []})

    out = await _c(h).get_schedule(uid)
    assert out["schedule"]["owner_user_id"] == str(uid)


@pytest.mark.asyncio
async def test_get_schedule_404_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await _c(lambda _req: httpx.Response(404)).get_schedule(uuid4())


@pytest.mark.asyncio
async def test_put_schedule_forwards_body() -> None:
    uid = uuid4()

    def h(req: httpx.Request) -> httpx.Response:
        assert req.method == "PUT"
        assert json.loads(req.content)["time_zone"] == "UTC"
        return httpx.Response(200, json={"schedule": {"owner_user_id": str(uid)}, "weekly_hours": []})

    await _c(h).put_schedule(uid, {"time_zone": "UTC", "weekly_hours": [], "date_overrides": []})


@pytest.mark.asyncio
async def test_get_bookings_query_and_unwrap() -> None:
    uid = uuid4()

    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/bookings"
        assert req.url.params["host_user_id"] == str(uid)
        return httpx.Response(200, json={"bookings": [{"id": str(uuid4()), "status": "confirmed"}]})

    out = await _c(h).get_bookings(uid)
    assert len(out) == 1
    assert out[0]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_5xx_raises_upstream() -> None:
    with pytest.raises(UpstreamError):
        await _c(lambda _req: httpx.Response(503)).get_schedule(uuid4())


@pytest.mark.asyncio
async def test_get_booking_detail_ok_and_path() -> None:
    bid = str(uuid4())

    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/api/v1/bookings/{bid}/detail"
        return httpx.Response(200, json={"uid": bid, "title": "Консультация", "status": "confirmed"})

    out = await _c(h).get_booking_detail(bid)
    assert out["title"] == "Консультация"


@pytest.mark.asyncio
async def test_get_booking_detail_404_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await _c(lambda _req: httpx.Response(404)).get_booking_detail(str(uuid4()))


@pytest.mark.asyncio
async def test_422_raises_validation_with_upstream_detail() -> None:
    def h(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "weekly_hours times must be on the hour (day 1)"})

    with pytest.raises(ValidationError) as ei:
        await _c(h).put_schedule(uuid4(), {"time_zone": "UTC", "weekly_hours": [], "date_overrides": []})
    assert "on the hour" in str(ei.value)


@pytest.mark.asyncio
async def test_get_slots_params() -> None:
    et = str(uuid4())

    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/slots"
        assert req.url.params["event_type_id"] == et
        assert req.url.params["time_zone"] == "Europe/Moscow"
        return httpx.Response(
            200,
            json={"event_type_id": et, "time_zone": "Europe/Moscow", "slots": {"2026-10-01": ["2026-10-01T09:00:00Z"]}},
        )

    out = await _c(h).get_slots(et, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", "Europe/Moscow")
    assert out["slots"]["2026-10-01"] == ["2026-10-01T09:00:00Z"]


@pytest.mark.asyncio
async def test_reschedule_sends_body_and_actor_headers() -> None:
    import json

    bid, uid = str(uuid4()), uuid4()

    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/api/v1/bookings/{bid}/reschedule"
        assert req.headers["actor-source"] == "organizer"
        assert req.headers["actor-user-id"] == str(uid)
        assert json.loads(req.content)["start_time"] == "2026-10-01T09:00:00Z"
        return httpx.Response(200, json={"id": bid, "status": "confirmed"})

    out = await _c(h).reschedule_booking(bid, "2026-10-01T09:00:00Z", uid)
    assert out["status"] == "confirmed"


@pytest.mark.asyncio
async def test_409_raises_conflict_with_detail() -> None:
    def h(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "host is not available at the new time"})

    with pytest.raises(ConflictError) as ei:
        await _c(h).reschedule_booking(str(uuid4()), "2026-10-01T09:00:00Z", uuid4())
    assert "not available" in str(ei.value)


@pytest.mark.asyncio
async def test_get_event_type_path() -> None:
    et = str(uuid4())

    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/api/v1/event-types/{et}"
        return httpx.Response(200, json={"id": et, "hosts": [{"user_id": str(uuid4()), "schedule_id": str(uuid4())}]})

    out = await _c(h).get_event_type(et)
    assert len(out["hosts"]) == 1


@pytest.mark.asyncio
async def test_reassign_sends_body_and_actor_headers() -> None:
    import json

    bid, new_host, uid = str(uuid4()), str(uuid4()), uuid4()

    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/api/v1/bookings/{bid}/reassign"
        assert req.headers["actor-source"] == "organizer"
        assert req.headers["actor-user-id"] == str(uid)
        assert json.loads(req.content)["new_host_user_id"] == new_host
        return httpx.Response(200, json={"id": bid, "host_user_id": new_host})

    out = await _c(h).reassign_booking(bid, new_host, uid)
    assert out["host_user_id"] == new_host


@pytest.mark.asyncio
async def test_reassign_422_raises_validation() -> None:
    def h(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "new host is not a host of this event type"})

    with pytest.raises(ValidationError):
        await _c(h).reassign_booking(str(uuid4()), str(uuid4()), uuid4())
