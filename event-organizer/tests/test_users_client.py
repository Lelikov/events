import json
from uuid import uuid4

import httpx
import pytest

from event_organizer.adapters.users_client import UsersClient
from event_organizer.errors import NotFoundError

BASE, TOKEN = "http://users.test", "t"


def _c(handler):
    return UsersClient(BASE, TOKEN, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_get_user_ok() -> None:
    uid = uuid4()

    def h(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
        assert req.url.path == f"/api/users/id/{uid}"
        body = {"id": str(uid), "email": "a@b.io", "name": "A", "role": "organizer", "time_zone": "UTC"}
        return httpx.Response(200, json=body)

    out = await _c(h).get_user(uid)
    assert out["name"] == "A"


@pytest.mark.asyncio
async def test_get_user_404() -> None:
    with pytest.raises(NotFoundError):
        await _c(lambda _req: httpx.Response(404)).get_user(uuid4())


@pytest.mark.asyncio
async def test_patch_user_forwards_body() -> None:
    uid = uuid4()

    def h(req: httpx.Request) -> httpx.Response:
        assert req.method == "PATCH"
        assert json.loads(req.content) == {"name": "New", "time_zone": "Europe/Moscow"}
        body = {"id": str(uid), "email": "a@b.io", "name": "New", "role": "organizer", "time_zone": "Europe/Moscow"}
        return httpx.Response(200, json=body)

    out = await _c(h).patch_user(uid, {"name": "New", "time_zone": "Europe/Moscow"})
    assert out["name"] == "New"


@pytest.mark.asyncio
async def test_resolve_organizer_hit_and_miss() -> None:
    uid = uuid4()
    body = {"id": str(uid), "email": "a@b.io", "name": "A", "role": "organizer", "time_zone": "UTC"}
    ok_resp = httpx.Response(200, json=body)
    assert await _c(lambda _req: ok_resp).resolve_organizer("a@b.io") == uid
    assert await _c(lambda _req: httpx.Response(404)).resolve_organizer("x@y.io") is None
