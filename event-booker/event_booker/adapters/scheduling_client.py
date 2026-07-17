from datetime import datetime
from uuid import UUID

import httpx

from event_booker.dto import AnswerDTO, BookingFieldDTO, BookingResult, EventTypeDTO, OptionDTO, SlotsResult
from event_booker.errors import NotFoundError, SlotUnavailableError, UpstreamError


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value)


class SchedulingClient:
    def __init__(self, base_url: str, api_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport, timeout=10.0, headers={"authorization": f"Bearer {self._api_key}"}
        )

    async def list_event_types(self) -> list[EventTypeDTO]:
        async with self._http() as client:
            resp = await client.get(f"{self._base_url}/api/v1/event-types")
        self._raise_for_status(resp)
        return [self._to_event_type(item) for item in resp.json()["items"]]

    async def get_event_type(self, event_type_id: UUID) -> EventTypeDTO:
        async with self._http() as client:
            resp = await client.get(f"{self._base_url}/api/v1/event-types/{event_type_id}")
        if resp.status_code == httpx.codes.NOT_FOUND:
            raise NotFoundError("event type not found")
        self._raise_for_status(resp)
        return self._to_event_type(resp.json())

    async def get_slots(self, event_type_id: UUID, start: datetime, end: datetime, time_zone: str) -> SlotsResult:
        params = {
            "event_type_id": str(event_type_id),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "time_zone": time_zone,
        }
        async with self._http() as client:
            resp = await client.get(f"{self._base_url}/api/v1/slots", params=params)
        self._raise_for_status(resp)
        data = resp.json()
        return SlotsResult(event_type_id=event_type_id, time_zone=data["time_zone"], slots=data["slots"])

    async def create_booking(
        self,
        event_type_id: UUID,
        client_user_id: UUID,
        start_time: datetime,
        attendee_time_zone: str,
        field_answers: list[AnswerDTO] | None = None,
    ) -> BookingResult:
        body = {
            "event_type_id": str(event_type_id),
            "client_user_id": str(client_user_id),
            "start_time": start_time.isoformat(),
            "attendee_time_zone": attendee_time_zone,
            "field_answers": [{"key": a.key, "value": a.value} for a in (field_answers or [])],
        }
        async with self._http() as client:
            resp = await client.post(f"{self._base_url}/api/v1/bookings", json=body, headers={"actor_source": "booker"})
        if resp.status_code == httpx.codes.CONFLICT:
            raise SlotUnavailableError("slot no longer available")
        if resp.status_code == httpx.codes.NOT_FOUND:
            raise NotFoundError("event type not found")
        self._raise_for_status(resp)
        data = resp.json()
        return BookingResult(
            id=UUID(data["id"]),
            start_time=_dt(data["start_time"]),
            end_time=_dt(data["end_time"]),
            status=data["status"],
        )

    @staticmethod
    def _to_event_type(item: dict) -> EventTypeDTO:
        fields = [
            BookingFieldDTO(
                field_key=f["field_key"],
                field_type=f["field_type"],
                label=f["label"],
                placeholder=f.get("placeholder"),
                required=f["required"],
                options=[OptionDTO(value=o["value"], label=o["label"]) for o in (f.get("options") or [])],
            )
            for f in item.get("booking_fields", [])
        ]
        return EventTypeDTO(
            id=UUID(item["id"]),
            slug=item["slug"],
            title=item["title"],
            duration_minutes=item["duration_minutes"],
            booking_fields=fields,
        )

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        raise UpstreamError(f"event-scheduling returned {resp.status_code}")
