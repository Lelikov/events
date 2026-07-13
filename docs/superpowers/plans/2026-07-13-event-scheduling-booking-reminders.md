# event-scheduling booking reminders (срез 4a.3) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** event-scheduling sends one ~1h-before reminder for its own confirmed bookings, matching the cal.com reminder contract, entirely in-service.

**Architecture:** A second background poller in event-scheduling (mirrors the existing outbox `run_dispatcher_loop`): each tick selects confirmed bookings starting in `[now+55m, now+65m]` that are not yet reminded (`reminder_sent_at IS NULL`), resolves participants via the existing `users_client`, POSTs `notification.send_requested` + `booking.reminder_sent` CloudEvents to event-receiver `/event/booking` via the existing `receiver_client`, then stamps `reminder_sent_at`. Reschedule re-arms by nulling the marker. Additive — the cal.com reminder poller in event-booking is untouched.

**Tech Stack:** Python 3.14, FastAPI, Dishka DI, raw SQL via `SqlExecutor` (`:param` binds), alembic, frozen-dataclass DTOs, httpx, structlog, pytest (Docker Postgres for DB tests).

## Global Constraints

- Work ONLY in `event-scheduling` (tracked by the ROOT repo `/Users/alexandrlelikov/PycharmProjects/events`). Commit in the root repo on branch `feat/booking-reminders` (create off `main` before Task 1).
- Code style: **NO `elif`**; **avoid `else`** (early returns / guard clauses / mapping dicts); Ruff line length 120. Frozen dataclasses as DTOs. Pydantic only in `schemas/`.
- Additive only: do NOT modify the cal.com reminder path (`event-booking/`), event-receiver, event-notifier, or event-saver. This slice is one service.
- `ce-source` for both emitted CloudEvents MUST be `"booking"` (event-receiver `ROUTING_RULES`: `booking.reminder_sent` requires source `booking`; `notification.send_requested` accepts any source — a single `"booking"` value satisfies both).
- Producers POST FLAT domain bodies to `/event/booking` (event-receiver builds the `{original, normalized}` envelope). Do NOT pre-wrap.
- DB tests need Docker Postgres: `docker run -d --rm --name sched-testpg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=event_scheduling -p 5599:5432 postgres:16`, then run pytest with `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling'`. Use the `+asyncpg` driver (alembic env uses the async engine). Do NOT pass `SCHEDULING_API_KEY` on the pytest command (conftest sets its own).
- Reminder command shape MUST match event-booking byte-for-byte so event-notifier can't tell the source apart: recipient dict = `{"email", "role"}` + `"locale"` only when truthy (`event-booking/event_booking/dtos.py::notification_recipient`); trigger `"BOOKING_REMINDER"`; command body `{booking_uid, booking_id, trigger_event, recipients, template_data}`; `booking.reminder_sent` body `{booking_uid, email}`.

---

## File Structure

New module `event_scheduling/reminders/` (peer of `publishing/`):
- `reminders/__init__.py` — empty package marker.
- `reminders/dto.py` — `DueBookingDTO` (frozen).
- `reminders/interfaces.py` — `IReminderReadAdapter`, `IReminderWriteAdapter` (Protocols). Reuses `IUsersClient`, `IReceiverClient` from `publishing/interfaces.py`.
- `reminders/read_adapter.py` — `ReminderReadAdapter(sql).due_bookings(...)`.
- `reminders/write_adapter.py` — `ReminderWriteAdapter(sql).mark_sent(...)`.
- `reminders/payload.py` — `build_reminder_command(...)`, `build_reminder_sent(...)`, deterministic ce-id.
- `reminders/dispatcher.py` — `remind_once(...)`, `run_reminder_loop(...)`.

Modified:
- `alembic/versions/0004_booking_reminder_sent.py` — new migration (column + partial index).
- `event_scheduling/booking/write_adapter.py` — reschedule UPDATE also sets `reminder_sent_at=NULL`.
- `event_scheduling/config.py` — reminder settings.
- `event_scheduling/ioc.py` — reminder-adapter providers.
- `event_scheduling/main.py` — second background task.
- Docs: `event-scheduling/CLAUDE.md`, `event-scheduling/docs/DATA_MODEL.md`, root `docs/architecture/MESSAGE_CONTRACTS.md`.

Tests (new): `tests/test_reminder_schema.py`, `tests/test_reminder_read_adapter.py`, `tests/test_reminder_write_adapter.py`, `tests/test_reminder_payload.py`, `tests/test_reminder_dispatcher.py`, `tests/test_reminder_config_ioc.py`.

---

## Task 1: Migration 0004 — `reminder_sent_at` column + partial index + reschedule reset

**Files:**
- Create: `alembic/versions/0004_booking_reminder_sent.py`
- Modify: `event_scheduling/booking/write_adapter.py` (reschedule UPDATE ~line 67)
- Test: `tests/test_reminder_schema.py`

**Interfaces:**
- Consumes: existing `booking` table (0002), `BookingWriteAdapter.reschedule` (`write_adapter.py`).
- Produces: `booking.reminder_sent_at TIMESTAMPTZ NULL`; partial index `ix_booking_reminder`; reschedule now nulls `reminder_sent_at`.

- [ ] **Step 1: Write the failing test** `tests/test_reminder_schema.py`:

