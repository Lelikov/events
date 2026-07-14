from urllib.parse import urlparse

import httpx

from event_scheduling.errors import UpstreamError, ValidationError


class ICalClient:
    def __init__(self, timeout_seconds: float, *, transport: httpx.BaseTransport | None = None) -> None:
        self._timeout = timeout_seconds
        self._transport = transport

    async def fetch(self, url: str) -> bytes:
        scheme = urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValidationError(f"unsupported url scheme: {scheme!r}")
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout, follow_redirects=True) as client:
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                raise UpstreamError(f"iCal fetch failed: {exc}") from exc
        if not resp.is_success:
            raise UpstreamError(f"iCal fetch returned {resp.status_code}")
        return resp.content
