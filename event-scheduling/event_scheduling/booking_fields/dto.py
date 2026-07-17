from dataclasses import dataclass


@dataclass(frozen=True)
class OptionDTO:
    value: str
    label: str


@dataclass(frozen=True)
class BookingFieldDTO:
    field_key: str
    field_type: str
    label: str
    placeholder: str | None
    required: bool
    options: list[OptionDTO]
    position: int


@dataclass(frozen=True)
class UpsertBookingFieldDTO:
    field_type: str
    label: str
    placeholder: str | None
    required: bool
    options: list[OptionDTO]


@dataclass(frozen=True)
class AnswerDTO:
    key: str
    value: str | list[str] | bool


@dataclass(frozen=True)
class AnsweredFieldDTO:
    key: str
    label: str
    field_type: str
    value: str | list[str] | bool