```python
"""Migration 0004: booking.reminder_sent_at column + partial index; reschedule re-arms it."""

import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.booking.write_adapter import BookingWriteAdapter
from event_scheduling.dto.schedule import ActorDTO

ACTOR = ActorDTO(source="api", user_id=None)


@pytest.mark.asyncio
async def test_reminder_column_and_partial_index_exist(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        col = await s.execute(
            text(
                "SELECT is_nullable, data_type FROM information_schema.columns "
                "WHERE table_name='booking' AND column_name='reminder_sent_at'"
            )
        )
        row = col.mappings().one()
        assert row["is_nullable"] == "YES"
        assert row["data_type"] == "timestamp with time zone"

        idx = await s.execute(
            text("SELECT indexdef FROM pg_indexes WHERE indexname='ix_booking_reminder'")
        )
        indexdef = idx.scalar_one()
        assert "reminder_sent_at IS NULL" in indexdef
        assert "status" in indexdef and "confirmed" in indexdef


@pytest.mark.asyncio
async def test_reschedule_clears_reminder_marker(sessionmaker_fixture) -> None:
    et_id, host, client = uuid4(), uuid4(), uuid4()
    async with sessionmaker_fixture() as s:
        await s.execute(
            text("INSERT INTO event_type (id, slug, title, duration_minutes) VALUES (:id, :slug, 't', 60)"),
            {"id": et_id, "slug": f"et-{et_id}"},
        )
        booking_id = (
            await s.execute(
                text(
                    "INSERT INTO booking (event_type_id, host_user_id, client_user_id, start_time, end_time, "
                    "attendee_time_zone, reminder_sent_at) VALUES "
                    "(:et,:h,:c, '2026-10-01T09:00+00','2026-10-01T10:00+00','Europe/Berlin', now()) RETURNING id"
                ),
                {"et": et_id, "h": host, "c": client},
            )
        ).scalar_one()
        await s.commit()

    async with sessionmaker_fixture() as s:
        adapter = BookingWriteAdapter(SqlExecutor(s))
        await adapter.reschedule(booking_id, dt.datetime(2026, 10, 2, 9, tzinfo=dt.UTC), ACTOR)
        await s.commit()

    async with sessionmaker_fixture() as s:
        marker = (
            await s.execute(text("SELECT reminder_sent_at FROM booking WHERE id=:id"), {"id": booking_id})
        ).scalar_one()
        assert marker is None
```

- [ ] **Step 2: Run — verify it FAILS**

Run: `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest tests/test_reminder_schema.py -v`
Expected: FAIL — column `reminder_sent_at` does not exist.

- [ ] **Step 3: Write the migration** `alembic/versions/0004_booking_reminder_sent.py`:

```python
"""booking.reminder_sent_at + partial index (slice 4a.3)

Revision ID: 0004
Revises: 0003
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("booking", sa.Column("reminder_sent_at", sa.DateTime(timezone=True), nullable=True))
    op.execute(
        "CREATE INDEX ix_booking_reminder ON booking (start_time) "
        "WHERE status = 'confirmed' AND reminder_sent_at IS NULL"
    )


def downgrade() -> None:
    op.drop_index("ix_booking_reminder", table_name="booking")
    op.drop_column("booking", "reminder_sent_at")
```

- [ ] **Step 4: Modify the reschedule UPDATE** in `event_scheduling/booking/write_adapter.py` (~line 67). Add `reminder_sent_at=NULL` so a rescheduled booking re-arms:

```python
                row = await self._sql.fetch_one(
                    f"UPDATE booking SET start_time=:s, end_time=:e, reminder_sent_at=NULL, "  # noqa: S608
                    f"updated_at=now() WHERE id=:id RETURNING {_COLS}",
                    {"id": booking_id, "s": start, "e": end},
                )
```
> Keep the surrounding `begin_nested()` / `IntegrityError → ConflictError` handling exactly as-is. Only the SQL string changes.

- [ ] **Step 5: Run — verify it PASSES**

Run: `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest tests/test_reminder_schema.py -v`
Expected: PASS (2 tests). If the test DB was already migrated to head from an earlier run, drop & recreate it (`docker rm -f sched-testpg` then re-run the docker run) so alembic applies 0004 fresh.

- [ ] **Step 6: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/alembic/versions/0004_booking_reminder_sent.py \
        event-scheduling/event_scheduling/booking/write_adapter.py \
        event-scheduling/tests/test_reminder_schema.py
git commit -m "feat(reminders): booking.reminder_sent_at column + partial index; reschedule re-arms (slice 4a.3)"
```

---

## Task 2: `reminders/` DTO + read adapter (`due_bookings`)

**Files:**
- Create: `event_scheduling/reminders/__init__.py` (empty), `event_scheduling/reminders/dto.py`, `event_scheduling/reminders/interfaces.py`, `event_scheduling/reminders/read_adapter.py`
- Test: `tests/test_reminder_read_adapter.py`

**Interfaces:**
- Consumes: `ISqlExecutor` (`fetch_all(query, values) -> list[RowMapping]`), `booking`/`event_type` tables, `reminder_sent_at` (Task 1).
- Produces:
  - `DueBookingDTO(id: UUID, event_type_id: UUID, host_user_id: UUID, client_user_id: UUID, start_time: datetime, end_time: datetime, attendee_time_zone: str, title: str)` (frozen).
  - `IReminderReadAdapter.due_bookings(self, *, now: datetime, shift_from_minutes: int, shift_to_minutes: int, limit: int) -> list[DueBookingDTO]`.
  - `ReminderReadAdapter(sql: ISqlExecutor)` implementing it.

- [ ] **Step 1: Write the failing test** `tests/test_reminder_read_adapter.py`:

```python
"""ReminderReadAdapter.due_bookings: confirmed ∧ window ∧ not-yet-reminded."""

import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.reminders.read_adapter import ReminderReadAdapter

NOW = dt.datetime(2026, 10, 1, 8, 0, tzinfo=dt.UTC)  # reminders fire for starts in [NOW+55m, NOW+65m]


