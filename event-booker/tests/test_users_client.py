from uuid import uuid4

import httpx
import pytest

from event_booker.adapters.users_client import UsersClient
from event_booker.errors import ConflictError, UpstreamError


BASE = "http://users.test"
TOKEN = "users-token"  # noqa: S105 - test fixture, not a real secret


def _client(handler) -> UsersClient:
    return UsersClient(BASE, TOKEN, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_get_client_by_email_found() -> None:
    uid = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.url.path == "/api/users/by-identity"
        assert request.url.params["email"] == "a@b.io"
        assert request.url.params["role"] == "client"
        return httpx.Response(
            200, json={"id": str(uid), "email": "a@b.io", "name": "A", "role": "client", "time_zone": "UTC"}
        )

    assert await _client(handler).get_client_by_email("a@b.io") == uid


@pytest.mark.asyncio
async def test_get_client_by_email_404_returns_none() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    assert await _client(handler).get_client_by_email("x@y.io") is None


@pytest.mark.asyncio
async def test_create_client_success() -> None:
    uid = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/api/users"
        import json

        body = json.loads(request.content)
        assert body == {"email": "a@b.io", "name": "A", "role": "client", "time_zone": "Europe/Berlin"}
        return httpx.Response(
            201, json={"id": str(uid), "email": "a@b.io", "name": "A", "role": "client", "time_zone": "Europe/Berlin"}
        )

    assert await _client(handler).create_client("a@b.io", "A", "Europe/Berlin") == uid


@pytest.mark.asyncio
async def test_create_client_409_raises_conflict() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "dup"})

    with pytest.raises(ConflictError):
        await _client(handler).create_client("a@b.io", "A", "UTC")


@pytest.mark.asyncio
async def test_create_client_5xx_raises_upstream() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(UpstreamError):
        await _client(handler).create_client("a@b.io", "A", "UTC")
