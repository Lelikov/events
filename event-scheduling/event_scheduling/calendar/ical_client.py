from urllib.parse import urlparse

import httpx

from event_scheduling.errors import UpstreamError, ValidationError


_MAX_ICS_BYTES = 2 * 1024 * 1024


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
                async with client.stream("GET", url) as resp:
                    if not resp.is_success:
                        raise UpstreamError(f"iCal fetch returned {resp.status_code}")
                    buffer = bytearray()
                    async for chunk in resp.aiter_bytes():
                        buffer.extend(chunk)
                        if len(buffer) > _MAX_ICS_BYTES:
                            raise UpstreamError("iCal payload too large")
            except httpx.HTTPError as exc:
                raise UpstreamError(f"iCal fetch failed: {exc}") from exc
        return bytes(buffer)
