from __future__ import annotations

from pydantic import BaseModel

from event_scheduling.booking_fields.dto import BookingFieldDTO, OptionDTO, UpsertBookingFieldDTO


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
    position: int

    @classmethod
    def from_dto(cls, d: BookingFieldDTO) -> BookingFieldModel:
        return cls(
            field_key=d.field_key,
            field_type=d.field_type,
            label=d.label,
            placeholder=d.placeholder,
            required=d.required,
            options=[OptionModel(value=o.value, label=o.label) for o in d.options],
            position=d.position,
        )


class UpsertBookingFieldModel(BaseModel):
    field_type: str
    label: str
    placeholder: str | None = None
    required: bool = False
    options: list[OptionModel] = []

    def to_dto(self) -> UpsertBookingFieldDTO:
        return UpsertBookingFieldDTO(
            field_type=self.field_type,
            label=self.label,
            placeholder=self.placeholder,
            required=self.required,
            options=[OptionDTO(value=o.value, label=o.label) for o in self.options],
        )


class BookingFieldListResponse(BaseModel):
    items: list[BookingFieldModel]


class ReplaceBookingFieldsRequest(BaseModel):
    items: list[UpsertBookingFieldModel]
