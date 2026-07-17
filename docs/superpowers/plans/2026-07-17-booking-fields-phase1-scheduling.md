# Booking Fields — Phase 1 (event-scheduling core) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give `event-scheduling` per-event-type configurable booking fields: a `booking_field` table, a management API to read/replace a type's fields, the field defs exposed on the event-type read, and booking-create validating + storing the guest's answers (snapshot) — all configurable via API before any UI exists.

**Architecture:** Follows event-scheduling's existing layering: raw-SQL `SqlExecutor` adapters behind `Protocol` interfaces, frozen-dataclass DTOs, Pydantic in `schemas/`, thin controllers, DishkaRoute routers under `require_api_key`. Booking fields live in a new `booking_fields/` module; answer validation is a pure function reused by the booking service; answers are stored as a JSONB snapshot on `booking` and echoed into the outbox payload.

**Tech Stack:** Python 3.14, FastAPI, Dishka, SQLAlchemy Core (raw `text()` via `SqlExecutor`), Alembic, PostgreSQL (JSONB), pytest against a real Postgres.

## Global Constraints

- **Package:** `event-scheduling` only (Phase 1). Phases 2–3 (BFF/frontends/admin) are separate plans.
- **Field types (6):** `text`, `textarea`, `select`, `radio`, `checkbox`, `boolean`. `select`/`radio`/`checkbox` carry `options` (`{"value","label"}[]`); the others must NOT.
- **Value shapes by type:** `text`/`textarea` → string; `select`/`radio` → one option value (string); `checkbox` → list of option values (unique subset); `boolean` → bool.
- **`field_key`** is a slug derived from `label`, unique within an event type (dedupe with `-2`, `-3`, …). Answers reference `key`.
- **Answers stored as a snapshot:** `booking.field_answers` JSONB = `[{"key","label","type","value"}, …]`, captured at booking time; later field edits never mutate stored bookings. The same snapshot is included in the outbox event payload.
- **event-scheduling is the authoritative validator** for answers (required present & non-empty; option membership; checkbox subset & unique; boolean is bool; unknown key rejected). Failure → `ValidationError` (mapped to `422`).
- **Replace-all write model:** `PUT …/booking-fields` replaces the whole ordered list (no granular CRUD). `position` comes from array order.
- **Conventions:** no `elif`; avoid `else` (early returns / guard clauses / mapping dicts); Ruff line-length 120; frozen dataclasses as DTOs; Pydantic only in `schemas/`; `Protocol` interfaces in `interfaces/`; raw `:param` SQL via `SqlExecutor`.
- **Migrations:** event-scheduling owns its schema; next revision is `0006` (after `0005_external_calendar`). `down_revision = "0005"`.
- **Tests:** real Postgres via `TEST_POSTGRES_DSN` (or the suite's ephemeral cluster); `SCHEDULING_API_KEY` is `"test-scheduling-key"` in tests; auth header `Authorization: Bearer test-scheduling-key`.
- **Repo/commits:** `event-scheduling` is **root-tracked** (no nested `.git`) → commit from repo root `/Users/alexandrlelikov/PycharmProjects/events`, staging `event-scheduling/...`, on branch `feat/booking-fields-p1`.

---

### Task 1: Migration 0006 — `booking_field` table + `booking.field_answers`

**Files:**
- Create: `event-scheduling/alembic/versions/0006_booking_fields.py`
- Test: `event-scheduling/tests/test_migration_0006.py`

**Interfaces:**
- Produces: table `booking_field(id, event_type_id, field_key, field_type, label, placeholder, required, options, position, created_at, updated_at)` with `UNIQUE(event_type_id, field_key)` and a `field_type` CHECK; column `booking.field_answers JSONB NOT NULL DEFAULT '[]'`.

- [ ] **Step 1: Write the migration `0006_booking_fields.py`**

```python
"""booking_field table + booking.field_answers

Revision ID: 0006
Revises: 0005
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None

_UUID = postgresql.UUID(as_uuid=True)
_FIELD_TYPES = "('text','textarea','select','radio','checkbox','boolean')"


def upgrade() -> None:
    op.create_table(
        "booking_field",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_type_id", _UUID, nullable=False),
        sa.Column("field_key", sa.Text(), nullable=False),
        sa.Column("field_type", sa.Text(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("placeholder", sa.Text(), nullable=True),
        sa.Column("required", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("options", postgresql.JSONB(), nullable=True),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["event_type_id"], ["event_type.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("event_type_id", "field_key", name="uq_booking_field_key"),
        sa.CheckConstraint(f"field_type IN {_FIELD_TYPES}", name="ck_booking_field_type"),
    )
    op.create_index("ix_booking_field_event_type", "booking_field", ["event_type_id", "position"])
    op.add_column(
        "booking",
        sa.Column("field_answers", postgresql.JSONB(), server_default=sa.text("'[]'::jsonb"), nullable=False),
    )


def downgrade() -> None:
    op.drop_column("booking", "field_answers")
    op.drop_index("ix_booking_field_event_type", table_name="booking_field")
    op.drop_table("booking_field")
```

- [ ] **Step 2: Write the test `test_migration_0006.py`**

```python
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_booking_field_table_and_answers_column_exist(db_session):
    # booking_field columns
    cols = (await db_session.execute(text(
        "SELECT column_name FROM information_schema.columns WHERE table_name='booking_field'"
    ))).scalars().all()
    for c in ("id", "event_type_id", "field_key", "field_type", "label", "placeholder", "required", "options", "position"):
        assert c in cols
    # field_answers on booking, defaulting to []
    default = (await db_session.execute(text(
        "SELECT column_default FROM information_schema.columns "
        "WHERE table_name='booking' AND column_name='field_answers'"
    ))).scalar_one()
    assert "'[]'" in default
    # CHECK rejects an unknown type
    with pytest.raises(Exception):
        await db_session.execute(text(
            "INSERT INTO booking_field (event_type_id, field_key, field_type, label, position) "
            "VALUES (gen_random_uuid(), 'k', 'bogus', 'L', 0)"
        ))
```

Use the suite's existing DB-session fixture (inspect `event-scheduling/tests/conftest.py` for its exact name — the migration/DB tests there already use one; reuse it verbatim rather than inventing `db_session` if the fixture has another name).

- [ ] **Step 3: Run migration + test**

Run: `cd event-scheduling && TEST_POSTGRES_DSN=$TEST_POSTGRES_DSN uv run pytest tests/test_migration_0006.py -v`
Expected: PASS (the harness applies `alembic upgrade head` before tests). Confirm `uv run alembic upgrade head` then `uv run alembic downgrade -1` run cleanly.

- [ ] **Step 4: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/alembic/versions/0006_booking_fields.py event-scheduling/tests/test_migration_0006.py
git commit -m "feat(scheduling): migration 0006 — booking_field table + booking.field_answers"
```

---

### Task 2: DTOs + pure validation helpers (`booking_fields/` domain)

**Files:**
- Create: `event-scheduling/event_scheduling/booking_fields/__init__.py`
- Create: `event-scheduling/event_scheduling/booking_fields/dto.py`
- Create: `event-scheduling/event_scheduling/booking_fields/domain.py`
- Test: `event-scheduling/tests/test_booking_fields_domain.py`

**Interfaces:**
- Produces (DTOs):
  - `OptionDTO(value: str, label: str)` (frozen)
  - `BookingFieldDTO(field_key: str, field_type: str, label: str, placeholder: str | None, required: bool, options: list[OptionDTO], position: int)` (frozen)
  - `UpsertBookingFieldDTO(field_type: str, label: str, placeholder: str | None, required: bool, options: list[OptionDTO])` (frozen; no key/position — server assigns)
  - `AnswerDTO(key: str, value: str | list[str] | bool)` (frozen)
  - `AnsweredFieldDTO(key: str, label: str, field_type: str, value: str | list[str] | bool)` (frozen; the snapshot element)
- Produces (pure functions in `domain.py`):
  - `FIELD_TYPES: frozenset[str]` and `OPTION_TYPES: frozenset[str] = {'select','radio','checkbox'}`
  - `slugify_key(label: str) -> str`
  - `assign_keys(items: list[UpsertBookingFieldDTO]) -> list[str]` — slug per item, de-duplicated in order (`reason`, `reason-2`, …)
  - `validate_field_items(items: list[UpsertBookingFieldDTO]) -> None` — raises `ValidationError` on: empty label; unknown type; option type with <1 option or duplicate/empty option values; non-option type carrying options.
  - `validate_and_snapshot(fields: list[BookingFieldDTO], answers: list[AnswerDTO]) -> list[AnsweredFieldDTO]` — the authoritative answer validator (see rules below); returns the snapshot or raises `ValidationError`.

- [ ] **Step 1: Write the failing test `test_booking_fields_domain.py`**

```python
import pytest
from event_scheduling.booking_fields.domain import (
    assign_keys, slugify_key, validate_field_items, validate_and_snapshot,
)
from event_scheduling.booking_fields.dto import AnswerDTO, BookingFieldDTO, OptionDTO, UpsertBookingFieldDTO
from event_scheduling.errors import ValidationError


def _field(key, ftype, required=False, options=None):
    return BookingFieldDTO(field_key=key, field_type=ftype, label=key.title(), placeholder=None,
                           required=required, options=options or [], position=0)


def _opt(*vals):
    return [OptionDTO(value=v, label=v.title()) for v in vals]


def test_slugify_and_dedupe():
    assert slugify_key("Почему нужна помощь") == "pochemu-nuzhna-pomoshch" or slugify_key("Reason For Visit") == "reason-for-visit"
    items = [UpsertBookingFieldDTO("text", "Reason", None, False, []),
             UpsertBookingFieldDTO("text", "Reason", None, False, [])]
    assert assign_keys(items) == ["reason", "reason-2"]


def test_validate_field_items_rejects_bad_shapes():
    with pytest.raises(ValidationError):
        validate_field_items([UpsertBookingFieldDTO("select", "Pick", None, False, [])])  # option type, no options
    with pytest.raises(ValidationError):
        validate_field_items([UpsertBookingFieldDTO("text", "T", None, False, _opt("a"))])  # non-option with options
    with pytest.raises(ValidationError):
        validate_field_items([UpsertBookingFieldDTO("text", "", None, False, [])])  # empty label
    validate_field_items([UpsertBookingFieldDTO("radio", "Pick", None, True, _opt("a", "b"))])  # ok


def test_validate_and_snapshot_required_and_membership():
    fields = [_field("reason", "textarea", required=True),
              _field("topics", "checkbox", options=_opt("anx", "sleep")),
              _field("agree", "boolean", required=True)]
    # missing required 'reason' → error
    with pytest.raises(ValidationError):
        validate_and_snapshot(fields, [AnswerDTO("agree", True)])
    # checkbox value outside options → error
    with pytest.raises(ValidationError):
        validate_and_snapshot(fields, [AnswerDTO("reason", "hi"), AnswerDTO("agree", True), AnswerDTO("topics", ["nope"])])
    # unknown key → error
    with pytest.raises(ValidationError):
        validate_and_snapshot(fields, [AnswerDTO("reason", "hi"), AnswerDTO("agree", True), AnswerDTO("bogus", "x")])
    # happy path → snapshot preserves label/type/value
    snap = validate_and_snapshot(fields, [AnswerDTO("reason", "hi"), AnswerDTO("agree", True), AnswerDTO("topics", ["anx"])])
    by_key = {s.key: s for s in snap}
    assert by_key["reason"].label == "Reason" and by_key["reason"].field_type == "textarea"
    assert by_key["topics"].value == ["anx"]
```

- [ ] **Step 2: Run it — expect failure**

Run: `cd event-scheduling && uv run pytest tests/test_booking_fields_domain.py -v`
Expected: FAIL (module missing).

- [ ] **Step 3: Implement `dto.py`**

```python
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
```

- [ ] **Step 4: Implement `domain.py`**

```python
import re
import unicodedata

from event_scheduling.booking_fields.dto import (
    AnsweredFieldDTO, AnswerDTO, BookingFieldDTO, OptionDTO, UpsertBookingFieldDTO,
)
from event_scheduling.errors import ValidationError

FIELD_TYPES = frozenset({"text", "textarea", "select", "radio", "checkbox", "boolean"})
OPTION_TYPES = frozenset({"select", "radio", "checkbox"})

_CYR = {
    "а": "a", "б": "b", "в": "v", "г": "g", "д": "d", "е": "e", "ё": "e", "ж": "zh", "з": "z", "и": "i",
    "й": "i", "к": "k", "л": "l", "м": "m", "н": "n", "о": "o", "п": "p", "р": "r", "с": "s", "т": "t",
    "у": "u", "ф": "f", "х": "h", "ц": "c", "ч": "ch", "ш": "sh", "щ": "shch", "ъ": "", "ы": "y", "ь": "",
    "э": "e", "ю": "yu", "я": "ya",
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
        snapshot.append(AnsweredFieldDTO(key=field.field_key, label=field.label, field_type=field.field_type, value=value))
    return snapshot
```

- [ ] **Step 5: Run tests — expect pass; then Ruff**

Run: `cd event-scheduling && uv run pytest tests/test_booking_fields_domain.py -v && uv run ruff check event_scheduling/booking_fields && uv run ruff format --check event_scheduling/booking_fields`
Expected: all pass; Ruff clean.

- [ ] **Step 6: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/booking_fields/ event-scheduling/tests/test_booking_fields_domain.py
git commit -m "feat(scheduling): booking-field DTOs + pure validation/slug/snapshot helpers"
```

---

### Task 3: DB adapter + controller + interfaces (read / replace-all)

**Files:**
- Create: `event-scheduling/event_scheduling/booking_fields/interfaces.py`
- Create: `event-scheduling/event_scheduling/booking_fields/adapter.py`
- Create: `event-scheduling/event_scheduling/booking_fields/controller.py`
- Test: `event-scheduling/tests/test_booking_fields_db.py`

**Interfaces:**
- Consumes: DTOs + `validate_field_items`/`assign_keys` (Task 2); `ISqlExecutor`; `NotFoundError`/`ValidationError`.
- Produces:
  - `IBookingFieldAdapter` (Protocol): `list_for(event_type_id: UUID) -> list[BookingFieldDTO]`; `replace(event_type_id: UUID, items: list[UpsertBookingFieldDTO], keys: list[str]) -> list[BookingFieldDTO]`; `event_type_exists(event_type_id: UUID) -> bool`.
  - `IBookingFieldController` (Protocol): `list_for(event_type_id) -> list[BookingFieldDTO]`; `replace(event_type_id, items) -> list[BookingFieldDTO]`.
  - `BookingFieldController.replace`: `validate_field_items(items)` → `keys = assign_keys(items)` → check `event_type_exists` (else `NotFoundError`) → `adapter.replace(...)`.

- [ ] **Step 1: Write the failing DB test `test_booking_fields_db.py`**

```python
import pytest
from event_scheduling.booking_fields.adapter import BookingFieldAdapter
from event_scheduling.booking_fields.controller import BookingFieldController
from event_scheduling.booking_fields.dto import OptionDTO, UpsertBookingFieldDTO
from event_scheduling.errors import NotFoundError


def _up(ftype, label, required=False, options=None):
    return UpsertBookingFieldDTO(field_type=ftype, label=label, placeholder=None, required=required, options=options or [])


@pytest.mark.asyncio
async def test_replace_all_assigns_keys_and_positions(sql_executor, seeded_event_type_id):
    ctrl = BookingFieldController(BookingFieldAdapter(sql_executor))
    stored = await ctrl.replace(seeded_event_type_id, [
        _up("textarea", "Reason", required=True),
        _up("checkbox", "Topics", options=[OptionDTO("anx", "Anxiety"), OptionDTO("sleep", "Sleep")]),
    ])
    assert [f.field_key for f in stored] == ["reason", "topics"]
    assert [f.position for f in stored] == [0, 1]
    # replace again with a shorter list → old rows gone
    stored2 = await ctrl.replace(seeded_event_type_id, [_up("text", "Name again")])
    assert [f.field_key for f in stored2] == ["name-again"]
    assert await ctrl.list_for(seeded_event_type_id) == stored2


@pytest.mark.asyncio
async def test_replace_unknown_event_type_raises_not_found(sql_executor):
    from uuid import uuid4
    ctrl = BookingFieldController(BookingFieldAdapter(sql_executor))
    with pytest.raises(NotFoundError):
        await ctrl.replace(uuid4(), [_up("text", "X")])
```

Reuse the suite's existing SQL-executor + event-type seeding fixtures (grep `event-scheduling/tests/conftest.py` and existing `test_*_db.py` for the real fixture names — e.g. how `test` seeds an `event_type` row and exposes a `SqlExecutor`; use those names, not invented ones).

- [ ] **Step 2: Run it — expect failure.** `cd event-scheduling && uv run pytest tests/test_booking_fields_db.py -v` → FAIL (modules missing).

- [ ] **Step 3: Implement `interfaces.py`**

```python
from typing import Protocol
from uuid import UUID

from event_scheduling.booking_fields.dto import BookingFieldDTO, UpsertBookingFieldDTO


class IBookingFieldAdapter(Protocol):
    async def list_for(self, event_type_id: UUID) -> list[BookingFieldDTO]: ...
    async def replace(
        self, event_type_id: UUID, items: list[UpsertBookingFieldDTO], keys: list[str]
    ) -> list[BookingFieldDTO]: ...
    async def event_type_exists(self, event_type_id: UUID) -> bool: ...


class IBookingFieldController(Protocol):
    async def list_for(self, event_type_id: UUID) -> list[BookingFieldDTO]: ...
    async def replace(
        self, event_type_id: UUID, items: list[UpsertBookingFieldDTO]
    ) -> list[BookingFieldDTO]: ...
```

- [ ] **Step 4: Implement `adapter.py`** (raw SQL; `options` stored as JSONB via `json.dumps`; rows read back ordered by `position`)

```python
import json
from uuid import UUID

from event_scheduling.booking_fields.dto import BookingFieldDTO, OptionDTO, UpsertBookingFieldDTO
from event_scheduling.interfaces.sql import ISqlExecutor


def _row_to_dto(r) -> BookingFieldDTO:  # noqa: ANN001
    raw = r["options"]
    opts = [OptionDTO(value=o["value"], label=o["label"]) for o in (raw or [])]
    return BookingFieldDTO(
        field_key=r["field_key"], field_type=r["field_type"], label=r["label"], placeholder=r["placeholder"],
        required=r["required"], options=opts, position=r["position"],
    )


class BookingFieldAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def list_for(self, event_type_id: UUID) -> list[BookingFieldDTO]:
        rows = await self._sql.fetch_all(
            "SELECT field_key, field_type, label, placeholder, required, options, position "
            "FROM booking_field WHERE event_type_id = :et ORDER BY position",
            {"et": event_type_id},
        )
        return [_row_to_dto(r) for r in rows]

    async def event_type_exists(self, event_type_id: UUID) -> bool:
        row = await self._sql.fetch_one("SELECT 1 AS ok FROM event_type WHERE id = :id", {"id": event_type_id})
        return row is not None

    async def replace(
        self, event_type_id: UUID, items: list[UpsertBookingFieldDTO], keys: list[str]
    ) -> list[BookingFieldDTO]:
        await self._sql.execute("DELETE FROM booking_field WHERE event_type_id = :et", {"et": event_type_id})
        for position, (item, key) in enumerate(zip(items, keys, strict=True)):
            options_json = json.dumps([{"value": o.value, "label": o.label} for o in item.options]) if item.options else None
            await self._sql.execute(
                "INSERT INTO booking_field "
                "(event_type_id, field_key, field_type, label, placeholder, required, options, position) "
                "VALUES (:et, :k, :ft, :lbl, :ph, :req, CAST(:opts AS JSONB), :pos)",
                {"et": event_type_id, "k": key, "ft": item.field_type, "lbl": item.label,
                 "ph": item.placeholder, "req": item.required, "opts": options_json, "pos": position},
            )
        return await self.list_for(event_type_id)
```

- [ ] **Step 5: Implement `controller.py`**

```python
from uuid import UUID

from event_scheduling.booking_fields.domain import assign_keys, validate_field_items
from event_scheduling.booking_fields.dto import BookingFieldDTO, UpsertBookingFieldDTO
from event_scheduling.booking_fields.interfaces import IBookingFieldAdapter
from event_scheduling.errors import NotFoundError


class BookingFieldController:
    def __init__(self, adapter: IBookingFieldAdapter) -> None:
        self._adapter = adapter

    async def list_for(self, event_type_id: UUID) -> list[BookingFieldDTO]:
        return await self._adapter.list_for(event_type_id)

    async def replace(self, event_type_id: UUID, items: list[UpsertBookingFieldDTO]) -> list[BookingFieldDTO]:
        validate_field_items(items)
        keys = assign_keys(items)
        if not await self._adapter.event_type_exists(event_type_id):
            raise NotFoundError(f"event_type {event_type_id} not found")
        return await self._adapter.replace(event_type_id, items, keys)
```

- [ ] **Step 6: Run tests + Ruff.** `cd event-scheduling && uv run pytest tests/test_booking_fields_db.py -v && uv run ruff check event_scheduling/booking_fields && uv run ruff format --check event_scheduling/booking_fields` → all pass.

- [ ] **Step 7: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/booking_fields/interfaces.py \
        event-scheduling/event_scheduling/booking_fields/adapter.py \
        event-scheduling/event_scheduling/booking_fields/controller.py \
        event-scheduling/tests/test_booking_fields_db.py
git commit -m "feat(scheduling): booking-field DB adapter + controller (list / replace-all)"
```

---

### Task 4: Management API + event-type read exposure + DI wiring

**Files:**
- Create: `event-scheduling/event_scheduling/schemas/booking_field.py`
- Create: `event-scheduling/event_scheduling/routers/booking_field.py`
- Modify: `event-scheduling/event_scheduling/schemas/event_type.py` (add `booking_fields` to `EventTypeResponse`)
- Modify: `event-scheduling/event_scheduling/dto/event_type.py` (add `booking_fields` to `EventTypeDTO`)
- Modify: `event-scheduling/event_scheduling/adapters/event_type_db.py` (populate `booking_fields` in `get`/`_build_dto`)
- Modify: `event-scheduling/event_scheduling/ioc.py` (provide adapter + controller)
- Modify: `event-scheduling/event_scheduling/main.py` (include the router)
- Test: `event-scheduling/tests/test_booking_fields_api.py`

**Interfaces:**
- Consumes: `IBookingFieldController` (Task 3); the Pydantic patterns in `schemas/event_type.py`.
- Produces: `GET/PUT /api/v1/event-types/{id}/booking-fields`; `EventTypeResponse.booking_fields` on the existing `GET /api/v1/event-types/{id}`.

- [ ] **Step 1: Write the failing API test `test_booking_fields_api.py`**

```python
import pytest

AUTH = {"Authorization": "Bearer test-scheduling-key"}


@pytest.mark.asyncio
async def test_put_then_get_and_event_type_exposes_fields(client, seeded_event_type_id):
    body = {"items": [
        {"field_type": "textarea", "label": "Reason", "required": True},
        {"field_type": "select", "label": "Topic", "options": [{"value": "a", "label": "A"}]},
    ]}
    r = await client.put(f"/api/v1/event-types/{seeded_event_type_id}/booking-fields", json=body, headers=AUTH)
    assert r.status_code == 200
    keys = [f["field_key"] for f in r.json()["items"]]
    assert keys == ["reason", "topic"]

    g = await client.get(f"/api/v1/event-types/{seeded_event_type_id}/booking-fields", headers=AUTH)
    assert [f["field_key"] for f in g.json()["items"]] == ["reason", "topic"]

    # the event-type read now carries booking_fields
    et = await client.get(f"/api/v1/event-types/{seeded_event_type_id}", headers=AUTH)
    assert [f["field_key"] for f in et.json()["booking_fields"]] == ["reason", "topic"]


@pytest.mark.asyncio
async def test_put_invalid_option_type_is_422(client, seeded_event_type_id):
    body = {"items": [{"field_type": "select", "label": "NoOpts"}]}  # option type, no options
    r = await client.put(f"/api/v1/event-types/{seeded_event_type_id}/booking-fields", json=body, headers=AUTH)
    assert r.status_code == 422
```

Reuse the suite's real `client` + event-type-seeding fixtures (match the names used by the existing `test_*_api.py` files).

- [ ] **Step 2: Run it — expect failure.**

- [ ] **Step 3: Implement `schemas/booking_field.py`**

```python
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
        return cls(field_key=d.field_key, field_type=d.field_type, label=d.label, placeholder=d.placeholder,
                   required=d.required, options=[OptionModel(value=o.value, label=o.label) for o in d.options],
                   position=d.position)


class UpsertBookingFieldModel(BaseModel):
    field_type: str
    label: str
    placeholder: str | None = None
    required: bool = False
    options: list[OptionModel] = []

    def to_dto(self) -> UpsertBookingFieldDTO:
        return UpsertBookingFieldDTO(field_type=self.field_type, label=self.label, placeholder=self.placeholder,
                                     required=self.required,
                                     options=[OptionDTO(value=o.value, label=o.label) for o in self.options])


class BookingFieldListResponse(BaseModel):
    items: list[BookingFieldModel]


class ReplaceBookingFieldsRequest(BaseModel):
    items: list[UpsertBookingFieldModel]
```

- [ ] **Step 4: Implement `routers/booking_field.py`** (a sub-router under the event-types prefix)

```python
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends

from event_scheduling.auth import require_api_key
from event_scheduling.booking_fields.interfaces import IBookingFieldController
from event_scheduling.schemas.booking_field import (
    BookingFieldListResponse, BookingFieldModel, ReplaceBookingFieldsRequest,
)

booking_field_router = APIRouter(
    prefix="/api/v1/event-types",
    tags=["booking-fields"],
    route_class=DishkaRoute,
    dependencies=[Depends(require_api_key)],
)


@booking_field_router.get("/{event_type_id}/booking-fields", response_model=BookingFieldListResponse)
async def get_booking_fields(
    event_type_id: UUID, controller: FromDishka[IBookingFieldController]
) -> BookingFieldListResponse:
    fields = await controller.list_for(event_type_id)
    return BookingFieldListResponse(items=[BookingFieldModel.from_dto(f) for f in fields])


@booking_field_router.put("/{event_type_id}/booking-fields", response_model=BookingFieldListResponse)
async def replace_booking_fields(
    event_type_id: UUID, body: ReplaceBookingFieldsRequest, controller: FromDishka[IBookingFieldController]
) -> BookingFieldListResponse:
    fields = await controller.replace(event_type_id, [i.to_dto() for i in body.items])
    return BookingFieldListResponse(items=[BookingFieldModel.from_dto(f) for f in fields])
```

- [ ] **Step 5: Extend the event-type DTO + response + adapter to carry `booking_fields`.**
  - In `dto/event_type.py`, add to `EventTypeDTO`: `booking_fields: list[BookingFieldDTO]` (import from `booking_fields.dto`). Add `booking_fields: list = field(default_factory=list)` default so existing constructors don't break — use `from dataclasses import field`.
  - In `schemas/event_type.py`, add `booking_fields: list[BookingFieldModel]` to `EventTypeResponse` and map it in `from_dto` (`[BookingFieldModel.from_dto(f) for f in dto.booking_fields]`); import `BookingFieldModel`.
  - In `adapters/event_type_db.py`, in `get()` (where it builds the DTO), fetch the fields and pass them: add a `_fetch_booking_fields(event_type_id)` mirroring `_fetch_hosts` (SELECT the 7 columns ORDER BY position → `BookingFieldDTO`s), and include them in `_build_dto` (add a `booking_fields` param). `list_all()` may pass `booking_fields=[]` (the list endpoint doesn't need them — keep it lean).

- [ ] **Step 6: Wire DI + router.**
  - In `ioc.py`, provide `IBookingFieldAdapter` → `BookingFieldAdapter(sql)` and `IBookingFieldController` → `BookingFieldController(adapter)` in the same scope as the event-type controller (follow the existing provider style).
  - In `main.py`, `app.include_router(booking_field_router)` alongside the other routers.

- [ ] **Step 7: Run the API tests + full suite + Ruff.**

Run: `cd event-scheduling && uv run pytest tests/test_booking_fields_api.py -v && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: new API tests pass; the full existing suite stays green (the `booking_fields` default keeps existing event-type constructors working); Ruff clean.

- [ ] **Step 8: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/schemas/booking_field.py \
        event-scheduling/event_scheduling/routers/booking_field.py \
        event-scheduling/event_scheduling/schemas/event_type.py \
        event-scheduling/event_scheduling/dto/event_type.py \
        event-scheduling/event_scheduling/adapters/event_type_db.py \
        event-scheduling/event_scheduling/ioc.py event-scheduling/event_scheduling/main.py \
        event-scheduling/tests/test_booking_fields_api.py
git commit -m "feat(scheduling): booking-fields management API + expose on event-type read"
```

---

### Task 5: Booking-create accepts, validates & stores answers (+ outbox)

**Files:**
- Modify: `event-scheduling/event_scheduling/booking/dto.py` (`CreateBookingDTO.field_answers`, `BookingDTO.field_answers`)
- Modify: `event-scheduling/event_scheduling/schemas/booking.py` (`CreateBookingRequest.field_answers`, `BookingResponse.field_answers`)
- Modify: `event-scheduling/event_scheduling/routers/booking.py` (thread answers into the DTO)
- Modify: `event-scheduling/event_scheduling/booking/service.py` (validate against the type's fields; pass snapshot to the write)
- Modify: `event-scheduling/event_scheduling/booking/write_adapter.py` (persist `field_answers`; return it; include in outbox)
- Modify: `event-scheduling/event_scheduling/booking/interfaces.py` (adapter signature)
- Test: `event-scheduling/tests/test_booking_create_answers.py`

**Interfaces:**
- Consumes: `validate_and_snapshot` + `AnswerDTO`/`AnsweredFieldDTO` (Task 2); `IBookingFieldAdapter.list_for` (Task 3) — inject the booking-field adapter into `BookingService`.
- Produces: booking-create validating answers → `422` on invalid, storing the snapshot on `booking.field_answers` and echoing it in the `booking.created` outbox payload.

- [ ] **Step 1: Write the failing test `test_booking_create_answers.py`**

```python
import pytest

AUTH = {"Authorization": "Bearer test-scheduling-key"}


@pytest.mark.asyncio
async def test_booking_requires_and_stores_answers(client, bookable_event_type):
    et_id, client_user_id, a_valid_start = bookable_event_type  # fixture: seeds a host+schedule so a slot is bookable
    # configure a required textarea
    await client.put(f"/api/v1/event-types/{et_id}/booking-fields", headers=AUTH,
                     json={"items": [{"field_type": "textarea", "label": "Reason", "required": True}]})
    base = {"event_type_id": str(et_id), "client_user_id": str(client_user_id),
            "start_time": a_valid_start, "attendee_time_zone": "UTC"}

    # missing the required answer → 422
    r = await client.post("/api/v1/bookings", json=base, headers=AUTH)
    assert r.status_code == 422

    # with the answer → 201 and it's stored + echoed on the response
    r2 = await client.post("/api/v1/bookings", json={**base, "field_answers": [{"key": "reason", "value": "help"}]},
                           headers=AUTH)
    assert r2.status_code == 201
    answers = r2.json()["field_answers"]
    assert answers == [{"key": "reason", "label": "Reason", "type": "textarea", "value": "help"}]
```

Reuse/extend the suite's existing bookable-event-type fixture (the booking API tests already seed a host+schedule so `POST /bookings` can succeed — use that exact fixture; if it doesn't expose a valid `start_time`, derive one the same way those tests do).

- [ ] **Step 2: Run it — expect failure.**

- [ ] **Step 3: Extend DTOs.** In `booking/dto.py`:
  - `CreateBookingDTO`: add `field_answers: list[AnswerDTO]` (import from `booking_fields.dto`) — add a default `= field(default_factory=list)` (use `from dataclasses import field`) so existing constructors keep working.
  - `BookingDTO`: add `field_answers: list[AnsweredFieldDTO] = field(default_factory=list)`.

- [ ] **Step 4: Extend schemas.** In `schemas/booking.py`:
  - `CreateBookingRequest`: add `field_answers: list[AnswerModel] = []` where `AnswerModel(BaseModel)` has `key: str` and `value: str | list[str] | bool`.
  - `BookingResponse`: add `field_answers: list[AnsweredFieldModel]` where `AnsweredFieldModel(BaseModel)` has `key/label/type/value` with `type` aliased from the DTO's `field_type` — since the DTO uses `field_type` and the JSON key must be `type`, add `AnsweredFieldModel(key: str, label: str, type: str, value: ...)` and build it in `from_dto` mapping `type=af.field_type`. Update `BookingResponse.from_dto` to map `field_answers=[AnsweredFieldModel(key=a.key, label=a.label, type=a.field_type, value=a.value) for a in b.field_answers]` (it can no longer use `cls(**b.__dict__)` verbatim — construct explicitly).

- [ ] **Step 5: Thread answers through the router.** In `routers/booking.py` `create_booking`, build the DTO with answers:

```python
    dto = CreateBookingDTO(
        body.event_type_id, body.client_user_id, body.start_time, body.attendee_time_zone,
        field_answers=[AnswerDTO(key=a.key, value=a.value) for a in body.field_answers],
    )
```
(import `AnswerDTO`).

- [ ] **Step 6: Validate + persist in the service.** Inject `IBookingFieldAdapter` into `BookingService.__init__` (update `ioc.py` provider) and, in `create()`, after loading the bundle and before/around the insert:
  - `fields = await self._fields.list_for(dto.event_type_id)`
  - `snapshot = validate_and_snapshot(fields, dto.field_answers)` (raises `ValidationError` → 422) — do this early (right after the `bundle` load, before host search, so an invalid form fails fast).
  - pass `snapshot` to `self._write.insert(...)` (new trailing arg) and to the outbox.
  Update `booking/interfaces.py` `IBookingWriteAdapter.insert` signature to accept `field_answers: list[AnsweredFieldDTO]`.

- [ ] **Step 7: Persist in the write adapter.** In `write_adapter.py` `insert`, add `field_answers` param, serialize to JSONB, and store it; return it on the DTO:
  - Add `answers_json = json.dumps([{"key": a.key, "label": a.label, "type": a.field_type, "value": a.value} for a in field_answers])` (`import json`).
  - Add `field_answers` to the INSERT column list + `CAST(:fa AS JSONB)` value; add `"fa": answers_json` to params; add `field_answers` to `_COLS`; in `_row_to_dto`, parse `r["field_answers"]` back into `list[AnsweredFieldDTO]` (`AnsweredFieldDTO(key=x["key"], label=x["label"], field_type=x["type"], value=x["value"])`).
  - In `BookingService.create`, the outbox write already sends `booking`; add the snapshot to the payload — extend `self._outbox.write("booking.created", booking)` so the emitted payload includes `field_answers` (mirror how the outbox serializes a booking; if `IOutboxWriter.write` builds the payload from the `BookingDTO`, the `BookingDTO.field_answers` now flows automatically — verify the outbox payload builder includes it, and if it whitelists fields, add `field_answers`).

- [ ] **Step 8: Run the new test + full suite + Ruff.**

Run: `cd event-scheduling && uv run pytest tests/test_booking_create_answers.py -v && uv run pytest -q && uv run ruff check . && uv run ruff format --check .`
Expected: new test passes; full suite green (defaults keep old constructors valid); Ruff clean.

- [ ] **Step 9: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/booking/ event-scheduling/event_scheduling/schemas/booking.py \
        event-scheduling/event_scheduling/routers/booking.py event-scheduling/event_scheduling/ioc.py \
        event-scheduling/tests/test_booking_create_answers.py
git commit -m "feat(scheduling): booking-create validates + stores field answers (snapshot + outbox)"
```

---

## Notes for the executor

- **Branch:** create `feat/booking-fields-p1` in the root `events` repo before Task 1 (off the current tip).
- **Fixtures:** this plan reuses the suite's existing fixtures (DB session, `SqlExecutor`, `client`, event-type/host/bookable seeds). Before Task 1, read `event-scheduling/tests/conftest.py` and one existing `test_*_db.py` + `test_*_api.py` to learn their exact names, and use those — do not invent fixture names. If a needed seed (e.g. a bookable event type exposing a valid `start_time`) isn't already a fixture, extend `conftest.py` minimally in Task 5.
- **DTO defaults:** every new DTO/schema field added to an existing type gets a default (`field(default_factory=list)` / `= []`) so untouched call sites and the full existing suite stay green — this is what keeps each task's "full suite" step passing.
- **`type` vs `field_type`:** the DTO field is `field_type` (Python), but the JSON/snapshot key is `type`. Keep that mapping consistent in `write_adapter` (stores `"type"`), the snapshot (`AnsweredFieldDTO.field_type`), and `AnsweredFieldModel` (JSON `type`).
- **Phase boundary:** this plan ends with event-scheduling fully able to store/validate fields+answers via API. Phase 2 (event-booker BFF + booker-frontend rendering) and Phase 3 (event-admin proxy + admin-frontend editor) are separate plans, written after this merges.
