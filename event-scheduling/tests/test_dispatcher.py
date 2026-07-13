import datetime as dt
import json
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.publishing.dispatcher import dispatch_once
from event_scheduling.publishing.dto import ParticipantInfo


@pytest.fixture(autouse=True)
async def _clean_outbox(_migrated: str, sessionmaker_fixture) -> None:
    """Truncate outbox before each test in this module.

    `outbox` isn't in the shared `_clean_db` fixture yet (Task 6 wires that up)
    and other suites (booking create/reschedule/cancel flows) write real rows
    to it — including at least one payload from an older schema-test fixture
    that lacks `host_user_id`/`client_user_id`. Without this, `dispatch_once`'s
    `FOR UPDATE SKIP LOCKED` batch would pick up unrelated, sometimes malformed,
    rows left behind by earlier tests in the same session.
    """
    async with sessionmaker_fixture() as s:
        await s.execute(text("DELETE FROM outbox"))
        await s.commit()


class _FixedClock:
    def __init__(self, now: dt.datetime) -> None:
        self._now = now

    def now(self) -> dt.datetime:
        return self._now


class _Users:
    async def by_ids(self, ids: list[UUID]) -> dict[UUID, ParticipantInfo]:
        return {u: ParticipantInfo(f"{u}@x.io", "Europe/Berlin") for u in ids}


class _Receiver:
    def __init__(self, status: int = 202) -> None:
        self.status = status
        self.calls = []

    async def publish(self, headers: dict, body: dict) -> int:
        self.calls.append((headers, body))
        return self.status


async def _insert_pending(s) -> UUID:
    host, client = uuid4(), uuid4()
    payload = {
        "host_user_id": str(host),
        "client_user_id": str(client),
        "start_time": "2026-10-01T07:00:00Z",
        "end_time": "2026-10-01T08:00:00Z",
        "attendee_time_zone": "Europe/Moscow",
    }
    ce = uuid4()
    await s.execute(
        text(
            "INSERT INTO outbox (event_ce_id, event_type, booking_uid, payload) "
            "VALUES (:ce, 'booking.created', :uid, CAST(:p AS jsonb))"
        ),
        {"ce": ce, "uid": str(uuid4()), "p": json.dumps(payload)},
    )
    return ce


