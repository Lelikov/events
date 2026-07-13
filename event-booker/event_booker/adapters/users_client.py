from uuid import UUID

import httpx

from event_booker.errors import ConflictError, UpstreamError


_CLIENT_ROLE = "client"


class UsersClient:
    def __init__(self, base_url: str, token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._transport = transport

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport, timeout=10.0, headers={"authorization": f"Bearer {self._token}"}
        )

    async def get_client_by_email(self, email: str) -> UUID | None:
        async with self._http() as client:
            resp = await client.get(
                f"{self._base_url}/api/users/by-identity", params={"email": email, "role": _CLIENT_ROLE}
            )
        if resp.status_code == httpx.codes.NOT_FOUND:
            return None
        if not resp.is_success:
            raise UpstreamError(f"event-users returned {resp.status_code}")
        return UUID(resp.json()["id"])

    async def create_client(self, email: str, name: str, time_zone: str) -> UUID:
        body = {"email": email, "name": name, "role": _CLIENT_ROLE, "time_zone": time_zone}
        async with self._http() as client:
            resp = await client.post(f"{self._base_url}/api/users", json=body)
        if resp.status_code == httpx.codes.CONFLICT:
            raise ConflictError("client already exists")
        if resp.status_code != httpx.codes.CREATED:
            raise UpstreamError(f"event-users returned {resp.status_code}")
        return UUID(resp.json()["id"])
