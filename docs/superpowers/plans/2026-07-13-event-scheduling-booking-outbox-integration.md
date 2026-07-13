# event-scheduling — booking→events outbox integration (срез 4a) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `event-scheduling` при create/reschedule/cancel пишет строку в транзакционный `outbox`; фоновый диспетчер резолвит UUID→email, строит `booking.lifecycle` CloudEvent и публикует его через `POST /event/booking` (event-receiver), чтобы `event-saver` проецировал брони новой системы — аддитивно к cal.com.

**Architecture:** Изолированный модуль `event_scheduling/publishing/`: чистый `payload.py` (build CloudEvent), IO-слой — `outbox_writer` (INSERT в той же транзакции, что мутация брони), два HTTP-клиента (`receiver_client`, `users_client`), и `dispatcher` (poll `pending` → resolve → build → POST → sent/retry/failed с backoff). Диспетчер — фоновый asyncio-цикл в FastAPI-lifespan. At-least-once: стабильный `ce-id` + downstream-дедуп.

**Tech Stack:** Python 3.14, FastAPI, Dishka, SQLAlchemy async (`SqlExecutor` raw SQL), httpx, pytest.

**Spec:** `docs/superpowers/specs/2026-07-13-event-scheduling-booking-outbox-integration-design.md`
**Target service (main):** `event-scheduling/` — срезы 1–3 (модель, слоты, booking write-side с `booking`/`booking_change_log`).

## Global Constraints

- **Python `>=3.14`**; deps via `uv`; работать из `event-scheduling/`. `httpx` уже в deps (транзитивно через FastAPI/тесты; если нет — добавить в `pyproject.toml`).
- **Ruff** 120 / py314; **NO `elif`; avoid `else`** — guard clauses / early returns / mapping dicts.
- **Raw SQL только** через `SqlExecutor` (`:param`); ORM в `db/models.py` — только для alembic. DTO frozen; Pydantic только в `schemas/`.
- **Outbox пишется в ТОЙ ЖЕ транзакции**, что и мутация брони (общий `SqlExecutor`/сессия) — атомарно.
- **Booking write-path без внешних HTTP-вызовов** — только `INSERT` в `outbox`.
- **Публикация:** `POST /event/booking` на event-receiver, auth `Authorization: <BOOKING_API_KEY>` (**сырой** ключ, НЕ `Bearer`), CloudEvent binary mode (`ce-*` заголовки + JSON-тело), тело содержит `booking_uid`. Ответы `202`/`400`/`401`/`503`.
- **`ce-id` стабилен** (`outbox.event_ce_id`) → retry идемпотентны. `ce-source: booking`, `ce-type ∈ {booking.created,booking.rescheduled,booking.cancelled}`, `ce-specversion: 1.0`.
- Диспетчер: `202`→`sent`; транзиентно (`503`/таймаут/conn-error/event-users down/email-not-found)→retry с backoff `min(OUTBOX_MAX_BACKOFF_SECONDS, 5 * 2^attempts)`; перманентно (`400`/`401`)→`failed`.
- Ветка реализации: `feat/booking-outbox-impl`.

## Reuse from earlier slices

- `event_scheduling/booking/service.py`: `BookingService` (create/reschedule/cancel) — inject `IOutboxWriter`, call in-txn.
- `event_scheduling/booking/dto.py`: `BookingDTO`. `dto/schedule.py`: `ActorDTO`.
- `event_scheduling/adapters/sql.py`: `SqlExecutor` (+ `begin_nested`). `interfaces/sql.py`: `ISqlExecutor`.
- `event_scheduling/slots/interfaces.py`: `Clock`. `slots/service.py`: `SystemClock`.
- `event_scheduling/config.py`: `Settings` (pydantic-settings) — add new fields.
- `event_scheduling/ioc.py`: Dishka providers. `main.py`: FastAPI lifespan. `db/models.py`: `_uuid_pk()`. Alembic chain head = `0002`.

---

## File Structure

```
event-scheduling/
├── alembic/versions/0003_outbox.py               # outbox table
├── event_scheduling/
│   ├── db/models.py                              # + Outbox ORM (modify)
│   ├── config.py                                 # + EVENT_RECEIVER_URL/BOOKING_API_KEY/EVENT_USERS_URL/OUTBOX_* (modify)
│   ├── publishing/
│   │   ├── __init__.py
│   │   ├── dto.py                                # ParticipantInfo, OutboxRow
│   │   ├── interfaces.py                         # IOutboxWriter, IReceiverClient, IUsersClient
│   │   ├── payload.py                            # build_cloudevent (pure)
│   │   ├── outbox_writer.py                      # OutboxWriter (INSERT, same txn)
│   │   ├── receiver_client.py                    # ReceiverClient (POST /event/booking)
│   │   ├── users_client.py                       # UsersClient (GET /api/users/by-ids)
│   │   └── dispatcher.py                         # dispatch_once + run_dispatcher_loop
│   ├── booking/service.py                        # + outbox writes in create/reschedule/cancel (modify)
│   ├── booking/interfaces.py                      # IBookingService unchanged; note new dep (modify if needed)
│   ├── ioc.py                                    # + publishing providers (modify)
│   └── main.py                                   # + start/stop dispatcher loop in lifespan (modify)
├── tests/conftest.py                            # + outbox in _clean_db TRUNCATE (modify)
├── tests/test_outbox_schema.py
├── tests/test_publishing_payload.py
├── tests/test_outbox_writer.py
├── tests/test_publishing_clients.py
├── tests/test_dispatcher.py
```

---

## Task 1: Migration 0003 outbox + ORM + DTO

**Files:**
- Create: `alembic/versions/0003_outbox.py`, `event_scheduling/publishing/__init__.py`, `event_scheduling/publishing/dto.py`
- Modify: `event_scheduling/db/models.py`
- Test: `tests/test_outbox_schema.py`

