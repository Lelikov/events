from uuid import UUID

import httpx

from event_organizer.errors import NotFoundError, UpstreamError


class UsersClient:
    def __init__(self, base_url: str, token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._transport = transport

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport, timeout=10.0, headers={"authorization": f"Bearer {self._token}"}
        )

    async def get_user(self, user_id: UUID) -> dict:
        async with self._http() as c:
            resp = await c.get(f"{self._base_url}/api/users/id/{user_id}")
        if resp.status_code == httpx.codes.NOT_FOUND:
            raise NotFoundError("user not found")
        if not resp.is_success:
            raise UpstreamError(f"event-users returned {resp.status_code}")
        return resp.json()

    async def patch_user(self, user_id: UUID, body: dict) -> dict:
        async with self._http() as c:
            resp = await c.patch(f"{self._base_url}/api/users/id/{user_id}", json=body)
        if resp.status_code == httpx.codes.NOT_FOUND:
            raise NotFoundError("user not found")
        if not resp.is_success:
            raise UpstreamError(f"event-users returned {resp.status_code}")
        return resp.json()

    async def resolve_organizer(self, email: str) -> UUID | None:
        async with self._http() as c:
            resp = await c.get(f"{self._base_url}/api/users/by-identity", params={"email": email, "role": "organizer"})
        if resp.status_code == httpx.codes.NOT_FOUND:
            return None
        if not resp.is_success:
            raise UpstreamError(f"event-users returned {resp.status_code}")
        return UUID(resp.json()["id"])
