import datetime as dt
from uuid import UUID, uuid4

import pytest

from event_booker.dto import BookingResult, EventTypeDTO
from event_booker.errors import SlotUnavailableError
from event_booker.services.guest_booking import GuestBookingService


ET_ID = uuid4()
START = dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC)
END = dt.datetime(2026, 10, 1, 9, 30, tzinfo=dt.UTC)


class _Scheduling:
    def __init__(self, *, conflict: bool = False) -> None:
        self.created_with: tuple | None = None
        self._conflict = conflict

    async def get_event_type(self, event_type_id):
        return EventTypeDTO(id=event_type_id, slug="intro", title="Intro call", duration_minutes=30)

    async def create_booking(self, event_type_id, client_user_id, start_time, attendee_time_zone):
        if self._conflict:
            raise SlotUnavailableError("slot no longer available")
        self.created_with = (event_type_id, client_user_id, start_time, attendee_time_zone)
        return BookingResult(id=uuid4(), start_time=START, end_time=END, status="confirmed")

    async def list_event_types(self): ...
    async def get_slots(self, *a, **k): ...


class _Users:
    def __init__(self, existing: UUID | None) -> None:
        self._existing = existing
        self.created = False

    async def get_client_by_email(self, email):
        return self._existing

    async def create_client(self, email, name, time_zone):
        self.created = True
        return uuid4()


@pytest.mark.asyncio
async def test_books_for_existing_client_without_create() -> None:
    existing = uuid4()
    sched, users = _Scheduling(), _Users(existing=existing)
    conf = await GuestBookingService(sched, users).book(ET_ID, "A", "a@b.io", START, "Europe/Berlin")
    assert users.created is False
    assert sched.created_with[1] == existing
    assert conf.event_type_title == "Intro call"
    assert conf.time_zone == "Europe/Berlin"
    assert conf.status == "confirmed"


@pytest.mark.asyncio
async def test_creates_client_when_absent_then_books() -> None:
    sched, users = _Scheduling(), _Users(existing=None)
    conf = await GuestBookingService(sched, users).book(ET_ID, "A", "a@b.io", START, "UTC")
    assert users.created is True
    assert conf.booking_id is not None


@pytest.mark.asyncio
async def test_slot_conflict_propagates() -> None:
    sched, users = _Scheduling(conflict=True), _Users(existing=uuid4())
    with pytest.raises(SlotUnavailableError):
        await GuestBookingService(sched, users).book(ET_ID, "A", "a@b.io", START, "UTC")