**Interfaces:**
- Produces: table `outbox`; ORM `Outbox`; DTOs `ParticipantInfo(email: str, time_zone: str | None)`, `OutboxRow(id, event_ce_id, event_type, booking_uid, payload: dict, status, attempts, next_attempt_at)`.

- [ ] **Step 1: Failing test `tests/test_outbox_schema.py`**

```python
import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.mark.asyncio
async def test_outbox_insert_and_status_check(_migrated: str) -> None:
    eng = create_async_engine(_migrated)
    async with eng.begin() as conn:
        await conn.execute(text(
            "INSERT INTO outbox (event_ce_id, event_type, booking_uid, payload) "
            "VALUES (:ce, 'booking.created', :uid, CAST(:p AS jsonb))"),
            {"ce": uuid4(), "uid": str(uuid4()), "p": '{"start_time":"x"}'})
        row = (await conn.execute(text("SELECT status, attempts FROM outbox"))).one()
        assert row.status == "pending"
        assert row.attempts == 0
    async with eng.begin() as conn:
        with pytest.raises(Exception):  # noqa: B017 - CheckViolation
            await conn.execute(text(
                "INSERT INTO outbox (event_ce_id, event_type, booking_uid, payload, status) "
                "VALUES (:ce, 'booking.created', :uid, CAST('{}' AS jsonb), 'bogus')"),
                {"ce": uuid4(), "uid": str(uuid4())})
    await eng.dispose()
```

- [ ] **Step 2: Run — FAIL.** `cd event-scheduling && uv run pytest tests/test_outbox_schema.py -v` (local initdb is BROKEN — use Docker: `docker run -d --rm --name sched-testpg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=event_scheduling -p 5599:5432 postgres:16`, `TEST_POSTGRES_DSN=postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling`, stop after).

- [ ] **Step 3: ORM in `event_scheduling/db/models.py`** (append)

```python
class Outbox(Base):
    __tablename__ = "outbox"
    id: Mapped[str] = _uuid_pk()
    event_ce_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    booking_uid: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    next_attempt_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    __table_args__ = (
        CheckConstraint("status IN ('pending','sent','failed')", name="ck_outbox_status"),
        CheckConstraint(
            "event_type IN ('booking.created','booking.rescheduled','booking.cancelled')", name="ck_outbox_type"),
        Index("ix_outbox_dispatch", "status", "next_attempt_at"),
    )
```
(`JSONB`, `Integer` already imported in models.py from slice 3; confirm and add if missing.)

- [ ] **Step 4: Migration `alembic/versions/0003_outbox.py`**

```python
from collections.abc import Sequence
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels = None
depends_on = None
_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "outbox",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_ce_id", _UUID, nullable=False),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("booking_uid", sa.Text(), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("attempts", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("next_attempt_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("status IN ('pending','sent','failed')", name="ck_outbox_status"),
        sa.CheckConstraint(
            "event_type IN ('booking.created','booking.rescheduled','booking.cancelled')", name="ck_outbox_type"),
    )
    op.create_index("ix_outbox_dispatch", "outbox", ["status", "next_attempt_at"])


def downgrade() -> None:
    op.drop_table("outbox")
```

- [ ] **Step 5: `event_scheduling/publishing/__init__.py`** (empty) + **`event_scheduling/publishing/dto.py`**

```python
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class ParticipantInfo:
    email: str
    time_zone: str | None


@dataclass(frozen=True)
class OutboxRow:
    id: UUID
    event_ce_id: UUID
    event_type: str
    booking_uid: str
    payload: dict
    status: str
    attempts: int
    next_attempt_at: datetime
```

- [ ] **Step 6: Run — PASS.** `uv run pytest tests/test_outbox_schema.py tests/test_booking_schema.py -v`.

- [ ] **Step 7: Commit**
```bash
git add event_scheduling/db/models.py alembic/versions/0003_outbox.py event_scheduling/publishing tests/test_outbox_schema.py
git commit -m "feat(outbox): migration 0003 — outbox table + ORM + DTOs"
```

---

## Task 2: build_cloudevent (pure payload builder)

**Files:**
- Create: `event_scheduling/publishing/payload.py`
- Test: `tests/test_publishing_payload.py`

**Interfaces:**
- Consumes: `ParticipantInfo`.
- Produces: `payload.build_cloudevent(event_type: str, booking_uid: str, ce_id: str, payload: dict, host: ParticipantInfo, client: ParticipantInfo, now: datetime) -> tuple[dict[str, str], dict]` — returns `(ce_headers, body)`. `host`/`client` are the resolved participants; `payload` is the outbox `payload` dict (start_time/end_time/previous_start_time/cancellation_reason/attendee_time_zone as JSON values).

- [ ] **Step 1: Failing test `tests/test_publishing_payload.py`**

