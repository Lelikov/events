import json
from uuid import uuid4

import httpx
import pytest

from event_scheduling.publishing.receiver_client import ReceiverClient
from event_scheduling.publishing.users_client import UsersClient


@pytest.mark.asyncio
async def test_receiver_publish_sends_headers_and_key() -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["ce-type"] = req.headers.get("ce-type")
        seen["body"] = json.loads(req.content)
        return httpx.Response(202)

    client = ReceiverClient("http://receiver:8888", "SECRET-KEY", transport=httpx.MockTransport(handler))
    status = await client.publish({"ce-type": "booking.created", "ce-id": "x"}, {"booking_uid": "bk-1"})
    assert status == 202
    assert seen["url"] == "http://receiver:8888/event/booking"
    assert seen["auth"] == "SECRET-KEY"  # raw, not Bearer
    assert seen["ce-type"] == "booking.created"
    assert seen["body"] == {"booking_uid": "bk-1"}


@pytest.mark.asyncio
async def test_receiver_publish_propagates_transport_errors() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("boom", request=req)

    client = ReceiverClient("http://receiver:8888", "SECRET-KEY", transport=httpx.MockTransport(handler))
    with pytest.raises(httpx.ConnectError):
        await client.publish({"ce-type": "booking.created"}, {"booking_uid": "bk-1"})


@pytest.mark.asyncio
async def test_users_by_ids_maps_email_tz() -> None:
    # Real event-users contract (event_users/routes.py get_users_by_ids +
    # event_users/schemas/users.py GetUsersByIdsRequest/Response):
    #   POST /api/users/by-ids  body {"ids": [<uuid str>, ...]}  (require_admin: Bearer)
    #   -> 200 {"items": [{"id", "email", ..., "time_zone", ...}, ...]}
    a, b = uuid4(), uuid4()
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["method"] = req.method
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["body"] = json.loads(req.content)
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": str(a), "email": "a@x.io", "time_zone": "Europe/Berlin"},
                    {"id": str(b), "email": "b@x.io", "time_zone": None},
                ]
            },
        )

    client = UsersClient("http://users:8001", "USERS-TOKEN", transport=httpx.MockTransport(handler))
    out = await client.by_ids([a, b])

    assert seen["method"] == "POST"
    assert seen["url"] == "http://users:8001/api/users/by-ids"
    assert seen["auth"] == "Bearer USERS-TOKEN"
    assert seen["body"] == {"ids": [str(a), str(b)]}
    assert out[a].email == "a@x.io"
    assert out[a].time_zone == "Europe/Berlin"
    assert out[b].time_zone is None


@pytest.mark.asyncio
async def test_users_by_ids_maps_name_and_locale() -> None:
    a = uuid4()

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "items": [
                    {"id": str(a), "email": "a@x.io", "time_zone": "Europe/Berlin", "name": "Alice", "locale": "en"}
                ]
            },
        )

    client = UsersClient("http://users:8888", "tok", transport=httpx.MockTransport(handler))
    out = await client.by_ids([a])
    assert out[a].name == "Alice"
    assert out[a].locale == "en"
    assert out[a].email == "a@x.io"


@pytest.mark.asyncio
async def test_users_by_ids_missing_ids_absent_from_map() -> None:
    a, b = uuid4(), uuid4()

    def handler(_req: httpx.Request) -> httpx.Response:
        # b was not found by event-users; it's simply omitted from "items".
        return httpx.Response(200, json={"items": [{"id": str(a), "email": "a@x.io", "time_zone": None}]})

    client = UsersClient("http://users:8001", "USERS-TOKEN", transport=httpx.MockTransport(handler))
    out = await client.by_ids([a, b])

    assert a in out
    assert b not in out
