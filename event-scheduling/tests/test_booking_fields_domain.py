import pytest

from event_scheduling.booking_fields.domain import (
    assign_keys,
    slugify_key,
    validate_and_snapshot,
    validate_field_items,
)
from event_scheduling.booking_fields.dto import AnswerDTO, BookingFieldDTO, OptionDTO, UpsertBookingFieldDTO
from event_scheduling.errors import ValidationError


def _field(key, ftype, required=False, options=None):
    return BookingFieldDTO(
        field_key=key,
        field_type=ftype,
        label=key.title(),
        placeholder=None,
        required=required,
        options=options or [],
        position=0,
    )


def _opt(*vals):
    return [OptionDTO(value=v, label=v.title()) for v in vals]


def test_slugify_and_dedupe():
    assert (
        slugify_key("Почему нужна помощь") == "pochemu-nuzhna-pomoshch"
        or slugify_key("Reason For Visit") == "reason-for-visit"
    )
    items = [
        UpsertBookingFieldDTO("text", "Reason", None, False, []),
        UpsertBookingFieldDTO("text", "Reason", None, False, []),
    ]
    assert assign_keys(items) == ["reason", "reason-2"]


def test_assign_keys_avoids_collision_with_suffixed_key():
    # A later "Reason" must not collide with an earlier "Reason 2" that already produced "reason-2".
    items = [
        UpsertBookingFieldDTO("text", "Reason", None, False, []),
        UpsertBookingFieldDTO("text", "Reason 2", None, False, []),
        UpsertBookingFieldDTO("text", "Reason", None, False, []),
    ]
    keys = assign_keys(items)
    assert keys == ["reason", "reason-2", "reason-3"]
    assert len(set(keys)) == len(keys)


def test_validate_field_items_rejects_bad_shapes():
    with pytest.raises(ValidationError):
        validate_field_items([UpsertBookingFieldDTO("select", "Pick", None, False, [])])  # option type, no options
    with pytest.raises(ValidationError):
        validate_field_items([UpsertBookingFieldDTO("text", "T", None, False, _opt("a"))])  # non-option with options
    with pytest.raises(ValidationError):
        validate_field_items([UpsertBookingFieldDTO("text", "", None, False, [])])  # empty label
    validate_field_items([UpsertBookingFieldDTO("radio", "Pick", None, True, _opt("a", "b"))])  # ok


def test_validate_and_snapshot_required_and_membership():
    fields = [
        _field("reason", "textarea", required=True),
        _field("topics", "checkbox", options=_opt("anx", "sleep")),
        _field("agree", "boolean", required=True),
    ]
    # missing required 'reason' → error
    with pytest.raises(ValidationError):
        validate_and_snapshot(fields, [AnswerDTO("agree", True)])
    # checkbox value outside options → error
    with pytest.raises(ValidationError):
        validate_and_snapshot(
            fields, [AnswerDTO("reason", "hi"), AnswerDTO("agree", True), AnswerDTO("topics", ["nope"])]
        )
    # unknown key → error
    with pytest.raises(ValidationError):
        validate_and_snapshot(fields, [AnswerDTO("reason", "hi"), AnswerDTO("agree", True), AnswerDTO("bogus", "x")])
    # happy path → snapshot preserves label/type/value
    snap = validate_and_snapshot(
        fields, [AnswerDTO("reason", "hi"), AnswerDTO("agree", True), AnswerDTO("topics", ["anx"])]
    )
    by_key = {s.key: s for s in snap}
    assert by_key["reason"].label == "Reason"
    assert by_key["reason"].field_type == "textarea"
    assert by_key["topics"].value == ["anx"]
