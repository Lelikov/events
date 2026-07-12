from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, field_serializer

from event_scheduling.booking.dto import BookingDTO


def _iso_z(value: datetime) -> str:
    return value.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class CreateBookingRequest(BaseModel):
    event_type_id: UUID
    client_user_id: UUID
    start_time: datetime
    attendee_time_zone: str


class RescheduleRequest(BaseModel):
    start_time: datetime


class BookingResponse(BaseModel):
    id: UUID
    event_type_id: UUID
    host_user_id: UUID
    client_user_id: UUID
    start_time: datetime
    end_time: datetime
    status: str
    attendee_time_zone: str
    created_at: datetime

    @field_serializer("start_time", "end_time", "created_at")
    def _serialize_utc_z(self, value: datetime) -> str:
        return _iso_z(value)

    @classmethod
    def from_dto(cls, b: BookingDTO) -> BookingResponse:
        return cls(**b.__dict__)


class BookingListResponse(BaseModel):
    bookings: list[BookingResponse]


class ChangeEntryModel(BaseModel):
    kind: str
    from_start: datetime | None
    from_end: datetime | None
    to_start: datetime | None
    to_end: datetime | None
    actor_source: str
    actor_user_id: UUID | None
    at: datetime

    @field_serializer("from_start", "from_end", "to_start", "to_end", "at")
    def _serialize_utc_z(self, value: datetime | None) -> str | None:
        if value is None:
            return None
        return _iso_z(value)


class BookingHistoryResponse(BaseModel):
    entries: list[ChangeEntryModel]
