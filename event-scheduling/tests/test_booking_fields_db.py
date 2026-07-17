"""BookingFieldAdapter + BookingFieldController against a real Postgres (slice: booking fields phase 1, Task 3).

Uses the suite's `sessionmaker_fixture` (see `test_booking_write_adapter.py` for the same pattern):
build a session, wrap it in `SqlExecutor`, seed a minimal `event_type` row directly via SQL.
"""

from uuid import uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.booking_fields.adapter import BookingFieldAdapter
from event_scheduling.booking_fields.controller import BookingFieldController
from event_scheduling.booking_fields.dto import OptionDTO, UpsertBookingFieldDTO
from event_scheduling.errors import NotFoundError


def _up(ftype, label, required=False, options=None):
    return UpsertBookingFieldDTO(
        field_type=ftype, label=label, placeholder=None, required=required, options=options or []
    )


@pytest.mark.asyncio
async def test_replace_all_assigns_keys_and_positions(sessionmaker_fixture) -> None:
    et_id = uuid4()
    async with sessionmaker_fixture() as s:
        await s.execute(
            text("INSERT INTO event_type (id, slug, title, duration_minutes) VALUES (:id, :slug, 't', 60)"),
            {"id": et_id, "slug": f"et-{et_id}"},
        )
        await s.commit()

    async with sessionmaker_fixture() as s:
        ctrl = BookingFieldController(BookingFieldAdapter(SqlExecutor(s)))
        stored = await ctrl.replace(
            et_id,
            [
                _up("textarea", "Reason", required=True),
                _up("checkbox", "Topics", options=[OptionDTO("anx", "Anxiety"), OptionDTO("sleep", "Sleep")]),
            ],
        )
        assert [f.field_key for f in stored] == ["reason", "topics"]
        assert [f.position for f in stored] == [0, 1]

        # replace again with a shorter list -> old rows gone
        stored2 = await ctrl.replace(et_id, [_up("text", "Name again")])
        assert [f.field_key for f in stored2] == ["name-again"]
        assert await ctrl.list_for(et_id) == stored2
        await s.commit()


@pytest.mark.asyncio
async def test_replace_unknown_event_type_raises_not_found(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        ctrl = BookingFieldController(BookingFieldAdapter(SqlExecutor(s)))
        with pytest.raises(NotFoundError):
            await ctrl.replace(uuid4(), [_up("text", "X")])
