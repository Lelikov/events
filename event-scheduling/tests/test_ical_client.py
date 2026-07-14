import httpx
import pytest

from event_scheduling.calendar.ical_client import ICalClient
from event_scheduling.errors import UpstreamError, ValidationError


def _client(handler) -> ICalClient:
    return ICalClient(10.0, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_fetch_returns_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "cal.example"
        return httpx.Response(200, content=b"BEGIN:VCALENDAR")

    out = await _client(handler).fetch("https://cal.example/c.ics")
    assert out == b"BEGIN:VCALENDAR"


@pytest.mark.asyncio
async def test_non_2xx_raises_upstream() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(UpstreamError):
        await _client(handler).fetch("https://cal.example/c.ics")


@pytest.mark.asyncio
async def test_non_http_scheme_rejected() -> None:
    with pytest.raises(ValidationError):
        await ICalClient(10.0).fetch("file:///etc/passwd")


@pytest.mark.asyncio
async def test_oversized_body_rejected(monkeypatch) -> None:
    import event_scheduling.calendar.ical_client as mod

    monkeypatch.setattr(mod, "_MAX_ICS_BYTES", 8)

    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"BEGIN:VCALENDAR-and-more-than-8-bytes")

    with pytest.raises(UpstreamError):
        await _client(handler).fetch("https://cal.example/c.ics")