async def _mk_event_type(s) -> "uuid":  # noqa: ANN001
    et = uuid4()
    await s.execute(
        text("INSERT INTO event_type (id, slug, title, duration_minutes) VALUES (:id,:slug,'Intro call',60)"),
        {"id": et, "slug": f"et-{et}"},
    )
    return et


async def _mk_booking(s, et, *, start, status="confirmed", reminded=False) -> "uuid":  # noqa: ANN001
    bid = uuid4()
    await s.execute(
        text(
            "INSERT INTO booking (id, event_type_id, host_user_id, client_user_id, start_time, end_time, "
            "status, attendee_time_zone, reminder_sent_at) VALUES "
            "(:id,:et,:h,:c,:st,:en,:status,'Europe/Berlin',:rem)"
        ),
        {
            "id": bid, "et": et, "h": uuid4(), "c": uuid4(), "st": start,
            "en": start + dt.timedelta(hours=1), "status": status,
            "rem": (start - dt.timedelta(minutes=30)) if reminded else None,
        },
    )
    return bid


@pytest.mark.asyncio
async def test_due_bookings_selects_only_confirmed_in_window_not_reminded(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et = await _mk_event_type(s)
        due = await _mk_booking(s, et, start=NOW + dt.timedelta(minutes=60))       # in window
        await _mk_booking(s, et, start=NOW + dt.timedelta(minutes=30))             # too soon
        await _mk_booking(s, et, start=NOW + dt.timedelta(minutes=120))            # too far
        await _mk_booking(s, et, start=NOW + dt.timedelta(minutes=60), status="cancelled")
        await _mk_booking(s, et, start=NOW + dt.timedelta(minutes=60), reminded=True)
        await s.commit()

    async with sessionmaker_fixture() as s:
        rows = await ReminderReadAdapter(SqlExecutor(s)).due_bookings(
            now=NOW, shift_from_minutes=55, shift_to_minutes=65, limit=100
        )

    assert [r.id for r in rows] == [due]
    assert rows[0].title == "Intro call"
    assert rows[0].attendee_time_zone == "Europe/Berlin"


@pytest.mark.asyncio
async def test_due_bookings_respects_limit(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et = await _mk_event_type(s)
        for _ in range(3):
            await _mk_booking(s, et, start=NOW + dt.timedelta(minutes=60))
        await s.commit()

    async with sessionmaker_fixture() as s:
        rows = await ReminderReadAdapter(SqlExecutor(s)).due_bookings(
            now=NOW, shift_from_minutes=55, shift_to_minutes=65, limit=2
        )
    assert len(rows) == 2
```

- [ ] **Step 2: Run — verify it FAILS**

Run: `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest tests/test_reminder_read_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: event_scheduling.reminders.read_adapter`.

- [ ] **Step 3: Create the module files.**

`event_scheduling/reminders/__init__.py`: empty file.

`event_scheduling/reminders/dto.py`:

```python
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class DueBookingDTO:
    id: UUID
    event_type_id: UUID
    host_user_id: UUID
    client_user_id: UUID
    start_time: datetime
    end_time: datetime
    attendee_time_zone: str
    title: str
```

`event_scheduling/reminders/interfaces.py`:

```python
from datetime import datetime
from typing import Protocol
from uuid import UUID

from event_scheduling.reminders.dto import DueBookingDTO


class IReminderReadAdapter(Protocol):
    async def due_bookings(
        self, *, now: datetime, shift_from_minutes: int, shift_to_minutes: int, limit: int
    ) -> list[DueBookingDTO]: ...


class IReminderWriteAdapter(Protocol):
    async def mark_sent(self, booking_id: UUID, now: datetime) -> None: ...
```

`event_scheduling/reminders/read_adapter.py`:

```python
from datetime import datetime, timedelta

from event_scheduling.interfaces.sql import ISqlExecutor
from event_scheduling.reminders.dto import DueBookingDTO

_DUE_SQL = """
    SELECT b.id, b.event_type_id, b.host_user_id, b.client_user_id,
           b.start_time, b.end_time, b.attendee_time_zone, et.title AS title
    FROM booking b
    JOIN event_type et ON et.id = b.event_type_id
    WHERE b.status = 'confirmed'
      AND b.reminder_sent_at IS NULL
      AND b.start_time >= :start_from
      AND b.start_time <= :start_to
    ORDER BY b.start_time ASC
    LIMIT :limit
"""


class ReminderReadAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def due_bookings(
        self, *, now: datetime, shift_from_minutes: int, shift_to_minutes: int, limit: int
    ) -> list[DueBookingDTO]:
        rows = await self._sql.fetch_all(
            _DUE_SQL,
            {
                "start_from": now + timedelta(minutes=shift_from_minutes),
                "start_to": now + timedelta(minutes=shift_to_minutes),
                "limit": limit,
            },
        )
        return [
            DueBookingDTO(
                id=r["id"],
                event_type_id=r["event_type_id"],
                host_user_id=r["host_user_id"],
                client_user_id=r["client_user_id"],
                start_time=r["start_time"],
                end_time=r["end_time"],
                attendee_time_zone=r["attendee_time_zone"],
                title=r["title"],
            )
            for r in rows
        ]
```

- [ ] **Step 4: Run — verify it PASSES**

Run: `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest tests/test_reminder_read_adapter.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/reminders/ event-scheduling/tests/test_reminder_read_adapter.py
git commit -m "feat(reminders): DueBookingDTO + ReminderReadAdapter.due_bookings (slice 4a.3)"
```

---

## Task 3: reminders write adapter (`mark_sent`)

**Files:**
- Create: `event_scheduling/reminders/write_adapter.py`
- Test: `tests/test_reminder_write_adapter.py`

**Interfaces:**
- Consumes: `ISqlExecutor.execute(query, values) -> None`, `IReminderWriteAdapter` (Task 2).
- Produces: `ReminderWriteAdapter(sql: ISqlExecutor)` with `mark_sent(booking_id: UUID, now: datetime) -> None` — UPDATE guarded by `reminder_sent_at IS NULL`.

- [ ] **Step 1: Write the failing test** `tests/test_reminder_write_adapter.py`:

```python
"""ReminderWriteAdapter.mark_sent stamps reminder_sent_at, guarded against overwrite."""

import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.reminders.write_adapter import ReminderWriteAdapter

T1 = dt.datetime(2026, 10, 1, 8, 5, tzinfo=dt.UTC)
T2 = dt.datetime(2026, 10, 1, 8, 9, tzinfo=dt.UTC)


async def _mk_booking(s) -> "uuid":  # noqa: ANN001
    et, bid = uuid4(), uuid4()
    await s.execute(
        text("INSERT INTO event_type (id, slug, title, duration_minutes) VALUES (:id,:slug,'t',60)"),
        {"id": et, "slug": f"et-{et}"},
    )
    await s.execute(
        text(
            "INSERT INTO booking (id, event_type_id, host_user_id, client_user_id, start_time, end_time, "
            "attendee_time_zone) VALUES (:id,:et,:h,:c,'2026-10-01T09:00+00','2026-10-01T10:00+00','UTC')"
        ),
        {"id": bid, "et": et, "h": uuid4(), "c": uuid4()},
    )
    return bid


@pytest.mark.asyncio
async def test_mark_sent_stamps_and_is_idempotent(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        bid = await _mk_booking(s)
        await s.commit()

    async with sessionmaker_fixture() as s:
        await ReminderWriteAdapter(SqlExecutor(s)).mark_sent(bid, T1)
        await s.commit()

    async with sessionmaker_fixture() as s:
        first = (await s.execute(text("SELECT reminder_sent_at FROM booking WHERE id=:id"), {"id": bid})).scalar_one()
        assert first is not None

    # Second call must NOT overwrite (guard: reminder_sent_at IS NULL)
    async with sessionmaker_fixture() as s:
        await ReminderWriteAdapter(SqlExecutor(s)).mark_sent(bid, T2)
        await s.commit()

    async with sessionmaker_fixture() as s:
        second = (await s.execute(text("SELECT reminder_sent_at FROM booking WHERE id=:id"), {"id": bid})).scalar_one()
    assert second == first  # unchanged — guard held
```

- [ ] **Step 2: Run — verify it FAILS**

Run: `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest tests/test_reminder_write_adapter.py -v`
Expected: FAIL — `ModuleNotFoundError: event_scheduling.reminders.write_adapter`.

- [ ] **Step 3: Create** `event_scheduling/reminders/write_adapter.py`:

```python
from datetime import datetime
from uuid import UUID

from event_scheduling.interfaces.sql import ISqlExecutor

_MARK_SENT_SQL = "UPDATE booking SET reminder_sent_at=:now WHERE id=:id AND reminder_sent_at IS NULL"


class ReminderWriteAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def mark_sent(self, booking_id: UUID, now: datetime) -> None:
        await self._sql.execute(_MARK_SENT_SQL, {"id": booking_id, "now": now})
```

- [ ] **Step 4: Run — verify it PASSES**

Run: `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest tests/test_reminder_write_adapter.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/reminders/write_adapter.py \
        event-scheduling/tests/test_reminder_write_adapter.py
git commit -m "feat(reminders): ReminderWriteAdapter.mark_sent with NULL guard (slice 4a.3)"
```

---

## Task 4: reminder payload builders (`build_reminder_command`, `build_reminder_sent`)

**Files:**
- Create: `event_scheduling/reminders/payload.py`
- Test: `tests/test_reminder_payload.py`

**Interfaces:**
- Consumes: `DueBookingDTO` (Task 2), `ParticipantInfo` (`event_scheduling/publishing/dto.py` — fields `email`, `time_zone`, `name`, `locale`).
- Produces:
  - `build_reminder_command(due: DueBookingDTO, host: ParticipantInfo, client: ParticipantInfo, now: datetime) -> tuple[dict[str, str], dict]`
  - `build_reminder_sent(due: DueBookingDTO, client: ParticipantInfo, now: datetime) -> tuple[dict[str, str], dict]`
  - Deterministic ce-id via `uuid5` over a stable per-booking key (`reminder:{id}` / `reminder_sent:{id}`).

- [ ] **Step 1: Write the failing test** `tests/test_reminder_payload.py`:

```python
"""Reminder CloudEvent builders — shape parity with event-booking + deterministic ce-id."""

import datetime as dt
from uuid import uuid4

from event_scheduling.publishing.dto import ParticipantInfo
from event_scheduling.reminders.dto import DueBookingDTO
from event_scheduling.reminders.payload import build_reminder_command, build_reminder_sent

NOW = dt.datetime(2026, 10, 1, 8, 0, tzinfo=dt.UTC)
BID = uuid4()
DUE = DueBookingDTO(
    id=BID, event_type_id=uuid4(), host_user_id=uuid4(), client_user_id=uuid4(),
    start_time=dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.UTC),
    end_time=dt.datetime(2026, 10, 1, 10, 0, tzinfo=dt.UTC),
    attendee_time_zone="America/New_York", title="Intro call",
)
HOST = ParticipantInfo("host@x.io", "Europe/Berlin", "Hostie", "de")
CLIENT = ParticipantInfo("client@x.io", "America/New_York", "Clint", None)


def test_command_headers_and_body_match_contract() -> None:
    headers, body = build_reminder_command(DUE, HOST, CLIENT, NOW)
    assert headers["ce-type"] == "notification.send_requested"
    assert headers["ce-source"] == "booking"
    assert headers["ce-specversion"] == "1.0"
    assert body["booking_uid"] == str(BID)
    assert body["booking_id"] == str(BID)
    assert body["trigger_event"] == "BOOKING_REMINDER"
    assert body["recipients"] == [
        {"email": "host@x.io", "role": "organizer", "locale": "de"},
        {"email": "client@x.io", "role": "client"},  # locale omitted when falsy
    ]
    td = body["template_data"]
    assert td["booking_uid"] == str(BID)
    assert td["title"] == "Intro call"
    assert td["start_time"] == "2026-10-01T09:00:00+00:00"
    assert td["end_time"] == "2026-10-01T10:00:00+00:00"
    assert td["organizer_name"] == "Hostie"
    assert td["organizer_email"] == "host@x.io"
    assert td["client_name"] == "Clint"
    assert td["client_email"] == "client@x.io"


def test_reminder_sent_body() -> None:
    headers, body = build_reminder_sent(DUE, CLIENT, NOW)
    assert headers["ce-type"] == "booking.reminder_sent"
    assert headers["ce-source"] == "booking"
    assert body == {"booking_uid": str(BID), "email": "client@x.io"}


def test_ce_id_is_deterministic_per_booking_and_type() -> None:
    h1, _ = build_reminder_command(DUE, HOST, CLIENT, NOW)
    h2, _ = build_reminder_command(DUE, HOST, CLIENT, dt.datetime(2026, 10, 1, 8, 1, tzinfo=dt.UTC))
    assert h1["ce-id"] == h2["ce-id"]  # stable across ticks
    hs, _ = build_reminder_sent(DUE, CLIENT, NOW)
    assert hs["ce-id"] != h1["ce-id"]  # different event → different id
```

- [ ] **Step 2: Run — verify it FAILS**

Run: `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest tests/test_reminder_payload.py -v`
Expected: FAIL — `ModuleNotFoundError: event_scheduling.reminders.payload`. (These are pure unit tests but the shared conftest still requires the DSN to import.)

- [ ] **Step 3: Create** `event_scheduling/reminders/payload.py`:

```python
import uuid
from datetime import datetime

from event_scheduling.publishing.dto import ParticipantInfo
from event_scheduling.reminders.dto import DueBookingDTO

# Fixed namespace so ce-ids are reproducible across restarts (idempotent redelivery).
_REMINDER_NS = uuid.UUID("a3f1c2d4-5e6b-4a7c-8d9e-0f1a2b3c4d5e")


def _ce_id(key: str) -> str:
    return str(uuid.uuid5(_REMINDER_NS, key))


def _headers(ce_type: str, ce_id: str, now: datetime) -> dict[str, str]:
    return {
        "ce-specversion": "1.0",
        "ce-id": ce_id,
        "ce-source": "booking",
        "ce-type": ce_type,
        "ce-time": now.isoformat(),
    }


def _recipient(email: str, role: str, locale: str | None) -> dict[str, str]:
    recipient = {"email": email, "role": role}
    if locale:
        recipient["locale"] = locale
    return recipient


def build_reminder_command(
    due: DueBookingDTO, host: ParticipantInfo, client: ParticipantInfo, now: datetime
) -> tuple[dict[str, str], dict]:
    uid = str(due.id)
    body = {
        "booking_uid": uid,
        "booking_id": uid,
        "trigger_event": "BOOKING_REMINDER",
        "recipients": [
            _recipient(host.email, "organizer", host.locale),
            _recipient(client.email, "client", client.locale),
        ],
        "template_data": {
            "booking_uid": uid,
            "start_time": due.start_time.isoformat(),
            "end_time": due.end_time.isoformat(),
            "title": due.title,
            "organizer_name": host.name,
            "organizer_email": host.email,
            "client_name": client.name,
            "client_email": client.email,
        },
    }
    return _headers("notification.send_requested", _ce_id(f"reminder:{uid}"), now), body


def build_reminder_sent(due: DueBookingDTO, client: ParticipantInfo, now: datetime) -> tuple[dict[str, str], dict]:
    uid = str(due.id)
    body = {"booking_uid": uid, "email": client.email}
    return _headers("booking.reminder_sent", _ce_id(f"reminder_sent:{uid}"), now), body
```

- [ ] **Step 4: Run — verify it PASSES**

Run: `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest tests/test_reminder_payload.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/reminders/payload.py \
        event-scheduling/tests/test_reminder_payload.py
git commit -m "feat(reminders): reminder CloudEvent builders + deterministic ce-id (slice 4a.3)"
```

---

## Task 5: reminder dispatcher (`remind_once` + `run_reminder_loop`)

**Files:**
- Create: `event_scheduling/reminders/dispatcher.py`
- Test: `tests/test_reminder_dispatcher.py`

**Interfaces:**
- Consumes: `IReminderReadAdapter`, `IReminderWriteAdapter` (Task 2/3), `IUsersClient.by_ids(list[UUID]) -> dict[UUID, ParticipantInfo]`, `IReceiverClient.publish(dict, dict) -> int` (`publishing/interfaces.py`), `Clock.now() -> datetime` (`slots/interfaces.py`), `build_reminder_command`/`build_reminder_sent` (Task 4), `ReminderReadAdapter`/`ReminderWriteAdapter` classes, `SqlExecutor as _SqlExec` (`adapters/sql.py`).
- Produces:
  - `remind_once(read: IReminderReadAdapter, write: IReminderWriteAdapter, users: IUsersClient, receiver: IReceiverClient, clock: Clock, *, shift_from_minutes: int, shift_to_minutes: int, batch_size: int) -> int` (returns number of bookings reminded).
  - `run_reminder_loop(sessionmaker, users, receiver, clock, *, interval_s: float, shift_from_minutes: int, shift_to_minutes: int, batch_size: int, stop: asyncio.Event) -> None`.

- [ ] **Step 1: Write the failing test** `tests/test_reminder_dispatcher.py`:

```python
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
        id=uuid4(), event_type_id=uuid4(), host_user_id=host_id, client_user_id=client_id,
        start_time=NOW + dt.timedelta(minutes=60), end_time=NOW + dt.timedelta(minutes=120),
        attendee_time_zone="UTC", title="t",
    )


class _Clock:
    def now(self) -> dt.datetime:
        return NOW


class _Read:
    def __init__(self, due: list[DueBookingDTO]) -> None:
        self._due = due

    async def due_bookings(self, **_) -> list[DueBookingDTO]:  # noqa: ANN003
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


async def _run(read, write, users, receiver) -> int:  # noqa: ANN001
    return await remind_once(
        read, write, users, receiver, _Clock(),
        shift_from_minutes=55, shift_to_minutes=65, batch_size=100,
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
```

- [ ] **Step 2: Run — verify it FAILS**

Run: `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest tests/test_reminder_dispatcher.py -v`
Expected: FAIL — `ModuleNotFoundError: event_scheduling.reminders.dispatcher`.

- [ ] **Step 3: Create** `event_scheduling/reminders/dispatcher.py`:

```python
import asyncio
import contextlib
from typing import TYPE_CHECKING

import structlog

from event_scheduling.adapters.sql import SqlExecutor as _SqlExec
from event_scheduling.reminders.dto import DueBookingDTO
from event_scheduling.reminders.payload import build_reminder_command, build_reminder_sent
from event_scheduling.reminders.read_adapter import ReminderReadAdapter
from event_scheduling.reminders.write_adapter import ReminderWriteAdapter

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from event_scheduling.publishing.interfaces import IReceiverClient, IUsersClient
    from event_scheduling.reminders.interfaces import IReminderReadAdapter, IReminderWriteAdapter
    from event_scheduling.slots.interfaces import Clock

logger = structlog.get_logger(__name__)


async def _remind_one(
    due: DueBookingDTO,
    write: "IReminderWriteAdapter",
    users: "IUsersClient",
    receiver: "IReceiverClient",
    clock: "Clock",
) -> bool:
    resolved = await users.by_ids([due.host_user_id, due.client_user_id])
    host = resolved.get(due.host_user_id)
    client = resolved.get(due.client_user_id)
    if host is None or client is None:
        logger.warning("reminder skipped: participant unresolved", booking_uid=str(due.id))
        return False
    now = clock.now()
    cmd_headers, cmd_body = build_reminder_command(due, host, client, now)
    await receiver.publish(cmd_headers, cmd_body)
    sent_headers, sent_body = build_reminder_sent(due, client, now)
    await receiver.publish(sent_headers, sent_body)
    await write.mark_sent(due.id, now)
    return True


async def remind_once(
    read: "IReminderReadAdapter",
    write: "IReminderWriteAdapter",
    users: "IUsersClient",
    receiver: "IReceiverClient",
    clock: "Clock",
    *,
    shift_from_minutes: int,
    shift_to_minutes: int,
    batch_size: int,
) -> int:
    due = await read.due_bookings(
        now=clock.now(), shift_from_minutes=shift_from_minutes, shift_to_minutes=shift_to_minutes, limit=batch_size
    )
    count = 0
    for booking in due:
        try:
            sent = await _remind_one(booking, write, users, receiver, clock)
        except Exception:  # noqa: BLE001 - isolate one booking; transient errors retry next tick
            logger.exception("reminder failed", booking_uid=str(booking.id))
            continue
        if sent:
            count += 1
    if count:
        logger.info("reminders sent", count=count)
    return count


async def run_reminder_loop(
    sessionmaker: "async_sessionmaker",
    users: "IUsersClient",
    receiver: "IReceiverClient",
    clock: "Clock",
    *,
    interval_s: float,
    shift_from_minutes: int,
    shift_to_minutes: int,
    batch_size: int,
    stop: asyncio.Event,
) -> None:
    """Background poller: own session per tick, commit after each batch, survive a failing tick."""
    while not stop.is_set():
        try:
            async with sessionmaker() as session:
                sql = _SqlExec(session)
                await remind_once(
                    ReminderReadAdapter(sql),
                    ReminderWriteAdapter(sql),
                    users,
                    receiver,
                    clock,
                    shift_from_minutes=shift_from_minutes,
                    shift_to_minutes=shift_to_minutes,
                    batch_size=batch_size,
                )
                await session.commit()
        except Exception:
            logger.exception("reminder tick failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
```

> Note: `_remind_one` returns `False` (no mark) when a participant can't be resolved; `remind_once` treats a raised receiver/DB error per-booking as "not sent" (isolated, retried next tick). `write.mark_sent` runs only after BOTH publishes succeed — the guard `reminder_sent_at IS NULL` + deterministic ce-id make a crash between publish and mark safe (redelivery is deduped downstream).

- [ ] **Step 4: Run — verify it PASSES**

Run: `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest tests/test_reminder_dispatcher.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/reminders/dispatcher.py \
        event-scheduling/tests/test_reminder_dispatcher.py
git commit -m "feat(reminders): remind_once + run_reminder_loop poller (slice 4a.3)"
```

---

## Task 6: config + DI + lifespan wiring

**Files:**
- Modify: `event_scheduling/config.py` (Settings), `event_scheduling/ioc.py` (providers), `event_scheduling/main.py` (second task)
- Test: `tests/test_reminder_config_ioc.py`

**Interfaces:**
- Consumes: `Settings`, `AppProvider` (`ioc.py`), `run_reminder_loop` (Task 5), `ReminderReadAdapter`/`ReminderWriteAdapter`, `IReminderReadAdapter`/`IReminderWriteAdapter`.
- Produces: `Settings.reminder_enabled/reminder_interval_seconds/reminder_shift_from_minutes/reminder_shift_to_minutes/reminder_batch_size`; Dishka providers binding `IReminderReadAdapter`/`IReminderWriteAdapter` (REQUEST scope, from `ISqlExecutor`); a second background task in the lifespan (started only when `reminder_enabled`).

- [ ] **Step 1: Write the failing test** `tests/test_reminder_config_ioc.py`:

```python
"""Reminder settings defaults + DI resolvability of reminder adapters."""

import pytest
from dishka import Scope

from event_scheduling.config import Settings
from event_scheduling.ioc import AppProvider
from event_scheduling.reminders.interfaces import IReminderReadAdapter, IReminderWriteAdapter


def test_reminder_settings_defaults(monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("POSTGRES_DSN", "postgresql+asyncpg://u:p@h:5432/d")
    monkeypatch.setenv("SCHEDULING_API_KEY", "k")
    s = Settings()
    assert s.reminder_enabled is True
    assert s.reminder_interval_seconds == 60.0
    assert s.reminder_shift_from_minutes == 55
    assert s.reminder_shift_to_minutes == 65
    assert s.reminder_batch_size == 100


@pytest.mark.asyncio
async def test_reminder_adapters_resolvable_in_request_scope(app) -> None:  # noqa: ANN001
    from dishka import make_async_container

    container = make_async_container(AppProvider())
    async with container() as app_scope:
        async with app_scope(scope=Scope.REQUEST) as req:
            assert await req.get(IReminderReadAdapter) is not None
            assert await req.get(IReminderWriteAdapter) is not None
    await container.close()
```
> If constructing a bare `make_async_container(AppProvider())` needs a Settings/env like other ioc tests, mirror the existing pattern in `tests/` for resolving REQUEST-scoped adapters (e.g. how `provide_booking_read` is exercised). The essential assertion: both reminder adapters resolve. Adjust the harness to match the repo's existing ioc test style if one exists; keep the settings-defaults test as-is.

- [ ] **Step 2: Run — verify it FAILS**

Run: `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest tests/test_reminder_config_ioc.py -v`
Expected: FAIL — `AttributeError: 'Settings' object has no attribute 'reminder_enabled'` / adapters not bound.

- [ ] **Step 3: Add settings** to `event_scheduling/config.py` (after the outbox block, before the `field_validator`):

```python
    # Reminders (slice 4a.3): in-service poller emits one ~1h-before reminder per confirmed booking.
    reminder_enabled: bool = True
    reminder_interval_seconds: float = 60.0
    reminder_shift_from_minutes: int = 55
    reminder_shift_to_minutes: int = 65
    reminder_batch_size: int = 100
```

- [ ] **Step 4: Add DI providers** to `event_scheduling/ioc.py`. Add the imports near the other interface/adapter imports:

```python
from event_scheduling.reminders.interfaces import IReminderReadAdapter, IReminderWriteAdapter
from event_scheduling.reminders.read_adapter import ReminderReadAdapter
from event_scheduling.reminders.write_adapter import ReminderWriteAdapter
```

Add two REQUEST-scoped providers inside `AppProvider` (next to `provide_booking_read`/`provide_booking_write`):

```python
    @provide(scope=Scope.REQUEST)
    def provide_reminder_read(self, sql: ISqlExecutor) -> IReminderReadAdapter:
        return ReminderReadAdapter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_reminder_write(self, sql: ISqlExecutor) -> IReminderWriteAdapter:
        return ReminderWriteAdapter(sql)
```

- [ ] **Step 5: Wire the lifespan** in `event_scheduling/main.py`. Add the import beside `run_dispatcher_loop`:

```python
from event_scheduling.reminders.dispatcher import run_reminder_loop
```

In `lifespan`, after the existing dispatcher `task = asyncio.create_task(run_dispatcher_loop(...))` block, start a second task guarded by the toggle. Use ONE shared `stop` event (it already exists) and collect both tasks:

```python
    tasks = [task]
    if settings.reminder_enabled:
        tasks.append(
            asyncio.create_task(
                run_reminder_loop(
                    sessionmaker,
                    users,
                    receiver,
                    clock,
                    interval_s=settings.reminder_interval_seconds,
                    shift_from_minutes=settings.reminder_shift_from_minutes,
                    shift_to_minutes=settings.reminder_shift_to_minutes,
                    batch_size=settings.reminder_batch_size,
                    stop=stop,
                )
            )
        )
```

Then in the `finally`, replace the single-task shutdown (`task.cancel()` / `await task`) with a loop over `tasks`:

```python
    finally:
        stop.set()
        for t in tasks:
            t.cancel()
        for t in tasks:
            with contextlib.suppress(asyncio.CancelledError):
                await t
        await container.close()
        logger.info("event-scheduling shutdown complete")
```
> `sessionmaker`, `users`, `receiver`, `clock`, `stop` are already resolved earlier in `lifespan` for the outbox dispatcher — reuse those exact locals. Do NOT resolve them twice.

- [ ] **Step 6: Run — verify it PASSES**

Run: `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest tests/test_reminder_config_ioc.py -v`
Expected: PASS. If the DI-resolvability harness needs adjustment to match the repo's ioc-test idiom, adapt it (Step 1 note) until both adapters resolve.

- [ ] **Step 7: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/config.py event-scheduling/event_scheduling/ioc.py \
        event-scheduling/event_scheduling/main.py event-scheduling/tests/test_reminder_config_ioc.py
git commit -m "feat(reminders): config + DI + lifespan wiring for reminder poller (slice 4a.3)"
```

---

## Task 7: docs + full suite + compose env + final checks

**Files:**
- Modify: `event-scheduling/CLAUDE.md`, `event-scheduling/docs/DATA_MODEL.md`, root `docs/architecture/MESSAGE_CONTRACTS.md`, `docker-compose.services.yml`

- [ ] **Step 1: Full suite + lint (regression gate).**

Run:
```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-scheduling
TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest -q
uv run ruff check .
uv run ruff format --check .
```
Expected: all green (existing suite + the new reminder tests). If the test DB predates 0004, recreate it (`docker rm -f sched-testpg` + docker run) so migrations apply fresh. Fix any lint before continuing.

- [ ] **Step 2: docker-compose env.** In `/Users/alexandrlelikov/PycharmProjects/events/docker-compose.services.yml`, add to the `event-scheduling` service `environment:` block (defaults already cover behavior — these make the reminder toggle/window overridable). Match the block's existing indentation:

```yaml
      # Reminders (slice 4a.3): in-service ~1h-before reminder poller.
      REMINDER_ENABLED: ${REMINDER_ENABLED:-true}
      REMINDER_INTERVAL_SECONDS: ${REMINDER_INTERVAL_SECONDS:-60}
      REMINDER_SHIFT_FROM_MINUTES: ${REMINDER_SHIFT_FROM_MINUTES:-55}
      REMINDER_SHIFT_TO_MINUTES: ${REMINDER_SHIFT_TO_MINUTES:-65}
```
> The reminder poller reuses the outbox's already-present `EVENT_RECEIVER_URL`/`BOOKING_API_KEY`/`EVENT_USERS_URL`/`EVENT_USERS_TOKEN` env — no new receiver/users config needed.

- [ ] **Step 3: Docs.**
  - `event-scheduling/CLAUDE.md`: add the `reminders/` module + poller flow (confirmed booking due in ~1h → `notification.send_requested` + `booking.reminder_sent` via `/event/booking` → mark `reminder_sent_at`; reschedule re-arms; `REMINDER_ENABLED` toggle). Note it's additive and the cal.com reminder path in event-booking is untouched. Read the file first; make a focused additive edit matching its structure.
  - `event-scheduling/docs/DATA_MODEL.md`: document `booking.reminder_sent_at` (nullable TIMESTAMPTZ; NULL = not yet reminded) + the partial index `ix_booking_reminder`.
  - Root `docs/architecture/MESSAGE_CONTRACTS.md`: note event-scheduling is now an additive producer of `notification.send_requested` (BOOKING_REMINDER trigger) and `booking.reminder_sent` for its own bookings, published to `/event/booking` with `ce-source: booking`; routed to `events.notification.commands` (notifier) and `events.booking.lifecycle` (saver) respectively. Reminder shape matches event-booking so the notifier is source-agnostic.

- [ ] **Step 4: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/CLAUDE.md event-scheduling/docs/DATA_MODEL.md \
        docs/architecture/MESSAGE_CONTRACTS.md docker-compose.services.yml
git commit -m "docs(reminders): document event-scheduling reminder poller + compose env (slice 4a.3)"
```

---

## Self-Review (completed during plan authoring)

**1. Spec coverage:**
- §1 architecture (parallel poller) → Tasks 2–6. §2 migration (column + partial index) → Task 1. §3 reschedule reset → Task 1. §4 payloads (both CE, deterministic ce-id, shape parity) → Task 4. §5 data flow → Task 5 (`remind_once`). §6 three-layer idempotency: (1) query excludes reminded → Task 2 SQL; (2) `mark_sent` NULL-guard → Task 3; (3) deterministic ce-id → Task 4. §7 error handling (skip-on-unresolved, per-booking isolation, tick survives) → Task 5. §8 config → Task 6. §9 tests — distributed across tasks. §10 deferred — untouched. §11 DoR + docs → Task 7.

**2. Placeholders:** All code is complete. Two explicitly-flagged verify-at-impl notes (not placeholders): Task 6 Step 1 DI-resolvability harness may need to match the repo's existing ioc-test idiom (assertion is fixed: both adapters resolve); Task 7 doc edits are "read the file, add focused section" against real files. All SQL, DTOs, builders, dispatcher are given in full.

**3. Type consistency:** `DueBookingDTO` fields (Task 2) are consumed identically in `build_reminder_command`/`build_reminder_sent` (Task 4: `.id/.start_time/.end_time/.title`) and `remind_once` (Task 5: `.host_user_id/.client_user_id/.id`). `ParticipantInfo` fields (`email/time_zone/name/locale`) used consistently in Task 4. `IReminderReadAdapter.due_bookings(*, now, shift_from_minutes, shift_to_minutes, limit)` — same keyword args in read_adapter (Task 2), remind_once (Task 5), run_reminder_loop (Task 5). `IReminderWriteAdapter.mark_sent(booking_id, now)` — same in Task 3 + Task 5. `IReceiverClient.publish(headers, body) -> int` and `IUsersClient.by_ids(list[UUID]) -> dict[UUID, ParticipantInfo]` match the real `publishing/interfaces.py`. `ce-source="booking"` uniform (Task 4) and consistent with `ROUTING_RULES`. Settings names (Task 6) match `run_reminder_loop` params (Task 5).
