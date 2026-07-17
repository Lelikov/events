import re
import unicodedata

from event_scheduling.booking_fields.dto import (
    AnswerDTO,
    AnsweredFieldDTO,
    BookingFieldDTO,
    UpsertBookingFieldDTO,
)
from event_scheduling.errors import ValidationError


FIELD_TYPES = frozenset({"text", "textarea", "select", "radio", "checkbox", "boolean"})
OPTION_TYPES = frozenset({"select", "radio", "checkbox"})

_CYR = {
    "а": "a",
    "б": "b",
    "в": "v",
    "г": "g",
    "д": "d",
    "е": "e",
    "ё": "e",
    "ж": "zh",
    "з": "z",
    "и": "i",
    "й": "i",
    "к": "k",
    "л": "l",
    "м": "m",
    "н": "n",
    "о": "o",
    "п": "p",
    "р": "r",
    "с": "s",
    "т": "t",
    "у": "u",
    "ф": "f",
    "х": "h",
    "ц": "c",
    "ч": "ch",
    "ш": "sh",
    "щ": "shch",
    "ъ": "",
    "ы": "y",
    "ь": "",
    "э": "e",
    "ю": "yu",
    "я": "ya",
}


def slugify_key(label: str) -> str:
    lowered = label.strip().lower()
    translit = "".join(_CYR.get(ch, ch) for ch in lowered)
    ascii_only = unicodedata.normalize("NFKD", translit).encode("ascii", "ignore").decode()
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_only).strip("-")
    return slug or "field"


def assign_keys(items: list[UpsertBookingFieldDTO]) -> list[str]:
    seen: dict[str, int] = {}
    keys: list[str] = []
    for it in items:
        base = slugify_key(it.label)
        if base not in seen:
            seen[base] = 1
            keys.append(base)
            continue
        seen[base] += 1
        keys.append(f"{base}-{seen[base]}")
    return keys


def validate_field_items(items: list[UpsertBookingFieldDTO]) -> None:
    for it in items:
        if not it.label.strip():
            raise ValidationError("booking field label must not be empty")
        if it.field_type not in FIELD_TYPES:
            raise ValidationError(f"unknown field_type {it.field_type!r}")
        is_option = it.field_type in OPTION_TYPES
        if is_option and len(it.options) < 1:
            raise ValidationError(f"field {it.label!r} of type {it.field_type} needs at least one option")
        if not is_option and it.options:
            raise ValidationError(f"field {it.label!r} of type {it.field_type} must not have options")
        values = [o.value for o in it.options]
        if is_option and (any(not v.strip() for v in values) or len(set(values)) != len(values)):
            raise ValidationError(f"field {it.label!r} has empty or duplicate option values")


def _validate_one(field: BookingFieldDTO, value: object) -> str | list[str] | bool:
    ftype = field.field_type
    opt_values = {o.value for o in field.options}
    if ftype in ("text", "textarea"):
        if not isinstance(value, str):
            raise ValidationError(f"field {field.field_key!r} expects text")
        return value
    if ftype in ("select", "radio"):
        if not isinstance(value, str) or value not in opt_values:
            raise ValidationError(f"field {field.field_key!r} has an invalid choice")
        return value
    if ftype == "checkbox":
        if not isinstance(value, list) or any(v not in opt_values for v in value) or len(set(value)) != len(value):
            raise ValidationError(f"field {field.field_key!r} has invalid selections")
        return value
    if not isinstance(value, bool):
        raise ValidationError(f"field {field.field_key!r} expects a boolean")
    return value


def _is_empty(ftype: str, value: object) -> bool:
    if ftype == "checkbox":
        return not value
    if ftype == "boolean":
        return value is False
    return isinstance(value, str) and not value.strip()


def validate_and_snapshot(fields: list[BookingFieldDTO], answers: list[AnswerDTO]) -> list[AnsweredFieldDTO]:
    by_key = {f.field_key: f for f in fields}
    given = {a.key: a.value for a in answers}
    for key in given:
        if key not in by_key:
            raise ValidationError(f"unknown booking field {key!r}")
    snapshot: list[AnsweredFieldDTO] = []
    for field in fields:
        if field.field_key not in given:
            if field.required:
                raise ValidationError(f"field {field.field_key!r} is required")
            continue
        value = _validate_one(field, given[field.field_key])
        if field.required and _is_empty(field.field_type, value):
            raise ValidationError(f"field {field.field_key!r} is required")
        snapshot.append(
            AnsweredFieldDTO(key=field.field_key, label=field.label, field_type=field.field_type, value=value)
        )
    return snapshot
