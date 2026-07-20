from uuid import UUID

import httpx

from event_organizer.errors import NotFoundError, UpstreamError, ValidationError


class SchedulingClient:
    def __init__(self, base_url: str, api_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport, timeout=10.0, headers={"authorization": f"Bearer {self._api_key}"}
        )

    @staticmethod
    def _detail(resp: httpx.Response) -> str:
        try:
            body = resp.json()
        except ValueError:
            return f"event-scheduling returned {resp.status_code}"
        detail = body.get("detail") if isinstance(body, dict) else None
        return detail if isinstance(detail, str) else f"event-scheduling returned {resp.status_code}"

    @classmethod
    def _ok(cls, resp: httpx.Response) -> dict:
        if resp.status_code == httpx.codes.NOT_FOUND:
            raise NotFoundError("schedule not found")
        # Forward a domain validation rejection (e.g. non-whole-hour times) as a
        # 422 with the upstream message, not a generic 502 that hides the reason.
        if resp.status_code == httpx.codes.UNPROCESSABLE_ENTITY:
            raise ValidationError(cls._detail(resp))
        if not resp.is_success:
            raise UpstreamError(f"event-scheduling returned {resp.status_code}")
        return resp.json()

    async def get_schedule(self, owner_user_id: UUID) -> dict:
        async with self._http() as c:
            resp = await c.get(f"{self._base_url}/api/v1/schedules/{owner_user_id}")
        return self._ok(resp)

    async def put_schedule(self, owner_user_id: UUID, body: dict) -> dict:
        async with self._http() as c:
            resp = await c.put(f"{self._base_url}/api/v1/schedules/{owner_user_id}", json=body)
        return self._ok(resp)

    async def put_travel(self, owner_user_id: UUID, body: dict) -> dict:
        async with self._http() as c:
            resp = await c.put(f"{self._base_url}/api/v1/schedules/{owner_user_id}/travel", json=body)
        return self._ok(resp)

    async def get_bookings(self, host_user_id: UUID) -> list[dict]:
        async with self._http() as c:
            resp = await c.get(f"{self._base_url}/api/v1/bookings", params={"host_user_id": str(host_user_id)})
        if not resp.is_success:
            raise UpstreamError(f"event-scheduling returned {resp.status_code}")
        return resp.json()["bookings"]

    async def get_booking_detail(self, booking_id: UUID) -> dict:
        async with self._http() as c:
            resp = await c.get(f"{self._base_url}/api/v1/bookings/{booking_id}/detail")
        return self._ok(resp)
