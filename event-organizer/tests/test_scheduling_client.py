import json
from uuid import uuid4

import httpx
import pytest

from event_organizer.adapters.scheduling_client import SchedulingClient
from event_organizer.errors import NotFoundError, UpstreamError

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
