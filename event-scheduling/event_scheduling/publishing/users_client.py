from uuid import UUID

import httpx

from event_scheduling.publishing.dto import ParticipantInfo


class UsersClient:
    """Resolves participant UUIDs to email/time_zone via event-users.

    Matches the real event-users contract (event_users/routes.py::get_users_by_ids,
    event_users/schemas/users.py::GetUsersByIdsRequest/Response):
      POST {base_url}/api/users/by-ids   body {"ids": [<uuid str>, ...]}
      -> 200 {"items": [{"id", "email", "time_zone", ...}, ...]}
    The route is gated by require_admin (Bearer token: static service token or JWT).
    Ids event-users doesn't find are simply absent from "items" — never an error.
    """

    def __init__(self, base_url: str, bearer_token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._bearer_token = bearer_token
        self._transport = transport

    async def by_ids(self, user_ids: list[UUID]) -> dict[UUID, ParticipantInfo]:
        ids = [str(u) for u in user_ids]
        headers = {"authorization": f"Bearer {self._bearer_token}"}
        async with httpx.AsyncClient(transport=self._transport, timeout=10.0) as client:
            resp = await client.post(f"{self._base_url}/api/users/by-ids", headers=headers, json={"ids": ids})
        resp.raise_for_status()
        return self._parse(resp.json())

    @staticmethod
    def _parse(data: dict) -> dict[UUID, ParticipantInfo]:
        return {UUID(row["id"]): ParticipantInfo(row["email"], row.get("time_zone")) for row in data["items"]}
