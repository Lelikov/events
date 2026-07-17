from uuid import uuid4

import httpx
import pytest

from event_booker.adapters.scheduling_client import SchedulingClient
from event_booker.dto import AnswerDTO


@pytest.mark.asyncio
async def test_get_event_type_parses_booking_fields():
    et_id = uuid4()

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json={
                "id": str(et_id),
                "slug": "s",
                "title": "T",
                "duration_minutes": 30,
                "booking_fields": [
                    {
                        "field_key": "reason",
                        "field_type": "textarea",
                        "label": "Reason",
                        "placeholder": None,
                        "required": True,
                        "options": [],
                        "position": 0,
                    }
                ],
            },
        )

    c = SchedulingClient("http://sched", "k", transport=httpx.MockTransport(handler))
    et = await c.get_event_type(et_id)
    assert [f.field_key for f in et.booking_fields] == ["reason"]
    assert et.booking_fields[0].required is True


@pytest.mark.asyncio
async def test_create_booking_forwards_answers():
    sent = {}

    def handler(req: httpx.Request) -> httpx.Response:
        import json

        sent.update(json.loads(req.content))
        return httpx.Response(
            201,
            json={
                "id": str(uuid4()),
                "start_time": "2026-10-01T09:00:00Z",
                "end_time": "2026-10-01T09:30:00Z",
                "status": "confirmed",
            },
        )

    c = SchedulingClient("http://sched", "k", transport=httpx.MockTransport(handler))
    await c.create_booking(
        uuid4(),
        uuid4(),
        __import__("datetime").datetime(2026, 10, 1, 9, 0),
        "UTC",
        field_answers=[AnswerDTO("reason", "help"), AnswerDTO("topics", ["a", "b"])],
    )
    assert sent["field_answers"] == [{"key": "reason", "value": "help"}, {"key": "topics", "value": ["a", "b"]}]


@pytest.mark.asyncio
async def test_create_booking_maps_upstream_422_to_validation_error():
    from event_booker.errors import ValidationError

    def handler(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json={"detail": "field 'reason' is required"})

    c = SchedulingClient("http://sched", "k", transport=httpx.MockTransport(handler))
    with pytest.raises(ValidationError, match="reason"):
        await c.create_booking(
            uuid4(), uuid4(), __import__("datetime").datetime(2026, 10, 1, 9, 0), "UTC", field_answers=[]
        )