```python
import datetime as dt

from event_scheduling.publishing.dto import ParticipantInfo
from event_scheduling.publishing.payload import build_cloudevent

HOST = ParticipantInfo("org@x.io", "Europe/Berlin")
CLIENT = ParticipantInfo("cli@x.io", None)
NOW = dt.datetime(2026, 7, 13, 12, 0, tzinfo=dt.UTC)


def _payload(**kw) -> dict:
    base = {"host_user_id": "11111111-1111-1111-1111-111111111111",
            "client_user_id": "22222222-2222-2222-2222-222222222222",
            "start_time": "2026-10-01T07:00:00Z", "end_time": "2026-10-01T08:00:00Z",
            "attendee_time_zone": "Europe/Moscow"}
    base.update(kw)
    return base


def test_created_body_and_headers() -> None:
    headers, body = build_cloudevent("booking.created", "bk-1", "ce-1", _payload(), HOST, CLIENT, NOW)
    assert headers == {"ce-specversion": "1.0", "ce-id": "ce-1", "ce-source": "booking",
                       "ce-type": "booking.created", "ce-time": "2026-07-13T12:00:00+00:00"}
    assert body["booking_uid"] == "bk-1"
    assert body["start_time"] == "2026-10-01T07:00:00Z"
    assert body["volunteer_id"] == "11111111-1111-1111-1111-111111111111"
    assert body["client_id"] == "22222222-2222-2222-2222-222222222222"
    org = next(u for u in body["users"] if u["role"] == "organizer")
    cli = next(u for u in body["users"] if u["role"] == "client")
    assert org == {"email": "org@x.io", "role": "organizer", "time_zone": "Europe/Berlin"}
    assert cli == {"email": "cli@x.io", "role": "client", "time_zone": "Europe/Moscow"}  # attendee_tz


def test_rescheduled_includes_previous_start() -> None:
    _, body = build_cloudevent("booking.rescheduled", "bk-1", "ce-2",
                               _payload(previous_start_time="2026-10-01T06:00:00Z"), HOST, CLIENT, NOW)
    assert body["previous_start_time"] == "2026-10-01T06:00:00Z"
    assert body["start_time"] == "2026-10-01T07:00:00Z"
    assert "volunteer_id" not in body  # reschedule body per spec omits it


def test_cancelled_includes_reason() -> None:
    _, body = build_cloudevent("booking.cancelled", "bk-1", "ce-3",
                               _payload(cancellation_reason="client no-show"), HOST, CLIENT, NOW)
    assert body["cancellation_reason"] == "client no-show"
    assert body["booking_uid"] == "bk-1"
    assert {u["role"] for u in body["users"]} == {"organizer", "client"}
```

- [ ] **Step 2: Run — FAIL.** `uv run pytest tests/test_publishing_payload.py -v` (no DB).

- [ ] **Step 3: `event_scheduling/publishing/payload.py`**

```python
from datetime import datetime

from event_scheduling.publishing.dto import ParticipantInfo


def _users(host: ParticipantInfo, client: ParticipantInfo, attendee_tz: str | None) -> list[dict]:
    return [
        {"email": host.email, "role": "organizer", "time_zone": host.time_zone},
        {"email": client.email, "role": "client", "time_zone": attendee_tz},
    ]


def _created_body(booking_uid: str, payload: dict, users: list[dict]) -> dict:
    return {
        "users": users, "start_time": payload["start_time"], "end_time": payload["end_time"],
        "volunteer_id": payload["host_user_id"], "client_id": payload["client_user_id"],
        "booking_uid": booking_uid,
    }


def _rescheduled_body(booking_uid: str, payload: dict, users: list[dict]) -> dict:
    return {
        "users": users, "start_time": payload["start_time"], "end_time": payload["end_time"],
        "previous_start_time": payload.get("previous_start_time"), "booking_uid": booking_uid,
    }


def _cancelled_body(booking_uid: str, payload: dict, users: list[dict]) -> dict:
    body = {"users": users, "booking_uid": booking_uid}
    reason = payload.get("cancellation_reason")
    if reason is not None:
        body["cancellation_reason"] = reason
    return body


_BUILDERS = {
    "booking.created": _created_body,
    "booking.rescheduled": _rescheduled_body,
    "booking.cancelled": _cancelled_body,
}


def build_cloudevent(
    event_type: str, booking_uid: str, ce_id: str, payload: dict,
    host: ParticipantInfo, client: ParticipantInfo, now: datetime,
) -> tuple[dict[str, str], dict]:
    builder = _BUILDERS.get(event_type)
    if builder is None:
        msg = f"unknown event_type: {event_type!r}"
        raise ValueError(msg)
    users = _users(host, client, payload.get("attendee_time_zone"))
    body = builder(booking_uid, payload, users)
    headers = {
        "ce-specversion": "1.0", "ce-id": ce_id, "ce-source": "booking",
        "ce-type": event_type, "ce-time": now.isoformat(),
    }
    return headers, body
```
> Mapping dict of builders — no `elif`/`else`.

- [ ] **Step 4: Run — PASS.** `uv run pytest tests/test_publishing_payload.py -v`.

- [ ] **Step 5: Commit**
```bash
git add event_scheduling/publishing/payload.py tests/test_publishing_payload.py
git commit -m "feat(outbox): build_cloudevent payload builder (pure)"
```

---

## Task 3: OutboxWriter + BookingService integration

**Files:**
- Create: `event_scheduling/publishing/outbox_writer.py`
- Modify: `event_scheduling/publishing/interfaces.py` (create with `IOutboxWriter`), `event_scheduling/booking/service.py`, `event_scheduling/ioc.py`
- Test: `tests/test_outbox_writer.py`

**Interfaces:**
- Consumes: `ISqlExecutor`, `BookingDTO`, `Clock`.
- Produces:
  - `interfaces.IOutboxWriter.write(event_type: str, booking: BookingDTO, *, previous_start_time: datetime | None = None, cancellation_reason: str | None = None) -> None` — one INSERT via the shared `SqlExecutor` (same txn).
  - `BookingService.create/reschedule/cancel` now call the writer in-txn.

- [ ] **Step 1: Failing test `tests/test_outbox_writer.py`** (integration — service create writes an outbox row)

