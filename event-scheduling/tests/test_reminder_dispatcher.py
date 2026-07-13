"""remind_once orchestration — publish order, mark, skip-on-missing-email, retry-on-error."""

import datetime as dt
from uuid import UUID, uuid4

import pytest

from event_scheduling.publishing.dto import ParticipantInfo
from event_scheduling.reminders.dispatcher import remind_once
from event_scheduling.reminders.dto import DueBookingDTO


NOW = dt.datetime(2026, 10, 1, 8, 0, tzinfo=dt.UTC)


def _due(host_id: UUID, client_id: UUID) -> DueBookingDTO:
    return DueBookingDTO(
        id=uuid4(),
        event_type_id=uuid4(),
        host_user_id=host_id,
        client_user_id=client_id,
        start_time=NOW + dt.timedelta(minutes=60),
        end_time=NOW + dt.timedelta(minutes=120),
        attendee_time_zone="UTC",
        title="t",
    )


class _Clock:
    def now(self) -> dt.datetime:
        return NOW


class _Read:
    def __init__(self, due: list[DueBookingDTO]) -> None:
        self._due = due

    async def due_bookings(self, **_) -> list[DueBookingDTO]:
        return self._due


class _Write:
    def __init__(self) -> None:
        self.marked: list[UUID] = []

    async def mark_sent(self, booking_id: UUID, now: dt.datetime) -> None:
        self.marked.append(booking_id)


class _Users:
    def __init__(self, resolvable: bool = True) -> None:
        self._resolvable = resolvable

    async def by_ids(self, ids: list[UUID]) -> dict[UUID, ParticipantInfo]:
        if not self._resolvable:
            return {}
        return {u: ParticipantInfo(f"{u}@x.io", "UTC", "N", "en") for u in ids}


class _Receiver:
    def __init__(self, fail: bool = False) -> None:
        self.published: list[str] = []
        self._fail = fail

    async def publish(self, headers: dict, body: dict) -> int:
        if self._fail:
            raise RuntimeError("boom")
        self.published.append(headers["ce-type"])
        return 202


async def _run(read, write, users, receiver) -> int:
    return await remind_once(
        read,
        write,
        users,
        receiver,
        _Clock(),
        shift_from_minutes=55,
        shift_to_minutes=65,
        batch_size=100,
    )


@pytest.mark.asyncio
async def test_publishes_both_events_in_order_then_marks() -> None:
    h, c = uuid4(), uuid4()
    due = _due(h, c)
    write, receiver = _Write(), _Receiver()
    count = await _run(_Read([due]), write, _Users(), receiver)
    assert count == 1
    assert receiver.published == ["notification.send_requested", "booking.reminder_sent"]
    assert write.marked == [due.id]


@pytest.mark.asyncio
async def test_skips_and_does_not_mark_when_participant_unresolved() -> None:
    due = _due(uuid4(), uuid4())
    write, receiver = _Write(), _Receiver()
    count = await _run(_Read([due]), write, _Users(resolvable=False), receiver)
    assert count == 0
    assert receiver.published == []
    assert write.marked == []


@pytest.mark.asyncio
async def test_receiver_failure_does_not_mark() -> None:
    due = _due(uuid4(), uuid4())
    write = _Write()
    count = await _run(_Read([due]), write, _Users(), _Receiver(fail=True))
    assert count == 0
    assert write.marked == []


@pytest.mark.asyncio
async def test_empty_due_publishes_nothing() -> None:
    write, receiver = _Write(), _Receiver()
    count = await _run(_Read([]), write, _Users(), receiver)
    assert count == 0
    assert receiver.published == []
    assert write.marked == []