@pytest.mark.asyncio
async def test_dispatch_marks_sent_on_202(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        await _insert_pending(s)
        await s.commit()
    rcv = _Receiver(202)
    async with sessionmaker_fixture() as s:
        n = await dispatch_once(
            SqlExecutor(s), _Users(), rcv, _FixedClock(dt.datetime(2026, 7, 13, tzinfo=dt.UTC)), 300, 50
        )
        await s.commit()
    assert n == 1
    assert len(rcv.calls) == 1
    async with sessionmaker_fixture() as s:
        st = (await s.execute(text("SELECT status FROM outbox"))).scalar_one()
    assert st == "sent"


@pytest.mark.asyncio
async def test_dispatch_retries_on_503(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        await _insert_pending(s)
        await s.commit()
    now = dt.datetime(2026, 7, 13, tzinfo=dt.UTC)
    async with sessionmaker_fixture() as s:
        await dispatch_once(SqlExecutor(s), _Users(), _Receiver(503), _FixedClock(now), 300, 50)
        await s.commit()
    async with sessionmaker_fixture() as s:
        row = (await s.execute(text("SELECT status, attempts, next_attempt_at FROM outbox"))).one()
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.next_attempt_at > now


@pytest.mark.asyncio
async def test_dispatch_fails_permanently_on_400(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        await _insert_pending(s)
        await s.commit()
    async with sessionmaker_fixture() as s:
        await dispatch_once(
            SqlExecutor(s), _Users(), _Receiver(400), _FixedClock(dt.datetime(2026, 7, 13, tzinfo=dt.UTC)), 300, 50
        )
        await s.commit()
    async with sessionmaker_fixture() as s:
        st = (await s.execute(text("SELECT status FROM outbox"))).scalar_one()
    assert st == "failed"


class _RaisingUsers:
    async def by_ids(self, ids: list[UUID]) -> dict[UUID, ParticipantInfo]:
        raise RuntimeError("users-service unreachable")


class _RaisingReceiver:
    async def publish(self, headers: dict, body: dict) -> int:
        raise httpx.ConnectError("connection refused")


class _MissingUsers:
    """Resolves only the first id it's asked for — simulates a not-yet-synced participant."""

    async def by_ids(self, ids: list[UUID]) -> dict[UUID, ParticipantInfo]:
        resolved = {u: ParticipantInfo(f"{u}@x.io", "Europe/Berlin") for u in ids}
        missing = next(iter(resolved))
        del resolved[missing]
        return resolved


async def _insert_malformed(s) -> UUID:
    """Outbox row whose payload is missing host_user_id — a poison pill."""
    payload = {
        "client_user_id": str(uuid4()),
        "start_time": "2026-10-01T07:00:00Z",
        "end_time": "2026-10-01T08:00:00Z",
        "attendee_time_zone": "Europe/Moscow",
    }
    ce = uuid4()
    await s.execute(
        text(
            "INSERT INTO outbox (event_ce_id, event_type, booking_uid, payload) "
            "VALUES (:ce, 'booking.created', :uid, CAST(:p AS jsonb))"
        ),
        {"ce": ce, "uid": str(uuid4()), "p": json.dumps(payload)},
    )
    return ce


@pytest.mark.asyncio
async def test_dispatch_retries_when_users_service_down(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        await _insert_pending(s)
        await s.commit()
    now = dt.datetime(2026, 7, 13, tzinfo=dt.UTC)
    async with sessionmaker_fixture() as s:
        await dispatch_once(SqlExecutor(s), _RaisingUsers(), _Receiver(202), _FixedClock(now), 300, 50)
        await s.commit()
    async with sessionmaker_fixture() as s:
        row = (await s.execute(text("SELECT status, attempts, next_attempt_at FROM outbox"))).one()
    assert row.status == "pending"
    assert row.attempts == 1
    assert row.next_attempt_at > now


@pytest.mark.asyncio
async def test_dispatch_retries_when_receiver_publish_raises(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        await _insert_pending(s)
        await s.commit()
    now = dt.datetime(2026, 7, 13, tzinfo=dt.UTC)
    async with sessionmaker_fixture() as s:
        await dispatch_once(SqlExecutor(s), _Users(), _RaisingReceiver(), _FixedClock(now), 300, 50)
        await s.commit()
    async with sessionmaker_fixture() as s:
        row = (await s.execute(text("SELECT status, attempts FROM outbox"))).one()
    assert row.status == "pending"
    assert row.attempts == 1


@pytest.mark.asyncio
async def test_dispatch_retries_when_participant_email_not_found(sessionmaker_fixture) -> None:
    """A not-yet-synced host/client must be retried, never dropped as failed."""
    async with sessionmaker_fixture() as s:
        await _insert_pending(s)
        await s.commit()
    now = dt.datetime(2026, 7, 13, tzinfo=dt.UTC)
    async with sessionmaker_fixture() as s:
        await dispatch_once(SqlExecutor(s), _MissingUsers(), _Receiver(202), _FixedClock(now), 300, 50)
        await s.commit()
    async with sessionmaker_fixture() as s:
        row = (await s.execute(text("SELECT status, attempts FROM outbox"))).one()
    assert row.status == "pending"
    assert row.attempts == 1


@pytest.mark.asyncio
async def test_dispatch_marks_malformed_payload_failed_without_wedging_batch(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        await _insert_malformed(s)
        await _insert_pending(s)
        await s.commit()
    rcv = _Receiver(202)
    async with sessionmaker_fixture() as s:
        n = await dispatch_once(
            SqlExecutor(s), _Users(), rcv, _FixedClock(dt.datetime(2026, 7, 13, tzinfo=dt.UTC)), 300, 50
        )
        await s.commit()
    assert n == 2
    assert len(rcv.calls) == 1
    async with sessionmaker_fixture() as s:
        rows = (await s.execute(text("SELECT status, last_error FROM outbox ORDER BY status"))).all()
    statuses = {r.status for r in rows}
    assert statuses == {"failed", "sent"}
    failed_row = next(r for r in rows if r.status == "failed")
    assert "malformed" in failed_row.last_error


@pytest.mark.asyncio
async def test_dispatch_uses_stable_ce_id(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        ce = await _insert_pending(s)
        await s.commit()
    rcv = _Receiver(202)
    async with sessionmaker_fixture() as s:
        await dispatch_once(
            SqlExecutor(s), _Users(), rcv, _FixedClock(dt.datetime(2026, 7, 13, tzinfo=dt.UTC)), 300, 50
        )
        await s.commit()
    assert rcv.calls[0][0]["ce-id"] == str(ce)
