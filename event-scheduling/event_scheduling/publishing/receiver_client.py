import httpx


class ReceiverClient:
    """Publishes booking-lifecycle CloudEvents to event-receiver POST /event/booking.

    Auth is the raw shared API key in the Authorization header (NOT "Bearer ...") —
    matches event-receiver's ingress auth for this endpoint.
    """

    def __init__(self, base_url: str, api_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport

    async def publish(self, ce_headers: dict[str, str], body: dict) -> int:
        headers = {**ce_headers, "authorization": self._api_key, "content-type": "application/json"}
        async with httpx.AsyncClient(transport=self._transport, timeout=10.0) as client:
            resp = await client.post(f"{self._base_url}/event/booking", headers=headers, json=body)
        return resp.status_code