```python
import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.publishing.outbox_writer import OutboxWriter
from event_scheduling.booking.dto import BookingDTO


def _booking(**kw) -> BookingDTO:
    base = dict(id=uuid4(), event_type_id=uuid4(), host_user_id=uuid4(), client_user_id=uuid4(),
                start_time=dt.datetime(2026, 10, 1, 7, tzinfo=dt.UTC), end_time=dt.datetime(2026, 10, 1, 8, tzinfo=dt.UTC),
                status="confirmed", attendee_time_zone="Europe/Moscow", created_at=dt.datetime(2026, 7, 1, tzinfo=dt.UTC))
    base.update(kw)
    return BookingDTO(**base)


@pytest.mark.asyncio
async def test_write_created_row(sessionmaker_fixture) -> None:
    b = _booking()
    async with sessionmaker_fixture() as s:
        await OutboxWriter(SqlExecutor(s)).write("booking.created", b)
        await s.commit()
    async with sessionmaker_fixture() as s:
        row = (await s.execute(text(
            "SELECT event_type, booking_uid, status, payload FROM outbox WHERE booking_uid = :u"),
            {"u": str(b.id)})).one()
    assert row.event_type == "booking.created"
    assert row.status == "pending"
    assert row.payload["host_user_id"] == str(b.host_user_id)
    assert row.payload["attendee_time_zone"] == "Europe/Moscow"


@pytest.mark.asyncio
async def test_write_rescheduled_carries_previous(sessionmaker_fixture) -> None:
    b = _booking()
    prev = dt.datetime(2026, 10, 1, 6, tzinfo=dt.UTC)
    async with sessionmaker_fixture() as s:
        await OutboxWriter(SqlExecutor(s)).write("booking.rescheduled", b, previous_start_time=prev)
        await s.commit()
    async with sessionmaker_fixture() as s:
        payload = (await s.execute(text("SELECT payload FROM outbox WHERE booking_uid = :u"), {"u": str(b.id)})).scalar_one()
    assert payload["previous_start_time"] == prev.isoformat()
```

- [ ] **Step 2: Run — FAIL.** `uv run pytest tests/test_outbox_writer.py -v`.

- [ ] **Step 3: `event_scheduling/publishing/interfaces.py`**

```python
from datetime import datetime
from typing import Protocol

from event_scheduling.booking.dto import BookingDTO


class IOutboxWriter(Protocol):
    async def write(
        self, event_type: str, booking: BookingDTO, *,
        previous_start_time: datetime | None = None, cancellation_reason: str | None = None,
    ) -> None: ...
```

- [ ] **Step 4: `event_scheduling/publishing/outbox_writer.py`**

```python
import json
from datetime import datetime
from uuid import uuid4

from event_scheduling.booking.dto import BookingDTO
from event_scheduling.interfaces.sql import ISqlExecutor


class OutboxWriter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def write(
        self, event_type: str, booking: BookingDTO, *,
        previous_start_time: datetime | None = None, cancellation_reason: str | None = None,
    ) -> None:
        payload = {
            "host_user_id": str(booking.host_user_id),
            "client_user_id": str(booking.client_user_id),
            "start_time": booking.start_time.isoformat(),
            "end_time": booking.end_time.isoformat(),
            "attendee_time_zone": booking.attendee_time_zone,
        }
        if previous_start_time is not None:
            payload["previous_start_time"] = previous_start_time.isoformat()
        if cancellation_reason is not None:
            payload["cancellation_reason"] = cancellation_reason
        await self._sql.execute(
            """
            INSERT INTO outbox (event_ce_id, event_type, booking_uid, payload)
            VALUES (:ce, :type, :uid, CAST(:payload AS jsonb))
            """,
            {"ce": uuid4(), "type": event_type, "uid": str(booking.id), "payload": json.dumps(payload)},
        )
```

- [ ] **Step 5: Integrate into `BookingService`** — inject `IOutboxWriter`, call in-txn.

In `booking/service.py`, add `writer` to `__init__` (last positional param) and call after the mutation + change-log (before returning), in the same transaction:
- `create`: after `append_log(..., "created", ...)` and before `return booking`: `await self._outbox.write("booking.created", booking)`.
- `reschedule`: after `append_log(..., "rescheduled", ...)`: `await self._outbox.write("booking.rescheduled", updated, previous_start_time=booking.start_time)` (`booking.start_time` is the OLD start captured before update).
- `cancel`: after `append_log(..., "cancelled", ...)`: `await self._outbox.write("booking.cancelled", cancelled)`. (cancel has no reason field in the current API; pass none.)

```python
    def __init__(self, slots_read, read, write, busy, clock, outbox) -> None:  # noqa: ANN001, PLR0913
        ...
        self._outbox = outbox
```

Update `ioc.py` `provide_booking_service` to pass the new dep:
```python
    @provide(scope=Scope.REQUEST)
    def provide_outbox_writer(self, sql: ISqlExecutor) -> IOutboxWriter:
        return OutboxWriter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_booking_service(
        self, slots_read: ISlotsReadAdapter, read: IBookingReadAdapter, write: IBookingWriteAdapter,
        busy: BusyTimesSource, clock: Clock, outbox: IOutboxWriter,
    ) -> IBookingService:
        return BookingService(slots_read, read, write, busy, clock, outbox)
```
(+ imports `OutboxWriter`, `IOutboxWriter`.)

