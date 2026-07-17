from __future__ import annotations
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr

from event_booker.dto import BookingConfirmation, BookingFieldDTO, EventTypeDTO, SlotsResult


class OptionModel(BaseModel):
    value: str
    label: str


class BookingFieldModel(BaseModel):
    field_key: str
    field_type: str
    label: str
    placeholder: str | None
    required: bool
    options: list[OptionModel]

    @classmethod
    def from_dto(cls, d: BookingFieldDTO) -> BookingFieldModel:
        return cls(
            field_key=d.field_key,
            field_type=d.field_type,
            label=d.label,
            placeholder=d.placeholder,
            required=d.required,
            options=[OptionModel(value=o.value, label=o.label) for o in d.options],
        )


class EventTypeModel(BaseModel):
    id: UUID
    slug: str
    title: str
    duration_minutes: int
    booking_fields: list[BookingFieldModel] = []

    @classmethod
    def from_dto(cls, d: EventTypeDTO) -> EventTypeModel:
        return cls(
            id=d.id,
            slug=d.slug,
            title=d.title,
            duration_minutes=d.duration_minutes,
            booking_fields=[BookingFieldModel.from_dto(f) for f in d.booking_fields],
        )


class EventTypeListResponse(BaseModel):
    items: list[EventTypeModel]


class SlotsPublicResponse(BaseModel):
    event_type_id: UUID
    time_zone: str
    slots: dict[str, list[str]]

    @classmethod
    def from_result(cls, r: SlotsResult) -> SlotsPublicResponse:
        return cls(event_type_id=r.event_type_id, time_zone=r.time_zone, slots=r.slots)


class AnswerModel(BaseModel):
    key: str
    value: str | list[str] | bool


class CreateBookingPublicRequest(BaseModel):
    event_type_id: UUID
    name: str
    email: EmailStr
    start_time: datetime
    time_zone: str
    answers: list[AnswerModel] = []


class BookingConfirmationResponse(BaseModel):
    booking_id: UUID
    event_type_title: str
    start_time: datetime
    end_time: datetime
    status: str
    time_zone: str

    @classmethod
    def from_confirmation(cls, c: BookingConfirmation) -> BookingConfirmationResponse:
        return cls(
            booking_id=c.booking_id,
            event_type_title=c.event_type_title,
            start_time=c.start_time,
            end_time=c.end_time,
            status=c.status,
            time_zone=c.time_zone,
        )