- [ ] **Step 6: Run — PASS.** `uv run pytest tests/test_outbox_writer.py tests/test_booking_api.py -v` (existing booking tests still green; they don't assert outbox emptiness). If any existing booking test constructs `BookingService(...)` directly, update it to pass an `OutboxWriter(sql)` (or a no-op fake) as the new arg.

- [ ] **Step 7: Commit**
```bash
git add event_scheduling/publishing/outbox_writer.py event_scheduling/publishing/interfaces.py event_scheduling/booking/service.py event_scheduling/ioc.py tests/test_outbox_writer.py tests/test_booking_api.py
git commit -m "feat(outbox): OutboxWriter + BookingService writes outbox row in-txn"
```

---

## Task 4: HTTP clients (receiver + users)

**Files:**
- Create: `event_scheduling/publishing/receiver_client.py`, `event_scheduling/publishing/users_client.py`
- Modify: `event_scheduling/publishing/interfaces.py` (add `IReceiverClient`, `IUsersClient`)
- Test: `tests/test_publishing_clients.py`

**Interfaces:**
- Produces:
  - `IReceiverClient.publish(ce_headers: dict[str, str], body: dict) -> int` — POST to `<receiver_url>/event/booking` with `Authorization: <api_key>` + ce-headers + JSON body; returns HTTP status code. Raises `httpx.HTTPError`/transport errors on connection failure.
  - `IUsersClient.by_ids(user_ids: list[UUID]) -> dict[UUID, ParticipantInfo]` — GET `<users_url>/api/users/by-ids`; maps returned rows to `{uuid: ParticipantInfo(email, time_zone)}`. Missing ids simply absent from the map.

- [ ] **Step 1: Failing test `tests/test_publishing_clients.py`** (use `httpx.MockTransport`)

```python
import json
from uuid import UUID, uuid4

import httpx
import pytest

from event_scheduling.publishing.receiver_client import ReceiverClient
from event_scheduling.publishing.users_client import UsersClient


@pytest.mark.asyncio
async def test_receiver_publish_sends_headers_and_key() -> None:
    seen = {}

    def handler(req: httpx.Request) -> httpx.Response:
        seen["url"] = str(req.url)
        seen["auth"] = req.headers.get("authorization")
        seen["ce-type"] = req.headers.get("ce-type")
        seen["body"] = json.loads(req.content)
        return httpx.Response(202)

    client = ReceiverClient("http://receiver:8888", "SECRET-KEY", transport=httpx.MockTransport(handler))
    status = await client.publish({"ce-type": "booking.created", "ce-id": "x"}, {"booking_uid": "bk-1"})
    assert status == 202
    assert seen["url"] == "http://receiver:8888/event/booking"
    assert seen["auth"] == "SECRET-KEY"  # raw, not Bearer
    assert seen["ce-type"] == "booking.created"
    assert seen["body"] == {"booking_uid": "bk-1"}


@pytest.mark.asyncio
async def test_users_by_ids_maps_email_tz() -> None:
    a, b = uuid4(), uuid4()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=[
            {"id": str(a), "email": "a@x.io", "time_zone": "Europe/Berlin"},
            {"id": str(b), "email": "b@x.io", "time_zone": None},
        ])

    client = UsersClient("http://users:8001", transport=httpx.MockTransport(handler))
    out = await client.by_ids([a, b])
    assert out[a].email == "a@x.io"
    assert out[a].time_zone == "Europe/Berlin"
    assert out[b].time_zone is None
```
> If the real `/api/users/by-ids` response shape differs (e.g. `{"users":[...]}` or field names `userId`/`timeZone`), adjust `UsersClient._parse` and this test to the real contract — verify against `event-users` at implementation time (spec §8.2). Keep the test and parser consistent.

- [ ] **Step 2: Run — FAIL.** `uv run pytest tests/test_publishing_clients.py -v` (no DB).

- [ ] **Step 3: `interfaces.py` additions**

```python
from uuid import UUID
from event_scheduling.publishing.dto import ParticipantInfo


class IReceiverClient(Protocol):
    async def publish(self, ce_headers: dict[str, str], body: dict) -> int: ...


class IUsersClient(Protocol):
    async def by_ids(self, user_ids: list[UUID]) -> dict[UUID, ParticipantInfo]: ...
```

- [ ] **Step 4: `receiver_client.py`**

```python
import httpx


class ReceiverClient:
    def __init__(self, base_url: str, api_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport

    async def publish(self, ce_headers: dict[str, str], body: dict) -> int:
        headers = {**ce_headers, "authorization": self._api_key, "content-type": "application/json"}
        async with httpx.AsyncClient(transport=self._transport, timeout=10.0) as client:
            resp = await client.post(f"{self._base_url}/event/booking", headers=headers, json=body)
        return resp.status_code
```

- [ ] **Step 5: `users_client.py`**

```python
from uuid import UUID

import httpx

from event_scheduling.publishing.dto import ParticipantInfo


class UsersClient:
    def __init__(self, base_url: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._transport = transport

    async def by_ids(self, user_ids: list[UUID]) -> dict[UUID, ParticipantInfo]:
        ids = [str(u) for u in user_ids]
        async with httpx.AsyncClient(transport=self._transport, timeout=10.0) as client:
            resp = await client.get(f"{self._base_url}/api/users/by-ids", params={"ids": ids})
        resp.raise_for_status()
        return self._parse(resp.json())

    @staticmethod
    def _parse(data: list[dict]) -> dict[UUID, ParticipantInfo]:
        return {UUID(r["id"]): ParticipantInfo(r["email"], r.get("time_zone")) for r in data}
```
> If `/api/users/by-ids` takes a POST body of ids instead of a `?ids=` query, switch to `client.post(..., json={"ids": ids})` — match the real event-users contract; adjust the test handler accordingly.

- [ ] **Step 6: Run — PASS.** `uv run pytest tests/test_publishing_clients.py -v`.

- [ ] **Step 7: Commit**
```bash
git add event_scheduling/publishing/receiver_client.py event_scheduling/publishing/users_client.py event_scheduling/publishing/interfaces.py tests/test_publishing_clients.py
git commit -m "feat(outbox): receiver + users HTTP clients"
```

---

## Task 5: dispatcher (dispatch_once)

**Files:**
- Create: `event_scheduling/publishing/dispatcher.py`
- Test: `tests/test_dispatcher.py`

**Interfaces:**
- Consumes: `ISqlExecutor`, `IUsersClient`, `IReceiverClient`, `Clock`, `payload.build_cloudevent`, `OutboxRow`.
- Produces: `dispatcher.dispatch_once(sql, users, receiver, clock, max_backoff_s: int, batch_size: int) -> int` — processes one batch of pending rows, returns count processed. `dispatcher.run_dispatcher_loop(sessionmaker, users, receiver, clock, settings, stop_event)` (background loop) — added here, exercised in Task 6.

- [ ] **Step 1: Failing test `tests/test_dispatcher.py`** (integration DB + fake clients)

```python
import datetime as dt
import json
from uuid import UUID, uuid4

import httpx
import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.publishing.dispatcher import dispatch_once
from event_scheduling.publishing.dto import ParticipantInfo


class _FixedClock:
    def __init__(self, now): self._now = now  # noqa: E701, ANN001
    def now(self): return self._now  # noqa: E704


class _Users:
    async def by_ids(self, ids):  # noqa: ANN001, ANN201
        return {u: ParticipantInfo(f"{u}@x.io", "Europe/Berlin") for u in ids}


class _Receiver:
    def __init__(self, status=202): self.status = status; self.calls = []  # noqa: E702, ANN001
    async def publish(self, headers, body):  # noqa: ANN001, ANN201
        self.calls.append((headers, body)); return self.status  # noqa: E702


async def _insert_pending(s, **kw) -> UUID:  # noqa: ANN001
    host, client = uuid4(), uuid4()
    payload = {"host_user_id": str(host), "client_user_id": str(client),
               "start_time": "2026-10-01T07:00:00Z", "end_time": "2026-10-01T08:00:00Z",
               "attendee_time_zone": "Europe/Moscow"}
    ce = uuid4()
    await s.execute(text(
        "INSERT INTO outbox (event_ce_id, event_type, booking_uid, payload) "
        "VALUES (:ce, 'booking.created', :uid, CAST(:p AS jsonb))"),
        {"ce": ce, "uid": str(uuid4()), "p": json.dumps(payload)})
    return ce


@pytest.mark.asyncio
async def test_dispatch_marks_sent_on_202(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        await _insert_pending(s); await s.commit()
    rcv = _Receiver(202)
    async with sessionmaker_fixture() as s:
        n = await dispatch_once(SqlExecutor(s), _Users(), rcv, _FixedClock(dt.datetime(2026, 7, 13, tzinfo=dt.UTC)), 300, 50)
        await s.commit()
    assert n == 1
    assert len(rcv.calls) == 1
    async with sessionmaker_fixture() as s:
        st = (await s.execute(text("SELECT status FROM outbox"))).scalar_one()
    assert st == "sent"


@pytest.mark.asyncio
async def test_dispatch_retries_on_503(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        await _insert_pending(s); await s.commit()
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
        await _insert_pending(s); await s.commit()
    async with sessionmaker_fixture() as s:
        await dispatch_once(SqlExecutor(s), _Users(), _Receiver(400), _FixedClock(dt.datetime(2026, 7, 13, tzinfo=dt.UTC)), 300, 50)
        await s.commit()
    async with sessionmaker_fixture() as s:
        st = (await s.execute(text("SELECT status FROM outbox"))).scalar_one()
    assert st == "failed"


@pytest.mark.asyncio
async def test_dispatch_uses_stable_ce_id(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        ce = await _insert_pending(s); await s.commit()
    rcv = _Receiver(202)
    async with sessionmaker_fixture() as s:
        await dispatch_once(SqlExecutor(s), _Users(), rcv, _FixedClock(dt.datetime(2026, 7, 13, tzinfo=dt.UTC)), 300, 50)
        await s.commit()
    assert rcv.calls[0][0]["ce-id"] == str(ce)
```

- [ ] **Step 2: Run — FAIL.** `uv run pytest tests/test_dispatcher.py -v`.

- [ ] **Step 3: `event_scheduling/publishing/dispatcher.py`**

```python
import asyncio
import structlog

from datetime import timedelta
from uuid import UUID

from event_scheduling.interfaces.sql import ISqlExecutor
from event_scheduling.publishing.dto import ParticipantInfo
from event_scheduling.publishing.payload import build_cloudevent

logger = structlog.get_logger(__name__)

_PERMANENT = {400, 401}
_SELECT = (
    "SELECT id, event_ce_id, event_type, booking_uid, payload, attempts "
    "FROM outbox WHERE status = 'pending' AND next_attempt_at <= now() "
    "ORDER BY created_at LIMIT :batch FOR UPDATE SKIP LOCKED"
)


async def dispatch_once(sql, users, receiver, clock, max_backoff_s: int, batch_size: int) -> int:  # noqa: ANN001, PLR0913
    rows = await sql.fetch_all(_SELECT, {"batch": batch_size})
    for row in rows:
        await _dispatch_row(sql, row, users, receiver, clock, max_backoff_s)
    return len(rows)


async def _mark_sent(sql, row_id) -> None:  # noqa: ANN001
    await sql.execute("UPDATE outbox SET status='sent', sent_at=now() WHERE id=:id", {"id": row_id})


async def _mark_failed(sql, row_id, err: str) -> None:  # noqa: ANN001
    await sql.execute("UPDATE outbox SET status='failed', last_error=:e WHERE id=:id", {"id": row_id, "e": err})


async def _mark_retry(sql, row_id, attempts: int, clock, max_backoff_s: int, err: str) -> None:  # noqa: ANN001, PLR0913
    delay = min(max_backoff_s, 5 * (2 ** attempts))
    nxt = clock.now() + timedelta(seconds=delay)
    await sql.execute(
        "UPDATE outbox SET attempts=attempts+1, next_attempt_at=:n, last_error=:e WHERE id=:id",
        {"id": row_id, "n": nxt, "e": err})


async def _dispatch_row(sql, row, users, receiver, clock, max_backoff_s: int) -> None:  # noqa: ANN001, PLR0913
    payload = row["payload"]
    host_id = UUID(payload["host_user_id"])
    client_id = UUID(payload["client_user_id"])
    try:
        resolved = await users.by_ids([host_id, client_id])
    except Exception as exc:  # noqa: BLE001 - transient users-service failure
        await _mark_retry(sql, row["id"], row["attempts"], clock, max_backoff_s, f"users:{exc}")
        return
    host = resolved.get(host_id)
    client = resolved.get(client_id)
    if host is None or client is None:
        await _mark_retry(sql, row["id"], row["attempts"], clock, max_backoff_s, "email-not-found")
        return
    headers, body = build_cloudevent(
        row["event_type"], row["booking_uid"], str(row["event_ce_id"]), payload, host, client, clock.now())
    try:
        status = await receiver.publish(headers, body)
    except Exception as exc:  # noqa: BLE001 - transient transport failure
        await _mark_retry(sql, row["id"], row["attempts"], clock, max_backoff_s, f"transport:{exc}")
        return
    if status == 202:
        await _mark_sent(sql, row["id"])
        return
    if status in _PERMANENT:
        await _mark_failed(sql, row["id"], f"http:{status}")
        return
    await _mark_retry(sql, row["id"], row["attempts"], clock, max_backoff_s, f"http:{status}")


async def run_dispatcher_loop(sessionmaker, users, receiver, clock, interval_s: float,  # noqa: ANN001, PLR0913
                              max_backoff_s: int, batch_size: int, stop: asyncio.Event) -> None:
    while not stop.is_set():
        try:
            async with sessionmaker() as session:
                await dispatch_once(_SqlExec(session), users, receiver, clock, max_backoff_s, batch_size)
                await session.commit()
        except Exception:  # noqa: BLE001 - loop must survive a bad tick
            logger.exception("dispatcher tick failed")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
        except TimeoutError:
            pass
```
> `_SqlExec` = import `from event_scheduling.adapters.sql import SqlExecutor as _SqlExec`. Guard-clause status handling (no `elif`/`else`). The loop opens its own session per tick (background, not request-scoped), commits after each batch, survives a failing tick, and sleeps interruptibly on `stop`.

- [ ] **Step 4: Run — PASS.** `uv run pytest tests/test_dispatcher.py -v`.

- [ ] **Step 5: Commit**
```bash
git add event_scheduling/publishing/dispatcher.py tests/test_dispatcher.py
git commit -m "feat(outbox): dispatcher — resolve/build/publish with retry+backoff"
```

---

## Task 6: config + DI + lifespan loop + stub-receiver integration + _clean_db

**Files:**
- Modify: `event_scheduling/config.py`, `event_scheduling/ioc.py`, `event_scheduling/main.py`, `tests/conftest.py`
- Test: `tests/test_dispatcher.py` (add a stub-receiver end-to-end + idempotency test)

**Interfaces:**
- Produces: real `ReceiverClient`/`UsersClient` provided via Dishka (APP scope); the dispatcher loop started/stopped in `main.py` lifespan; new `Settings` fields.

- [ ] **Step 1: Config in `event_scheduling/config.py`** — add fields (with dev defaults so boot/tests don't require them):

```python
    event_receiver_url: str = "http://event-receiver:8888"
    booking_api_key: str = "dev-booking-api-key"
    event_users_url: str = "http://event-users:8001"
    outbox_dispatch_interval: float = 5.0
    outbox_batch_size: int = 50
    outbox_max_backoff_seconds: int = 300
```

- [ ] **Step 2: DI providers in `ioc.py`** (APP scope for the stateless HTTP clients)

```python
from event_scheduling.publishing.receiver_client import ReceiverClient
from event_scheduling.publishing.users_client import UsersClient
from event_scheduling.publishing.interfaces import IReceiverClient, IUsersClient

    @provide(scope=Scope.APP)
    def provide_receiver_client(self, settings: Settings) -> IReceiverClient:
        return ReceiverClient(settings.event_receiver_url, settings.booking_api_key)

    @provide(scope=Scope.APP)
    def provide_users_client(self, settings: Settings) -> IUsersClient:
        return UsersClient(settings.event_users_url)
```

- [ ] **Step 3: Lifespan loop in `main.py`** — start/stop the dispatcher

```python
import asyncio
from event_scheduling.publishing.dispatcher import run_dispatcher_loop

# inside lifespan(), after container/settings are ready:
    settings = await container.get(Settings)
    sessionmaker = await container.get(async_sessionmaker[AsyncSession])
    users = await container.get(IUsersClient)
    receiver = await container.get(IReceiverClient)
    clock = await container.get(Clock)
    stop = asyncio.Event()
    task = asyncio.create_task(run_dispatcher_loop(
        sessionmaker, users, receiver, clock,
        settings.outbox_dispatch_interval, settings.outbox_max_backoff_seconds, settings.outbox_batch_size, stop))
    try:
        yield
    finally:
        stop.set()
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
        await container.close()
```
> Import `contextlib`, `asyncio`, `async_sessionmaker`, `AsyncSession`, `Clock`, `IUsersClient`, `IReceiverClient`, `Settings`. NOTE: the test `app` fixture in `conftest.py` builds a bare `FastAPI()` WITHOUT this lifespan, so the background loop does NOT run during tests (dispatcher is exercised directly via `dispatch_once`). Only the real `main.py` app runs the loop.

- [ ] **Step 4: `_clean_db` carry-forward in `tests/conftest.py`** — add `outbox` to the TRUNCATE table list (per-test isolation for outbox rows).

- [ ] **Step 5: Stub-receiver end-to-end + idempotency test (add to `tests/test_dispatcher.py`)**

```python
@pytest.mark.asyncio
async def test_end_to_end_via_httpx_stub_and_idempotent_ce_id(sessionmaker_fixture) -> None:
    from event_scheduling.publishing.receiver_client import ReceiverClient
    captured = []

    def handler(req: httpx.Request) -> httpx.Response:
        captured.append((req.headers.get("ce-type"), req.headers.get("ce-id"), req.headers.get("authorization")))
        return httpx.Response(202)

    receiver = ReceiverClient("http://receiver:8888", "SECRET", transport=httpx.MockTransport(handler))
    async with sessionmaker_fixture() as s:
        ce = await _insert_pending(s); await s.commit()
    clock = _FixedClock(dt.datetime(2026, 7, 13, tzinfo=dt.UTC))
    async with sessionmaker_fixture() as s:
        await dispatch_once(SqlExecutor(s), _Users(), receiver, clock, 300, 50); await s.commit()
    assert captured == [("booking.created", str(ce), "SECRET")]
    # a second dispatch finds nothing pending (already sent) — no duplicate send
    async with sessionmaker_fixture() as s:
        n = await dispatch_once(SqlExecutor(s), _Users(), receiver, clock, 300, 50); await s.commit()
    assert n == 0
    assert len(captured) == 1  # ce-id stable, no re-send
```

- [ ] **Step 6: Run — PASS + FULL suite.** `uv run pytest -v` (all slices green; existing booking tests unaffected — outbox now truncated per test).

- [ ] **Step 7: Commit**
```bash
git add event_scheduling/config.py event_scheduling/ioc.py event_scheduling/main.py tests/conftest.py tests/test_dispatcher.py
git commit -m "feat(outbox): DI + lifespan dispatcher loop + config + end-to-end stub test"
```

---

## Task 7: docs + final checks

**Files:**
- Modify: `event-scheduling/CLAUDE.md`, `event-scheduling/docs/{DATA_MODEL,DEPENDENCIES,SERVICE_OVERVIEW}.md`, root `docs/architecture/ARCHITECTURE.md` + `docs/architecture/MESSAGE_CONTRACTS.md`
- Modify (compose): repo-root `docker-compose.services.yml` (env for event-scheduling)

- [ ] **Step 1: Docs.** `CLAUDE.md`: add `publishing/` module + the outbox flow (booking mutation → outbox row in-txn → background dispatcher → `POST /event/booking` → event-saver projections; additive to cal.com; event-booking is no-op until slice 4a.2). `docs/DATA_MODEL.md`: `outbox` table (now 11 tables). `docs/DEPENDENCIES.md`: NEW runtime deps — `event-receiver` (`POST /event/booking`, `BOOKING_API_KEY`) and `event-users` (`GET /api/users/by-ids`); both only exercised by the background dispatcher (booking write-path stays dependency-free); failure mode = outbox rows retry. `docs/SERVICE_OVERVIEW.md`: slice-4a maturity (publishes booking.lifecycle; projections active; event-booking chat/Jitsi deferred to 4a.2). Root `docs/architecture/ARCHITECTURE.md` + `MESSAGE_CONTRACTS.md`: event-scheduling is now an additive `booking.lifecycle` producer alongside cal.com (same `POST /event/booking` contract, same event types).

- [ ] **Step 2: docker-compose env.** In `docker-compose.services.yml`, add to the `event-scheduling` service `environment`: `EVENT_RECEIVER_URL: http://event-receiver:8888`, `BOOKING_API_KEY: ${BOOKING_API_KEY:-...}` (match the value event-receiver expects — reuse the same env the receiver uses for its booking key), `EVENT_USERS_URL: http://event-users:8001`. Verify `BOOKING_API_KEY` matches what `event-receiver` compares against (check the receiver's compose env).

- [ ] **Step 3: Full test + lint.** `cd event-scheduling && uv run pytest && ruff check . && ruff format --check .` — green (Docker Postgres per Task 1).

- [ ] **Step 4: Compose smoke (best-effort).** Bring up the contour (`docker compose up -d postgres rabbitmq event-receiver event-users event-saver event-scheduling`), create a booking via `POST /api/v1/bookings`, wait a few dispatch intervals, then check the `outbox` row flips to `sent` and (if event-saver DB reachable) a projection row appears keyed by `booking_uid = booking.id`. If the full contour is impractical, verify the outbox row is written and note the downstream as unverified — pytest is the hard gate.

- [ ] **Step 5: Commit**
```bash
git add event-scheduling/CLAUDE.md event-scheduling/docs docs/architecture docker-compose.services.yml
git commit -m "docs(outbox): document booking→events integration (slice 4a) + compose env"
```

---

## Self-Review (проведён при написании плана)

**1. Покрытие спека:**
- §2 схема outbox → Task 1. §5 payload → Task 2. §3 outbox-запись в BookingService (в транзакции) → Task 3. §1 HTTP-клиенты → Task 4. §4 диспетчер (resolve/build/POST/sent/retry/failed/backoff/стабильный ce-id) → Task 5. §6 config+DI+lifespan-цикл → Task 6. §7 тесты — распределены по задачам (payload/writer/dispatcher/stub-receiver/идемпотентность). §9 DoR + docs + compose → Task 7.
- Резолв UUID→email на dispatch → Task 5 `_dispatch_row`. Идемпотентность (стабильный ce-id) → Task 5/6. Аддитивность (не трогаем cal.com/receiver/booking) → нигде не модифицируем те сервисы.

**2. Плейсхолдеры:** код в шагах полный. Помечены явно: реальный контракт `/api/users/by-ids` (Task 4) — verify-at-impl с указанием, как подстроить парсер+тест; `BOOKING_API_KEY` совпадение с receiver (Task 7). Это указания сверить внешний контракт, не заглушки.

**3. Согласованность типов:** `ParticipantInfo`/`OutboxRow` (Task 1) → 2/4/5. `IOutboxWriter.write(event_type, booking, *, previous_start_time, cancellation_reason)` (Task 3) → BookingService + ioc. `IReceiverClient.publish(ce_headers, body)->int`, `IUsersClient.by_ids(ids)->dict[UUID,ParticipantInfo]` (Task 4) → dispatcher (Task 5). `build_cloudevent(event_type, booking_uid, ce_id, payload, host, client, now)` (Task 2) → dispatcher (Task 5). `dispatch_once(sql, users, receiver, clock, max_backoff_s, batch_size)` + `run_dispatcher_loop(...)` (Task 5) → lifespan (Task 6). `Clock`/`SystemClock`, `SqlExecutor`, `BookingDTO`, `async_sessionmaker` — из срезов 1–3, сигнатуры совпадают.
