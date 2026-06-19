# event-db-sync Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the HTTP CRM poll with a trigger-driven sync — a new `event-db-sync` service listens to cal.com DB changes via `pg_notify`, publishes `user.upserted` CloudEvents directly to RabbitMQ; `event-users` upserts the user and emits `user.synced`; `event-saver` backfills `bookings.{organizer,client}_user_id` by email.

**Architecture:** cal.com trigger → `pg_notify('user_sync', {table,id})` → `event-db-sync` (asyncpg `LISTEN`) re-SELECTs the row → publishes `user.upserted` (priority CRITICAL) → `events.user.email` queue → `event-users` upserts (returns user_id) → publishes `user.synced` → new `events.user.synced` queue → `event-saver` UPDATEs bookings by email. A watermark reconcile sweep (own Postgres DB) covers missed NOTIFYs and the cutover backfill; a `POST /admin/full-sync` endpoint forces a full pass with cal.com as source of truth.

**Tech Stack:** Python 3.14, FastAPI, Dishka, FastStream `RabbitBroker`, raw `asyncpg` for `LISTEN`, SQLAlchemy async + Alembic for the own DB, Pydantic v2 / pydantic-settings, `cloudevents`, pytest. Four git repos: `event-schemas`, `event-db-sync` (new), `event-users`, `event-saver`.

**Spec:** `docs/superpowers/specs/2026-06-19-event-db-sync-design.md`

---

## Conventions every task must follow

- **Per-repo commits.** Each repo is its own git repository. `cd` into the repo dir before `git add`/`commit`. Commit messages end with the trailer:
  ```
  Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
  ```
- **Do NOT push** unless the controller says so (tag pushes + branch pushes are explicit user-approved actions). Local commits and a local `git tag` are fine; pushing the tag is the final, separately-approved step.
- **No `elif`; avoid `else`** — early returns, guard clauses, mapping dicts (project rule).
- **Ruff** line length 120; run `uv run ruff check --fix . && uv run ruff format .` before each commit.
- **Tests are TDD**: write the failing test, run it red, implement, run it green, commit.
- **event-schemas dependency during development:** dependents (`event-users`, `event-saver`, `event-db-sync`) temporarily use `event-schemas = { path = "../event-schemas", editable = true }` in `[tool.uv.sources]` so you can develop+test against the unreleased schema. The final task (Task 21) flips all of them to the released `tag = "v0.4.0"`.

## Canonical names (use these EXACT strings everywhere)

| Thing | Value |
|---|---|
| New service package dir | `event_db_sync/` (repo `event-db-sync/`) |
| `user.upserted` event type | `EventType.USER_UPSERTED = "user.upserted"` |
| `user.synced` event type | `EventType.USER_SYNCED = "user.synced"` |
| `user.upserted` CloudEvent source | `"db-sync"` |
| `user.synced` CloudEvent source | `"event-users"` |
| New routing key | `RoutingKey.USER_SYNCED = "events.user.synced"` |
| `user.upserted` routing | reuses existing `RoutingKey.USER_EMAIL` (`events.user.email`) |
| New saver queue | `USER_SYNCED_QUEUE` name `"events.user.synced"`, consumer `"event-saver"` |
| Roles | `Attendee → "client"`, cal.com `users → "organizer"` |
| event-schemas new version / tag | `0.4.0` / `v0.4.0` |
| NOTIFY channel | `user_sync` |

---

# PHASE 1 — event-schemas (repo `event-schemas/`)

Foundation. Develop on a branch; the tag is created in Task 4 and pushed only when approved.

### Task 1: Add the two new EventType members, priorities, and schema versions

**Files:**
- Modify: `event_schemas/types.py`
- Test: `tests/test_payload_contracts.py` (existing completeness tests will guard this)

- [ ] **Step 1: Write the failing test** — append to `tests/test_types.py` (create if absent):

```python
from event_schemas.types import EVENT_PRIORITIES, EVENT_SCHEMA_VERSIONS, EventPriority, EventType


def test_user_upserted_and_synced_are_critical() -> None:
    assert EVENT_PRIORITIES[EventType.USER_UPSERTED] == EventPriority.CRITICAL
    assert EVENT_PRIORITIES[EventType.USER_SYNCED] == EventPriority.CRITICAL


def test_user_upserted_and_synced_have_schema_versions() -> None:
    assert EVENT_SCHEMA_VERSIONS[EventType.USER_UPSERTED] == "v1"
    assert EVENT_SCHEMA_VERSIONS[EventType.USER_SYNCED] == "v1"
```

- [ ] **Step 2: Run it red**

Run: `cd event-schemas && uv run pytest tests/test_types.py -v`
Expected: FAIL — `AttributeError: USER_UPSERTED`.

- [ ] **Step 3: Implement** — in `event_schemas/types.py`, under the `# User management` comment in `EventType` (next to `USER_EMAIL_CHANGE_REQUESTED`) add:

```python
    USER_UPSERTED = "user.upserted"
    USER_SYNCED = "user.synced"
```

In `EVENT_PRIORITIES`, beside `EventType.USER_EMAIL_CHANGE_REQUESTED: EventPriority.CRITICAL,` add:

```python
    EventType.USER_UPSERTED: EventPriority.CRITICAL,
    EventType.USER_SYNCED: EventPriority.CRITICAL,
```

In `EVENT_SCHEMA_VERSIONS`, beside `EventType.USER_EMAIL_CHANGE_REQUESTED: "v1",` add:

```python
    EventType.USER_UPSERTED: "v1",
    EventType.USER_SYNCED: "v1",
```

- [ ] **Step 4: Run green**

Run: `cd event-schemas && uv run pytest tests/test_types.py tests/test_payload_contracts.py -v`
Expected: PASS (the existing `test_every_event_type_has_priority` / `_schema_version` completeness tests also stay green).

- [ ] **Step 5: Commit**

```bash
cd event-schemas
git add event_schemas/types.py tests/test_types.py
git commit -m "feat: add user.upserted/user.synced event types + priorities

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Add the payload models and register them

**Files:**
- Modify: `event_schemas/user.py`
- Modify: `event_schemas/mapping.py` (the `PAYLOAD_MODELS` registry)
- Test: `tests/test_user_payloads.py` (create)

- [ ] **Step 1: Write the failing test** — `tests/test_user_payloads.py`:

```python
import pytest
from pydantic import ValidationError

from event_schemas.user import UserContactPayload, UserSyncedPayload, UserUpsertedPayload


def test_user_upserted_minimal() -> None:
    p = UserUpsertedPayload(email="a@b.c", role="client", time_zone=None, name=None)
    assert p.email == "a@b.c"
    assert p.contacts == []


def test_user_upserted_with_contacts() -> None:
    p = UserUpsertedPayload(
        email="a@b.c",
        role="organizer",
        time_zone="Europe/Moscow",
        name="Org",
        contacts=[UserContactPayload(channel="email", contact_id="a@b.c")],
    )
    assert p.contacts[0].channel == "email"


def test_user_synced_requires_uuid() -> None:
    ok = UserSyncedPayload(
        email="a@b.c", role="client", user_id="550e8400-e29b-41d4-a716-446655440001", time_zone=None
    )
    assert ok.role == "client"
    with pytest.raises(ValidationError):
        UserSyncedPayload(email="a@b.c", role="client", user_id="not-a-uuid", time_zone=None)
```

- [ ] **Step 2: Run it red**

Run: `cd event-schemas && uv run pytest tests/test_user_payloads.py -v`
Expected: FAIL — `ImportError: cannot import name 'UserUpsertedPayload'`.

- [ ] **Step 3: Implement** — append to `event_schemas/user.py`:

```python
class UserContactPayload(BaseModel):
    """A single contact channel for a synced user."""

    channel: str = Field(..., description="Contact channel, e.g. 'email' or 'telegram'")
    contact_id: str = Field(..., description="Channel-specific identifier")


class UserUpsertedPayload(BaseModel):
    """Payload for user.upserted — a cal.com row mapped to a user (source of truth: cal.com)."""

    email: EmailStr = Field(..., description="User email (unique within a role)")
    role: str = Field(..., description="'client' (cal.com Attendee) or 'organizer' (cal.com users)")
    time_zone: str | None = Field(None, description="IANA time zone from cal.com, or null")
    name: str | None = Field(None, description="Display name from cal.com, or null")
    contacts: list[UserContactPayload] = Field(default_factory=list, description="Extra contact channels")

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "client@example.com",
                "role": "client",
                "time_zone": "Europe/Moscow",
                "name": "Jane Client",
                "contacts": [{"channel": "email", "contact_id": "client@example.com"}],
            }
        }
    }


class UserSyncedPayload(BaseModel):
    """Payload for user.synced — event-users announces the resolved user_id for a synced user."""

    email: EmailStr = Field(..., description="User email")
    role: str = Field(..., description="'client' or 'organizer'")
    user_id: UuidStr = Field(..., description="UUID assigned by event-users")
    time_zone: str | None = Field(None, description="IANA time zone, or null")

    model_config = {
        "json_schema_extra": {
            "example": {
                "email": "client@example.com",
                "role": "client",
                "user_id": "550e8400-e29b-41d4-a716-446655440001",
                "time_zone": "Europe/Moscow",
            }
        }
    }
```

- [ ] **Step 4: Register in `PAYLOAD_MODELS`** — in `event_schemas/mapping.py`, import the two models and add entries:

```python
from event_schemas.user import (
    BookingClientReassignedPayload,
    UserEmailChangeRequestedPayload,
    UserSyncedPayload,
    UserUpsertedPayload,
)
```
and inside the `PAYLOAD_MODELS` dict:
```python
    EventType.USER_UPSERTED: UserUpsertedPayload,
    EventType.USER_SYNCED: UserSyncedPayload,
```

- [ ] **Step 5: Run green** (incl. the parametrized envelope round-trip that now covers the new models)

Run: `cd event-schemas && uv run pytest tests/test_user_payloads.py tests/test_payload_contracts.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd event-schemas
git add event_schemas/user.py event_schemas/mapping.py tests/test_user_payloads.py
git commit -m "feat: add UserUpsertedPayload/UserSyncedPayload/UserContactPayload

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Add the routing key, the saver queue, and the routing rules

**Files:**
- Modify: `event_schemas/queues.py`
- Test: `tests/test_queues.py`

- [ ] **Step 1: Write the failing test** — append to `tests/test_queues.py`:

```python
from event_schemas.queues import (
    ALL_QUEUES,
    ROUTING_RULES,
    SAVER_QUEUES,
    USER_SYNCED_QUEUE,
    RoutingKey,
)


def test_user_synced_queue_is_saver_owned() -> None:
    assert USER_SYNCED_QUEUE.name == "events.user.synced"
    assert USER_SYNCED_QUEUE.binding == RoutingKey.USER_SYNCED
    assert USER_SYNCED_QUEUE in ALL_QUEUES
    assert USER_SYNCED_QUEUE in SAVER_QUEUES  # consumer == "event-saver"


def test_sync_routing_rules_exist() -> None:
    rules = {(r.source_pattern, r.type_pattern): r.destination for r in ROUTING_RULES}
    assert rules[("db-sync", "user.upserted")] == RoutingKey.USER_EMAIL
    assert rules[("event-users", "user.synced")] == RoutingKey.USER_SYNCED
```

- [ ] **Step 2: Run it red**

Run: `cd event-schemas && uv run pytest tests/test_queues.py -v`
Expected: FAIL — `ImportError: USER_SYNCED_QUEUE` / `RoutingKey.USER_SYNCED`.

- [ ] **Step 3: Implement** — in `event_schemas/queues.py`:

Add to the `RoutingKey` StrEnum (after `USER_EMAIL`):
```python
    USER_SYNCED = "events.user.synced"
```

Add the queue constant near `USER_EMAIL_QUEUE`:
```python
USER_SYNCED_QUEUE = QueueSpec(
    name="events.user.synced",
    binding=RoutingKey.USER_SYNCED,
    consumer="event-saver",
)
```

Append `USER_SYNCED_QUEUE,` to the `ALL_QUEUES` tuple.

Add to `ROUTING_RULES` (beside the existing `RoutingRuleSpec(RoutingKey.USER_EMAIL, "admin", "user.email.*")`):
```python
    RoutingRuleSpec(RoutingKey.USER_EMAIL, "db-sync", "user.upserted"),
    RoutingRuleSpec(RoutingKey.USER_SYNCED, "event-users", "user.synced"),
```

- [ ] **Step 4: Run green** (incl. `test_every_routing_rule_destination_has_a_bound_queue`, `test_queue_names_are_unique`, `test_canonical_queue_arguments`)

Run: `cd event-schemas && uv run pytest tests/test_queues.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd event-schemas
git add event_schemas/queues.py tests/test_queues.py
git commit -m "feat: add events.user.synced queue + db-sync/user-synced routing rules

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: Re-export new symbols, bump version, tag

**Files:**
- Modify: `event_schemas/__init__.py`
- Modify: `pyproject.toml`

- [ ] **Step 1: Re-export** — in `event_schemas/__init__.py` add the new symbols to the import block and `__all__`: `UserContactPayload`, `UserUpsertedPayload`, `UserSyncedPayload` (from `.user`), `USER_SYNCED_QUEUE`, `RoutingKey.USER_SYNCED` is reached via `RoutingKey` already. Also **fix the stale `__version__`** — set:
```python
__version__ = "0.4.0"
```

- [ ] **Step 2: Bump pyproject** — in `pyproject.toml` set:
```toml
version = "0.4.0"
```

- [ ] **Step 3: Full test suite + lint**

Run: `cd event-schemas && uv run ruff check --fix . && uv run ruff format . && uv run pytest -q`
Expected: all PASS.

- [ ] **Step 4: Commit + local tag** (push is deferred to the controller's approval)

```bash
cd event-schemas
git add event_schemas/__init__.py pyproject.toml
git commit -m "chore: release event-schemas 0.4.0 (user sync events)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
git tag v0.4.0
```

> **Controller note:** the tag must be pushed (`git push origin main --tags`) before the dependents' `tag = "v0.4.0"` resolves remotely. That push is a separately-approved action (Task 21). Until then, dependents use the editable path source.

---

# PHASE 2 — event-db-sync (NEW repo `event-db-sync/`)

The new service. Scaffold from `event-shortener` (simplest own-DB skeleton) and lift the broker + background-task + concurrency-safe `SqlExecutor` patterns from `event-notifier`.

### Task 5: Scaffold the service from event-shortener

**Files:**
- Create: the `event-db-sync/` repo by copying `event-shortener/` and renaming.

- [ ] **Step 1: Copy + rename** (run from repo root `/Users/alexandrlelikov/PycharmProjects/events`):

```bash
cp -R event-shortener event-db-sync
cd event-db-sync
rm -rf .git .venv event_shortener/__pycache__ uv.lock
git init -q
git mv event_shortener event_db_sync
# rename every code reference
grep -rl --binary-files=without-match 'event_shortener\|event-shortener' . \
  | grep -v '/\.git/' \
  | xargs sed -i '' -e 's/event_shortener/event_db_sync/g; s/event-shortener/event-db-sync/g'
```

- [ ] **Step 2: Strip shortener-specific domain files** that won't be reused:

```bash
cd event-db-sync
git rm -q event_db_sync/adapters/short_url_db.py event_db_sync/dto/short_url.py event_db_sync/schemas/*.py \
  event_db_sync/pages.py 2>/dev/null || true
rm -f event_db_sync/adapters/short_url_db.py event_db_sync/dto/short_url.py event_db_sync/pages.py
rm -rf event_db_sync/schemas
rm -f alembic/versions/0001_initial.py alembic/versions/0002_click_count.py
rm -f tests/test_*.py
```

(Keep `adapters/sql.py`, `db/base.py`, `metrics.py`, `logger.py`, `telemetry.py`, `errors.py`, `routes.py` ops router, `main.py`, `config.py`, `ioc.py`, `auth.py`, alembic `env.py`/`script.py.mako`, `Dockerfile`, `entrypoint.sh`, CI files.)

- [ ] **Step 3: Rewrite `pyproject.toml`** — `event-db-sync/pyproject.toml`. Start from the copied shortener file and **add** the broker + schemas deps and the source pin. The `[project]` block:

```toml
[project]
name = "event-db-sync"
version = "0.1.0"
description = "Trigger-driven DB sync: cal.com -> event-users via pg_notify + RabbitMQ"
requires-python = ">=3.14"
dependencies = [
    "alembic>=1.16.0",
    "asyncpg>=0.31.0",
    "cloudevents>=1.11.0",
    "dishka>=1.8.0",
    "event-schemas",
    "faststream[rabbit]>=0.5.0",
    "fastapi>=0.135.1",
    "greenlet>=3.2.4",
    "opentelemetry-sdk>=1.30.0",
    "opentelemetry-exporter-otlp-proto-grpc>=1.30.0",
    "opentelemetry-instrumentation-fastapi>=0.51b0",
    "opentelemetry-instrumentation-asyncpg>=0.51b0",
    "prometheus-client>=0.25.0",
    "pydantic-settings>=2.13.1",
    "sqlalchemy>=2.0.48",
    "structlog>=25.5.0",
    "uvicorn>=0.41.0",
]

[tool.uv.sources]
# Local dev: editable path. Task 21 flips this to tag = "v0.4.0".
event-schemas = { path = "../event-schemas", editable = true }
```

(Confirm the exact `faststream` version pin against `event-notifier/pyproject.toml` and match it.) Keep the copied `[dependency-groups]`, `[tool.ruff]`, `[tool.pytest.ini_options]` blocks.

- [ ] **Step 4: Sync deps**

Run: `cd event-db-sync && uv lock && uv sync`
Expected: resolves with the editable event-schemas path.

- [ ] **Step 5: Commit**

```bash
cd event-db-sync
git add -A
git commit -m "chore: scaffold event-db-sync from event-shortener skeleton

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Configuration

**Files:**
- Modify: `event_db_sync/config.py`
- Test: `tests/test_config.py` (create)

- [ ] **Step 1: Write the failing test** — `tests/test_config.py`:

```python
from event_db_sync.config import Settings


def test_settings_load_from_env(monkeypatch) -> None:
    monkeypatch.setenv("DATABASE_URL", "postgresql+asyncpg://u:p@localhost:5432/dbsync")
    monkeypatch.setenv("CALCOM_DATABASE_URL", "postgresql://u:p@localhost:5432/calcom")
    monkeypatch.setenv("RABBIT_URL", "amqp://guest:guest@localhost:5672/")
    monkeypatch.setenv("SYNC_ADMIN_TOKEN", "secret")
    s = Settings()
    assert s.rabbit_exchange == "events"
    assert s.reconcile_interval_seconds == 300.0
    assert s.full_sync_batch_size == 500
    assert s.calcom_database_url.startswith("postgresql://")
```

- [ ] **Step 2: Run it red**

Run: `cd event-db-sync && uv run pytest tests/test_config.py -v`
Expected: FAIL (fields missing / import error).

- [ ] **Step 3: Implement** — replace `event_db_sync/config.py` body of `Settings`:

```python
from functools import lru_cache

from pydantic import AmqpDsn, Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    debug: bool = False
    log_level: str = "INFO"

    # Own DB (sync_state). SQLAlchemy asyncpg DSN.
    database_url: PostgresDsn = Field(strict=True)
    # cal.com DB. Plain asyncpg DSN (NOT the +asyncpg SQLAlchemy form) for raw LISTEN/SELECT.
    calcom_database_url: str = Field(...)

    rabbit_url: AmqpDsn = "amqp://guest:guest@localhost:5672/"
    rabbit_exchange: str = "events"
    publish_timeout: float = 10.0

    apply_triggers: bool = True
    notify_channel: str = "user_sync"

    reconcile_enabled: bool = True
    reconcile_interval_seconds: float = 300.0

    sync_admin_token: str = Field(...)
    full_sync_batch_size: int = 500
    full_sync_batch_pause_seconds: float = 0.1

    @field_validator("log_level")
    @classmethod
    def _validate_log_level(cls, v: str) -> str:
        valid = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid:
            raise ValueError(f"log_level must be one of {valid}")
        return upper


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: Run green**

Run: `cd event-db-sync && uv run pytest tests/test_config.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd event-db-sync
git add event_db_sync/config.py tests/test_config.py
git commit -m "feat: event-db-sync settings (calcom DSN, rabbit, reconcile, full-sync)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Own-DB `sync_state` model + Alembic migration

**Files:**
- Create: `event_db_sync/db/models.py` (replace copied one)
- Create: `alembic/versions/0001_sync_state.py`
- Test: `tests/test_models.py` (create)

- [ ] **Step 1: Write the failing test** — `tests/test_models.py`:

```python
from event_db_sync.db.models import SyncState


def test_sync_state_table_shape() -> None:
    cols = {c.name for c in SyncState.__table__.columns}
    assert {"source", "last_id", "last_updated_at", "updated_at"} <= cols
    assert SyncState.__table__.primary_key.columns.keys() == ["source"]
```

- [ ] **Step 2: Run it red**

Run: `cd event-db-sync && uv run pytest tests/test_models.py -v`
Expected: FAIL — import error.

- [ ] **Step 3: Implement model** — `event_db_sync/db/models.py`:

```python
from sqlalchemy import BigInteger, DateTime, Text, text
from sqlalchemy.orm import Mapped, mapped_column

from event_db_sync.db.base import Base


class SyncState(Base):
    """One watermark row per cal.com source table ('attendee' | 'users')."""

    __tablename__ = "sync_state"

    source: Mapped[str] = mapped_column(Text, primary_key=True)
    last_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    last_updated_at: Mapped[object | None] = mapped_column(DateTime(timezone=True), nullable=True)
    updated_at: Mapped[object] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
```

- [ ] **Step 4: Implement migration** — `alembic/versions/0001_sync_state.py` (copy the revision-header style from any event-notifier migration; `down_revision = None`):

```python
"""sync_state watermark table

Revision ID: 0001_sync_state
Revises:
Create Date: 2026-06-19
"""

import sqlalchemy as sa
from alembic import op

revision = "0001_sync_state"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "sync_state",
        sa.Column("source", sa.Text(), primary_key=True, nullable=False),
        sa.Column("last_id", sa.BigInteger(), nullable=True),
        sa.Column("last_updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
    )


def downgrade() -> None:
    op.drop_table("sync_state")
```

- [ ] **Step 5: Run green**

Run: `cd event-db-sync && uv run pytest tests/test_models.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd event-db-sync
git add event_db_sync/db/models.py alembic/versions/0001_sync_state.py tests/test_models.py
git commit -m "feat: sync_state model + alembic migration

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Concurrency-safe own-DB SqlExecutor + interface

**Files:**
- Modify: `event_db_sync/adapters/sql.py` (replace with the event-notifier APP-scoped flavour)
- Create: `event_db_sync/interfaces/sql.py`
- Test: `tests/test_sql.py` (create, sqlite-backed)

- [ ] **Step 1: Write the failing test** — `tests/test_sql.py`:

```python
import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from event_db_sync.adapters.sql import SqlExecutor


@pytest.fixture
async def sessionmaker():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    maker = async_sessionmaker(bind=engine, expire_on_commit=False)
    async with engine.begin() as conn:
        await conn.exec_driver_sql("CREATE TABLE t (id INTEGER PRIMARY KEY, v TEXT)")
    yield maker
    await engine.dispose()


async def test_execute_and_fetch(sessionmaker) -> None:
    ex = SqlExecutor(sessionmaker)
    await ex.execute("INSERT INTO t (id, v) VALUES (:id, :v)", {"id": 1, "v": "x"})
    row = await ex.fetch_one("SELECT v FROM t WHERE id = :id", {"id": 1})
    assert row["v"] == "x"
    rows = await ex.fetch_all("SELECT v FROM t", {})
    assert len(rows) == 1
```

- [ ] **Step 2: Run it red**

Run: `cd event-db-sync && uv run pytest tests/test_sql.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement** — `event_db_sync/interfaces/sql.py`:

```python
from typing import Protocol

from sqlalchemy.engine import RowMapping


class ISqlExecutor(Protocol):
    async def fetch_one(self, query: str, values: dict) -> RowMapping | None: ...
    async def fetch_all(self, query: str, values: dict) -> list[RowMapping]: ...
    async def execute(self, query: str, values: dict) -> None: ...
```

`event_db_sync/adapters/sql.py`:

```python
from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


class SqlExecutor:
    """Concurrency-safe: a fresh session + transaction per operation."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def fetch_one(self, query: str, values: dict) -> RowMapping | None:
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(text(query), values)
            return result.mappings().first()

    async def fetch_all(self, query: str, values: dict) -> list[RowMapping]:
        async with self._sessionmaker() as session, session.begin():
            result = await session.execute(text(query), values)
            return list(result.mappings().all())

    async def execute(self, query: str, values: dict) -> None:
        async with self._sessionmaker() as session, session.begin():
            await session.execute(text(query), values)
```

- [ ] **Step 4: Run green** (ensure `aiosqlite` is in dev deps — it is in the copied skeleton)

Run: `cd event-db-sync && uv run pytest tests/test_sql.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd event-db-sync
git add event_db_sync/adapters/sql.py event_db_sync/interfaces/sql.py tests/test_sql.py
git commit -m "feat: concurrency-safe own-DB SqlExecutor

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: cal.com row reader + role mapper (pure mapping unit)

This task isolates the *mapping* logic (cal.com row dict → `UserUpsertedPayload`) so it is unit-testable without a DB. The actual SELECTs live in Task 12/13.

**Files:**
- Create: `event_db_sync/domain/mapping.py`
- Test: `tests/test_calcom_mapping.py` (create)

- [ ] **Step 1: Write the failing test** — `tests/test_calcom_mapping.py`:

```python
from event_db_sync.domain.mapping import SOURCE_ATTENDEE, SOURCE_USERS, map_row_to_payload


def test_attendee_maps_to_client() -> None:
    row = {"email": "C@Ex.com", "name": "Jane", "timeZone": "Europe/Moscow"}
    p = map_row_to_payload(SOURCE_ATTENDEE, row)
    assert p.role == "client"
    assert p.email == "c@ex.com"  # normalized lowercase
    assert p.time_zone == "Europe/Moscow"
    assert p.name == "Jane"


def test_users_maps_to_organizer() -> None:
    row = {"email": "org@ex.com", "name": "Org", "timeZone": None}
    p = map_row_to_payload(SOURCE_USERS, row)
    assert p.role == "organizer"
    assert p.time_zone is None


def test_unknown_source_raises() -> None:
    import pytest

    with pytest.raises(ValueError, match="unknown source"):
        map_row_to_payload("nope", {"email": "a@b.c"})
```

- [ ] **Step 2: Run it red**

Run: `cd event-db-sync && uv run pytest tests/test_calcom_mapping.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement** — `event_db_sync/domain/mapping.py`:

```python
from event_schemas.user import UserUpsertedPayload

SOURCE_ATTENDEE = "attendee"
SOURCE_USERS = "users"

_ROLE_BY_SOURCE = {SOURCE_ATTENDEE: "client", SOURCE_USERS: "organizer"}


def map_row_to_payload(source: str, row: dict) -> UserUpsertedPayload:
    role = _ROLE_BY_SOURCE.get(source)
    if role is None:
        raise ValueError(f"unknown source: {source}")
    email = str(row["email"]).strip().lower()
    return UserUpsertedPayload(
        email=email,
        role=role,
        time_zone=row.get("timeZone"),
        name=row.get("name"),
        contacts=[],
    )
```

- [ ] **Step 4: Run green**

Run: `cd event-db-sync && uv run pytest tests/test_calcom_mapping.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd event-db-sync
git add event_db_sync/domain/mapping.py tests/test_calcom_mapping.py
git commit -m "feat: cal.com row -> UserUpsertedPayload mapping (attendee=client, users=organizer)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Direct-to-RabbitMQ CloudEvent publisher

**Files:**
- Create: `event_db_sync/adapters/publisher.py`
- Test: `tests/test_publisher.py` (create, fake broker)

- [ ] **Step 1: Write the failing test** — `tests/test_publisher.py`:

```python
import json

from event_schemas.user import UserUpsertedPayload

from event_db_sync.adapters.publisher import UserUpsertedPublisher


class FakeBroker:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def publish(self, body, **kwargs) -> None:
        self.calls.append({"body": body, **kwargs})


async def test_publish_builds_binary_cloudevent() -> None:
    broker = FakeBroker()
    pub = UserUpsertedPublisher(broker=broker, exchange="events", publish_timeout=5.0)
    payload = UserUpsertedPayload(email="a@b.c", role="client", time_zone="UTC", name="N")
    await pub.publish(payload)
    assert len(broker.calls) == 1
    call = broker.calls[0]
    assert call["routing_key"] == "events.user.email"
    assert call["priority"] == 10  # CRITICAL
    assert call["headers"]["ce-type"] == "user.upserted"
    assert call["headers"]["ce-source"] == "db-sync"
    body = json.loads(call["body"])
    assert body["original"]["email"] == "a@b.c"
    assert body["normalized"]["participants"] == []


async def test_publish_deterministic_id_is_stable() -> None:
    broker = FakeBroker()
    pub = UserUpsertedPublisher(broker=broker, exchange="events", publish_timeout=5.0)
    payload = UserUpsertedPayload(email="a@b.c", role="client", time_zone="UTC", name="N")
    await pub.publish(payload, dedupe_key="attendee:7:2026-06-19T00:00:00Z")
    await pub.publish(payload, dedupe_key="attendee:7:2026-06-19T00:00:00Z")
    assert broker.calls[0]["headers"]["ce-id"] == broker.calls[1]["headers"]["ce-id"]
```

- [ ] **Step 2: Run it red**

Run: `cd event-db-sync && uv run pytest tests/test_publisher.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement** — `event_db_sync/adapters/publisher.py`:

```python
import json
import uuid
from typing import Protocol

from cloudevents.conversion import to_binary
from cloudevents.http import CloudEvent
from event_schemas.queues import RoutingKey
from event_schemas.types import EVENT_PRIORITIES, EventType
from event_schemas.user import UserUpsertedPayload

_SOURCE = "db-sync"
_EVENT_TYPE = EventType.USER_UPSERTED
_NAMESPACE = uuid.UUID("0b6d2c2e-9f3a-5e7b-8c1d-2f4a6b8c0e10")


class IBroker(Protocol):
    async def publish(self, body: bytes, **kwargs: object) -> None: ...


class UserUpsertedPublisher:
    def __init__(self, broker: IBroker, exchange: str, publish_timeout: float) -> None:
        self._broker = broker
        self._exchange = exchange
        self._timeout = publish_timeout

    async def publish(self, payload: UserUpsertedPayload, dedupe_key: str | None = None) -> None:
        envelope = {"original": payload.model_dump(mode="json"), "normalized": {"participants": []}}
        key = dedupe_key or f"{payload.role}:{payload.email}"
        event = CloudEvent(
            {
                "type": str(_EVENT_TYPE),
                "source": _SOURCE,
                "id": str(uuid.uuid5(_NAMESPACE, key)),
                "specversion": "1.0",
            },
            json.dumps(envelope).encode(),
        )
        message = to_binary(event, None)
        headers = dict(message[0])
        headers["content-type"] = "application/json"
        await self._broker.publish(
            message[1],
            exchange=self._exchange,
            routing_key=str(RoutingKey.USER_EMAIL),
            headers=headers,
            content_type="application/json",
            message_type=str(_EVENT_TYPE),
            priority=EVENT_PRIORITIES[_EVENT_TYPE].value,
            timeout=self._timeout,
        )
```

> Note: `to_binary(event, None)` returns `(headers, body)`. If the installed `cloudevents` version returns a message object instead, mirror event-receiver's `to_binary(event, JSONFormat())` + `message.headers`/`message.body` form — confirm against `event-receiver/event_receiver/adapters/publisher.py` and match exactly. The test asserts the observable contract either way.

- [ ] **Step 4: Run green**

Run: `cd event-db-sync && uv run pytest tests/test_publisher.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd event-db-sync
git add event_db_sync/adapters/publisher.py tests/test_publisher.py
git commit -m "feat: direct-to-RabbitMQ user.upserted publisher (CRITICAL, deterministic ce-id)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Idempotent trigger DDL applier

**Files:**
- Create: `event_db_sync/adapters/calcom_triggers.py`
- Test: `tests/test_calcom_triggers.py` (assert the SQL text; live apply is integration-covered in Task 16/manual)

- [ ] **Step 1: Write the failing test** — `tests/test_calcom_triggers.py`:

```python
from event_db_sync.adapters.calcom_triggers import TRIGGER_DDL, trigger_statements


def test_ddl_is_idempotent_and_notifies_channel() -> None:
    sql = TRIGGER_DDL
    assert "CREATE OR REPLACE FUNCTION" in sql
    assert "pg_notify('user_sync'" in sql
    # idempotent trigger (re)creation
    assert 'DROP TRIGGER IF EXISTS user_sync_attendee ON "Attendee"' in sql
    assert 'DROP TRIGGER IF EXISTS user_sync_users ON "users"' in sql


def test_trigger_statements_split_executable() -> None:
    stmts = trigger_statements()
    assert len(stmts) >= 3
    assert all(s.strip() for s in stmts)
```

- [ ] **Step 2: Run it red**

Run: `cd event-db-sync && uv run pytest tests/test_calcom_triggers.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement** — `event_db_sync/adapters/calcom_triggers.py`:

```python
"""Idempotent cal.com trigger DDL.

Sanctioned additive integration hook (NOT a cal.com schema migration): it only
adds a NOTIFY function + AFTER triggers and never alters cal.com tables/columns.
"""

TRIGGER_DDL = """
CREATE OR REPLACE FUNCTION user_sync_notify() RETURNS trigger AS $$
BEGIN
  PERFORM pg_notify(
    'user_sync',
    json_build_object('table', TG_TABLE_NAME, 'id', NEW.id)::text
  );
  RETURN NEW;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS user_sync_attendee ON "Attendee";
CREATE TRIGGER user_sync_attendee
  AFTER INSERT OR UPDATE ON "Attendee"
  FOR EACH ROW EXECUTE FUNCTION user_sync_notify();

DROP TRIGGER IF EXISTS user_sync_users ON "users";
CREATE TRIGGER user_sync_users
  AFTER INSERT OR UPDATE ON "users"
  FOR EACH ROW EXECUTE FUNCTION user_sync_notify();
"""


def trigger_statements() -> list[str]:
    return [s.strip() + ";" for s in TRIGGER_DDL.strip().split(";") if s.strip()]


async def apply_triggers(conn) -> None:
    """Apply the DDL on a raw asyncpg connection (idempotent)."""
    await conn.execute(TRIGGER_DDL)
```

- [ ] **Step 4: Run green**

Run: `cd event-db-sync && uv run pytest tests/test_calcom_triggers.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd event-db-sync
git add event_db_sync/adapters/calcom_triggers.py tests/test_calcom_triggers.py
git commit -m "feat: idempotent cal.com NOTIFY trigger DDL

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: cal.com reader (raw asyncpg) + the LISTEN listener

**Files:**
- Create: `event_db_sync/adapters/calcom_reader.py` (raw asyncpg: connect, apply triggers, fetch row by id, scan-since-watermark, LISTEN)
- Create: `event_db_sync/services/listener.py` (notify → fetch → map → publish)
- Test: `tests/test_listener.py` (fake reader + fake publisher; no real PG)

- [ ] **Step 1: Write the failing test** — `tests/test_listener.py`:

```python
from event_schemas.user import UserUpsertedPayload

from event_db_sync.services.listener import SyncListener


class FakeReader:
    def __init__(self, row) -> None:
        self._row = row
        self.fetched: list[tuple[str, int]] = []

    async def fetch_row(self, table: str, row_id: int):
        self.fetched.append((table, row_id))
        return self._row


class FakePublisher:
    def __init__(self) -> None:
        self.published: list[UserUpsertedPayload] = []

    async def publish(self, payload, dedupe_key=None) -> None:
        self.published.append(payload)


async def test_notify_attendee_fetches_and_publishes_client() -> None:
    reader = FakeReader({"id": 7, "email": "c@ex.com", "name": "C", "timeZone": "UTC", "updatedAt": None})
    pub = FakePublisher()
    listener = SyncListener(reader=reader, publisher=pub)
    await listener.handle_notification('{"table": "Attendee", "id": 7}')
    assert reader.fetched == [("Attendee", 7)]
    assert pub.published[0].role == "client"
    assert pub.published[0].email == "c@ex.com"


async def test_missing_row_is_skipped() -> None:
    reader = FakeReader(None)
    pub = FakePublisher()
    listener = SyncListener(reader=reader, publisher=pub)
    await listener.handle_notification('{"table": "users", "id": 99}')
    assert pub.published == []


async def test_unknown_table_is_ignored() -> None:
    reader = FakeReader({"id": 1})
    pub = FakePublisher()
    listener = SyncListener(reader=reader, publisher=pub)
    await listener.handle_notification('{"table": "Booking", "id": 1}')
    assert reader.fetched == []
    assert pub.published == []
```

- [ ] **Step 2: Run it red**

Run: `cd event-db-sync && uv run pytest tests/test_listener.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement the listener** — `event_db_sync/services/listener.py`:

```python
import json

import structlog

from event_db_sync.domain.mapping import SOURCE_ATTENDEE, SOURCE_USERS, map_row_to_payload

logger = structlog.get_logger(__name__)

# cal.com table name -> our source key
_TABLE_TO_SOURCE = {"Attendee": SOURCE_ATTENDEE, "users": SOURCE_USERS}


class SyncListener:
    def __init__(self, reader, publisher) -> None:
        self._reader = reader
        self._publisher = publisher

    async def handle_notification(self, raw_payload: str) -> None:
        try:
            note = json.loads(raw_payload)
        except json.JSONDecodeError:
            logger.warning("Bad NOTIFY payload", payload=raw_payload)
            return
        table = note.get("table")
        row_id = note.get("id")
        source = _TABLE_TO_SOURCE.get(table)
        if source is None:
            return
        row = await self._reader.fetch_row(table, row_id)
        if row is None:
            logger.info("Row vanished before sync", table=table, id=row_id)
            return
        payload = map_row_to_payload(source, dict(row))
        dedupe_key = f"{source}:{row_id}:{row.get('updatedAt')}"
        await self._publisher.publish(payload, dedupe_key=dedupe_key)
```

- [ ] **Step 4: Implement the reader** — `event_db_sync/adapters/calcom_reader.py`. (No unit test for the raw SQL itself — it needs a live cal.com PG, exercised in compose/manual integration in Task 16. Keep it thin and obviously-correct.)

```python
import asyncpg

from event_db_sync.adapters.calcom_triggers import apply_triggers

# Per-source SELECT: column set differs only by table name; both expose email/name/timeZone.
_FETCH_BY_TABLE = {
    "Attendee": 'SELECT id, email, name, "timeZone", "updatedAt" FROM "Attendee" WHERE id = $1',
    "users": 'SELECT id, email, name, "timeZone", "updatedAt" FROM "users" WHERE id = $1',
}

# Watermark scan: rows with id greater than last seen (covers inserts during downtime).
_SCAN_BY_TABLE = {
    "Attendee": 'SELECT id, email, name, "timeZone", "updatedAt" FROM "Attendee" '
    "WHERE id > $1 ORDER BY id LIMIT $2",
    "users": 'SELECT id, email, name, "timeZone", "updatedAt" FROM "users" '
    "WHERE id > $1 ORDER BY id LIMIT $2",
}


class CalcomReader:
    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._conn: asyncpg.Connection | None = None

    async def connect(self, apply_trigger_ddl: bool) -> None:
        self._conn = await asyncpg.connect(self._dsn)
        if apply_trigger_ddl:
            await apply_triggers(self._conn)

    async def listen(self, channel: str, callback) -> None:
        await self._conn.add_listener(channel, lambda _c, _p, _ch, payload: callback(payload))

    async def fetch_row(self, table: str, row_id: int) -> dict | None:
        query = _FETCH_BY_TABLE.get(table)
        if query is None:
            return None
        record = await self._conn.fetchrow(query, row_id)
        return dict(record) if record else None

    async def scan_since(self, table: str, last_id: int, limit: int) -> list[dict]:
        query = _SCAN_BY_TABLE.get(table)
        if query is None:
            return []
        records = await self._conn.fetch(query, last_id, limit)
        return [dict(r) for r in records]

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
```

> `add_listener`'s callback is sync; it receives the JSON string. The listener wires it to schedule `handle_notification` on the loop (Task 15 wraps it in `asyncio.create_task`).

- [ ] **Step 5: Run green**

Run: `cd event-db-sync && uv run pytest tests/test_listener.py -v`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
cd event-db-sync
git add event_db_sync/services/listener.py event_db_sync/adapters/calcom_reader.py tests/test_listener.py
git commit -m "feat: NOTIFY listener + raw asyncpg cal.com reader

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 13: Watermark reconcile sweep

**Files:**
- Create: `event_db_sync/adapters/state_repo.py` (read/write `sync_state` via own-DB SqlExecutor)
- Create: `event_db_sync/services/reconciler.py`
- Test: `tests/test_reconciler.py`

- [ ] **Step 1: Write the failing test** — `tests/test_reconciler.py`:

```python
from event_db_sync.services.reconciler import Reconciler


class FakeStateRepo:
    def __init__(self, watermarks) -> None:
        self._w = watermarks
        self.saved: list[tuple[str, int]] = []

    async def get_last_id(self, source: str) -> int:
        return self._w.get(source, 0)

    async def set_last_id(self, source: str, last_id: int) -> None:
        self.saved.append((source, last_id))


class FakeReader:
    def __init__(self, rows_by_table) -> None:
        self._rows = rows_by_table

    async def scan_since(self, table: str, last_id: int, limit: int) -> list[dict]:
        return [r for r in self._rows.get(table, []) if r["id"] > last_id][:limit]


class FakePublisher:
    def __init__(self) -> None:
        self.published = []

    async def publish(self, payload, dedupe_key=None) -> None:
        self.published.append((payload, dedupe_key))


async def test_reconcile_emits_rows_above_watermark_and_advances() -> None:
    reader = FakeReader(
        {
            "Attendee": [
                {"id": 5, "email": "a@b.c", "name": "A", "timeZone": "UTC", "updatedAt": None},
                {"id": 6, "email": "b@b.c", "name": "B", "timeZone": "UTC", "updatedAt": None},
            ]
        }
    )
    state = FakeStateRepo({"attendee": 5})
    pub = FakePublisher()
    rec = Reconciler(reader=reader, publisher=pub, state=state, batch_size=10)
    count = await rec.run_once()
    assert count == 1  # only id 6
    assert pub.published[0][0].email == "b@b.c"
    assert ("attendee", 6) in state.saved
```

- [ ] **Step 2: Run it red**

Run: `cd event-db-sync && uv run pytest tests/test_reconciler.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement** — `event_db_sync/adapters/state_repo.py`:

```python
class StateRepo:
    def __init__(self, sql) -> None:
        self._sql = sql

    async def get_last_id(self, source: str) -> int:
        row = await self._sql.fetch_one(
            "SELECT last_id FROM sync_state WHERE source = :source", {"source": source}
        )
        if row is None or row["last_id"] is None:
            return 0
        return int(row["last_id"])

    async def set_last_id(self, source: str, last_id: int) -> None:
        await self._sql.execute(
            """
            INSERT INTO sync_state (source, last_id, updated_at)
            VALUES (:source, :last_id, now())
            ON CONFLICT (source)
            DO UPDATE SET last_id = GREATEST(sync_state.last_id, EXCLUDED.last_id), updated_at = now()
            """,
            {"source": source, "last_id": last_id},
        )
```

`event_db_sync/services/reconciler.py`:

```python
import structlog

from event_db_sync.domain.mapping import SOURCE_ATTENDEE, SOURCE_USERS, map_row_to_payload

logger = structlog.get_logger(__name__)

# our source key -> cal.com table name
_SOURCE_TABLES = {SOURCE_ATTENDEE: "Attendee", SOURCE_USERS: "users"}


class Reconciler:
    def __init__(self, reader, publisher, state, batch_size: int) -> None:
        self._reader = reader
        self._publisher = publisher
        self._state = state
        self._batch_size = batch_size

    async def run_once(self) -> int:
        total = 0
        for source, table in _SOURCE_TABLES.items():
            total += await self._reconcile_source(source, table)
        return total

    async def _reconcile_source(self, source: str, table: str) -> int:
        last_id = await self._state.get_last_id(source)
        rows = await self._reader.scan_since(table, last_id, self._batch_size)
        emitted = 0
        for row in rows:
            payload = map_row_to_payload(source, dict(row))
            await self._publisher.publish(payload, dedupe_key=f"{source}:{row['id']}:{row.get('updatedAt')}")
            await self._state.set_last_id(source, int(row["id"]))
            emitted += 1
        if emitted:
            logger.info("Reconcile emitted rows", source=source, count=emitted)
        return emitted
```

- [ ] **Step 4: Run green**

Run: `cd event-db-sync && uv run pytest tests/test_reconciler.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd event-db-sync
git add event_db_sync/adapters/state_repo.py event_db_sync/services/reconciler.py tests/test_reconciler.py
git commit -m "feat: watermark reconcile sweep + sync_state repo

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 14: Full-sync service + admin endpoint

**Files:**
- Create: `event_db_sync/services/full_sync.py` (paginated full pass, in-memory running guard)
- Modify: `event_db_sync/routes.py` (add `POST /admin/full-sync`)
- Modify: `event_db_sync/auth.py` (bearer check for `SYNC_ADMIN_TOKEN`)
- Test: `tests/test_full_sync.py`, `tests/test_admin_route.py`

- [ ] **Step 1: Write the failing test (service)** — `tests/test_full_sync.py`:

```python
import pytest

from event_db_sync.services.full_sync import FullSyncRunner


class FakeReader:
    def __init__(self, rows_by_table) -> None:
        self._rows = rows_by_table

    async def scan_since(self, table: str, last_id: int, limit: int) -> list[dict]:
        return [r for r in self._rows.get(table, []) if r["id"] > last_id][:limit]


class FakePublisher:
    def __init__(self) -> None:
        self.published = []

    async def publish(self, payload, dedupe_key=None) -> None:
        self.published.append(payload)


class FakeState:
    def __init__(self) -> None:
        self.saved = []

    async def set_last_id(self, source: str, last_id: int) -> None:
        self.saved.append((source, last_id))


def _rows(n):
    return [{"id": i, "email": f"u{i}@b.c", "name": "N", "timeZone": "UTC", "updatedAt": None} for i in range(1, n + 1)]


async def test_full_sync_walks_all_rows_in_batches() -> None:
    reader = FakeReader({"Attendee": _rows(5), "users": _rows(2)})
    pub = FakePublisher()
    runner = FullSyncRunner(reader=reader, publisher=pub, state=FakeState(), batch_size=2, pause_seconds=0.0)
    count = await runner.run("all")
    assert count == 7
    assert len(pub.published) == 7


async def test_full_sync_rejects_concurrent_run() -> None:
    reader = FakeReader({"Attendee": _rows(1), "users": []})
    runner = FullSyncRunner(reader=reader, publisher=FakePublisher(), state=FakeState(), batch_size=2, pause_seconds=0.0)
    runner._running = True  # simulate in-flight
    with pytest.raises(RuntimeError, match="already running"):
        await runner.run("all")
```

- [ ] **Step 2: Run it red**

Run: `cd event-db-sync && uv run pytest tests/test_full_sync.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement** — `event_db_sync/services/full_sync.py`:

```python
import asyncio

import structlog

from event_db_sync.domain.mapping import SOURCE_ATTENDEE, SOURCE_USERS, map_row_to_payload

logger = structlog.get_logger(__name__)

_SOURCE_TABLES = {SOURCE_ATTENDEE: "Attendee", SOURCE_USERS: "users"}


class FullSyncRunner:
    def __init__(self, reader, publisher, state, batch_size: int, pause_seconds: float) -> None:
        self._reader = reader
        self._publisher = publisher
        self._state = state
        self._batch_size = batch_size
        self._pause = pause_seconds
        self._running = False

    async def run(self, scope: str) -> int:
        if self._running:
            raise RuntimeError("full sync already running")
        self._running = True
        try:
            sources = self._scope_sources(scope)
            total = 0
            for source in sources:
                total += await self._sync_source(source, _SOURCE_TABLES[source])
            logger.info("Full sync complete", scope=scope, total=total)
            return total
        finally:
            self._running = False

    def _scope_sources(self, scope: str) -> list[str]:
        if scope == "attendee":
            return [SOURCE_ATTENDEE]
        if scope == "users":
            return [SOURCE_USERS]
        return [SOURCE_ATTENDEE, SOURCE_USERS]

    async def _sync_source(self, source: str, table: str) -> int:
        last_id = 0
        emitted = 0
        while True:
            rows = await self._reader.scan_since(table, last_id, self._batch_size)
            if not rows:
                return emitted
            for row in rows:
                payload = map_row_to_payload(source, dict(row))
                await self._publisher.publish(payload, dedupe_key=f"{source}:{row['id']}:{row.get('updatedAt')}")
                last_id = int(row["id"])
                emitted += 1
            await self._state.set_last_id(source, last_id)
            if self._pause:
                await asyncio.sleep(self._pause)
```

- [ ] **Step 4: Write the failing route test** — `tests/test_admin_route.py`:

```python
from fastapi import FastAPI
from fastapi.testclient import TestClient

from event_db_sync.routes import build_admin_router


class FakeRunner:
    def __init__(self) -> None:
        self.calls = []

    async def run(self, scope: str) -> int:
        self.calls.append(scope)
        return 3


def _app(runner, token="secret"):
    app = FastAPI()
    app.state.full_sync_runner = runner
    app.state.sync_admin_token = token
    app.include_router(build_admin_router())
    return app


def test_full_sync_requires_token() -> None:
    client = TestClient(_app(FakeRunner()))
    resp = client.post("/admin/full-sync")
    assert resp.status_code == 401


def test_full_sync_accepts_and_runs() -> None:
    runner = FakeRunner()
    client = TestClient(_app(runner))
    resp = client.post("/admin/full-sync?source=attendee", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 202
    assert runner.calls == ["attendee"]


def test_full_sync_conflict_when_running() -> None:
    class Busy(FakeRunner):
        async def run(self, scope: str) -> int:
            raise RuntimeError("full sync already running")

    client = TestClient(_app(Busy()))
    resp = client.post("/admin/full-sync", headers={"Authorization": "Bearer secret"})
    assert resp.status_code == 409
```

- [ ] **Step 5: Implement the route** — add to `event_db_sync/routes.py`:

```python
import hmac

from fastapi import APIRouter, Header, HTTPException, Query, Request, status


def build_admin_router() -> APIRouter:
    router = APIRouter(prefix="/admin", tags=["admin"])

    @router.post("/full-sync", status_code=status.HTTP_202_ACCEPTED)
    async def full_sync(
        request: Request,
        source: str = Query("all", pattern="^(attendee|users|all)$"),
        authorization: str = Header(default=""),
    ) -> dict:
        expected = f"Bearer {request.app.state.sync_admin_token}"
        if not hmac.compare_digest(authorization, expected):
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid token")
        runner = request.app.state.full_sync_runner
        try:
            count = await runner.run(source)
        except RuntimeError:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail="full sync already running")
        return {"status": "completed", "source": source, "emitted": count}

    return router
```

> The test runs `run` inline (returns a count) for determinism. In production wiring (Task 15) the endpoint may instead schedule `asyncio.create_task(runner.run(source))` and return `202` immediately; if you choose the background form, adjust the route to start the task and return before completion, and keep the 409 guard via `runner._running`. Pick the inline form here so the contract test is exact; document the choice in the service README.

- [ ] **Step 6: Run green**

Run: `cd event-db-sync && uv run pytest tests/test_full_sync.py tests/test_admin_route.py -v`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd event-db-sync
git add event_db_sync/services/full_sync.py event_db_sync/routes.py event_db_sync/auth.py \
  tests/test_full_sync.py tests/test_admin_route.py
git commit -m "feat: full-sync runner + POST /admin/full-sync (cal.com source of truth)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 15: Wire main.py lifespan, DI, health/ready

**Files:**
- Modify: `event_db_sync/main.py`
- Modify: `event_db_sync/ioc.py`
- Modify: `event_db_sync/routes.py` (mount admin router; `/ready` checks listener+DB)
- Test: `tests/test_health.py` (create), `tests/test_ioc.py` (create — container builds)

- [ ] **Step 1: Write the failing test** — `tests/test_ioc.py`:

```python
from event_db_sync.ioc import AppProvider


def test_provider_instantiates() -> None:
    # smoke: provider class is importable and constructs without a live broker/DB
    assert AppProvider() is not None
```

`tests/test_health.py`:

```python
from fastapi.testclient import TestClient

from event_db_sync.main import app


def test_health_ok() -> None:
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```

- [ ] **Step 2: Run it red**

Run: `cd event-db-sync && uv run pytest tests/test_ioc.py tests/test_health.py -v`
Expected: FAIL (DI providers/lifespan not wired for the new components).

- [ ] **Step 3: Implement `ioc.py`** — provide (all `Scope.APP`): `Settings`, `async_sessionmaker` (own DB; engine disposed on shutdown — copy event-notifier's `provide_sessionmaker`), `ISqlExecutor` → `SqlExecutor(sessionmaker)`, `StateRepo`, `RabbitBroker` (from `settings.rabbit_url`, telemetry middlewares), `UserUpsertedPublisher` (broker + `settings.rabbit_exchange` + `settings.publish_timeout`), `CalcomReader` (from `settings.calcom_database_url`), `SyncListener`, `Reconciler`, `FullSyncRunner`. Mirror the provider style already in the file. Example for the publisher:

```python
@provide(scope=Scope.APP)
def provide_publisher(self, broker: RabbitBroker, settings: Settings) -> UserUpsertedPublisher:
    return UserUpsertedPublisher(broker=broker, exchange=settings.rabbit_exchange, publish_timeout=settings.publish_timeout)
```

- [ ] **Step 4: Implement `main.py` lifespan** — copy event-notifier's lifespan shape. On startup:
  1. resolve `Settings`, set up logging/tracing;
  2. resolve `CalcomReader`, `await reader.connect(apply_trigger_ddl=settings.apply_triggers)`;
  3. resolve `RabbitBroker`, `await broker.start()` (publisher needs a started broker);
  4. resolve `SyncListener`; register the NOTIFY callback that schedules `asyncio.create_task(listener.handle_notification(payload))`; `await reader.listen(settings.notify_channel, callback)`;
  5. if `settings.reconcile_enabled`, `reconcile_task = asyncio.create_task(_reconcile_loop(reconciler, settings.reconcile_interval_seconds))` where the loop runs `run_once()` then `asyncio.sleep(interval)` (run once immediately on boot to cover the cutover backfill);
  6. stash `app.state.full_sync_runner`, `app.state.sync_admin_token = settings.sync_admin_token`.
  On shutdown: cancel `reconcile_task` (suppress `CancelledError`), `await reader.close()`, `await broker.close()`, `await container.close()`.

  Add `app.include_router(build_admin_router())` after the ops router include.

- [ ] **Step 5: Implement `/ready`** in `routes.py` ops router: inject the own-DB engine, run `SELECT 1`, and confirm the reconcile task/listener are alive via `request.app.state`; return 200/503.

- [ ] **Step 6: Run green**

Run: `cd event-db-sync && uv run pytest tests/test_ioc.py tests/test_health.py -v`
Expected: PASS. Then full suite: `uv run ruff check --fix . && uv run ruff format . && uv run pytest -q`.

- [ ] **Step 7: Commit**

```bash
cd event-db-sync
git add event_db_sync/main.py event_db_sync/ioc.py event_db_sync/routes.py tests/test_ioc.py tests/test_health.py
git commit -m "feat: wire lifespan (listener + reconcile loop), DI, health/ready, admin router

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 16: Dockerfile, entrypoint, CI, compose, docs

**Files:**
- Modify: `Dockerfile`, `entrypoint.sh`, `.github/workflows/publish-image.yml`, `.gitlab-ci.yml` (already name-swapped by Task 5; verify build context).
- Modify (repo root): `docker-compose.yml`, `.env.example`.
- Create: `event-db-sync/CLAUDE.md`, `event-db-sync/docs/{SERVICE_OVERVIEW,API_CONTRACTS,DEPENDENCIES,AUDIT}.md`.

- [ ] **Step 1: Dockerfile build context** — because `event-db-sync` depends on `event-schemas`, its image must build with `event-schemas` available. Mirror `event-notifier/Dockerfile` (which builds an event-schemas-dependent service) rather than event-shortener's (which has none). Confirm whether notifier copies `event-schemas` from the build context or installs by tag; replicate exactly. Set `entrypoint.sh` to:

```sh
#!/bin/sh
set -e
alembic upgrade head
exec uvicorn event_db_sync.main:app --host 0.0.0.0 --port 8888 --log-config uvicorn_config.json
```

- [ ] **Step 2: compose** — add to repo-root `docker-compose.yml` (model `pg-db-sync` on `pg-shortener`, the service on `event-notifier` since it needs both rabbit + own DB + reads pg-calcom):

```yaml
  pg-db-sync:
    image: postgres:16
    environment:
      POSTGRES_USER: ${PG_DB_SYNC_USER:-postgres}
      POSTGRES_PASSWORD: ${PG_DB_SYNC_PASSWORD:-postgres}
      POSTGRES_DB: ${PG_DB_SYNC_DB:-event_db_sync}
    ports:
      - "127.0.0.1:${PG_DB_SYNC_PORT:-5437}:5432"
    volumes:
      - pg-db-sync-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${PG_DB_SYNC_USER:-postgres} -d ${PG_DB_SYNC_DB:-event_db_sync}"]
      interval: 5s
      timeout: 5s
      retries: 10
    restart: unless-stopped

  event-db-sync:
    build:
      context: .
      dockerfile: ./event-db-sync/Dockerfile
    environment:
      DEBUG: "false"
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      OTEL_SERVICE_NAME: event-db-sync
      OTEL_SDK_DISABLED: ${OTEL_SDK_DISABLED:-true}
      DATABASE_URL: postgresql+asyncpg://${PG_DB_SYNC_USER:-postgres}:${PG_DB_SYNC_PASSWORD:-postgres}@pg-db-sync:5432/${PG_DB_SYNC_DB:-event_db_sync}
      CALCOM_DATABASE_URL: postgresql://${PG_CALCOM_USER:-postgres}:${PG_CALCOM_PASSWORD:-postgres}@pg-calcom:5432/${PG_CALCOM_DB:-calcom}
      RABBIT_URL: amqp://${RABBITMQ_USER:-events}:${RABBITMQ_PASSWORD:-events}@rabbitmq:5672/
      SYNC_ADMIN_TOKEN: ${SYNC_ADMIN_TOKEN:-dev-sync-admin-token}
    depends_on:
      pg-db-sync:
        condition: service_healthy
      pg-calcom:
        condition: service_healthy
      rabbitmq:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8888/health', timeout=5)\""]
      interval: 10s
      timeout: 10s
      retries: 10
      start_period: 20s
    restart: unless-stopped
```

Add `pg-db-sync-data:` under the top-level `volumes:`. Confirm the exact `pg-calcom` service name + creds in the existing compose and match them. Add the new env vars to `.env.example`.

- [ ] **Step 3: docs** — write `event-db-sync/CLAUDE.md` (commands, architecture: trigger→notify→publish, reconcile, full-sync, the "sanctioned cal.com trigger" note) and the four `docs/*.md` following the structure of `event-shortener/docs/`.

- [ ] **Step 4: Build + integration smoke** (manual / compose):

Run:
```bash
docker compose up -d --build pg-db-sync pg-calcom rabbitmq event-db-sync
docker compose logs event-db-sync | tail -30
```
Expected: triggers applied, listener connected, reconcile sweep logs on boot. Then exercise via `scripts/calcom_sim.py create` and confirm an `events.user.email` message with `ce-type=user.upserted` appears (RabbitMQ management UI), and a `POST /admin/full-sync` returns 202.

- [ ] **Step 5: Commit**

```bash
cd event-db-sync
git add -A
git commit -m "chore: Dockerfile/CI/docs for event-db-sync"  # add trailer
cd ..
git add docker-compose.yml .env.example
git commit -m "feat: add event-db-sync + pg-db-sync to docker-compose

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

# PHASE 3 — event-users (repo `event-users/`)

### Task 17: Point event-users at the local event-schemas + return user_id from upsert

**Files:**
- Modify: `event-users/pyproject.toml` (`[tool.uv.sources]` → editable path during dev)
- Modify: `event_users/adapters/users_db.py` (`upsert_user_from_crm` returns `uuid.UUID`)
- Modify: `event_users/crm/sync.py` (caller ignores the return — no behavior change)
- Test: `tests/adapters/test_users_db.py`

- [ ] **Step 1: Switch the schema source for dev** — in `event-users/pyproject.toml`:
```toml
event-schemas = { path = "../event-schemas", editable = true }
```
Run: `cd event-users && uv lock && uv sync`.

- [ ] **Step 2: Write the failing test** — add to `tests/adapters/test_users_db.py`:

```python
async def test_upsert_user_from_crm_returns_user_id(sql) -> None:
    new_id = uuid.uuid4()
    sql.fetch_one_results.append({"id": new_id})
    adapter = UsersDBAdapter(sql)
    result = await adapter.upsert_user_from_crm(email="a@b.c", role="client", time_zone="UTC")
    assert result == new_id
```

- [ ] **Step 3: Run it red**

Run: `cd event-users && uv run pytest tests/adapters/test_users_db.py -k returns_user_id -v`
Expected: FAIL — returns `None`.

- [ ] **Step 4: Implement** — change `upsert_user_from_crm` signature to `-> uuid.UUID` and `return user_row["id"]` at the end (after `_upsert_contacts`). Update the existing `crm/sync.py` call site to not break (it discards the return; no change needed beyond confirming type checks pass).

- [ ] **Step 5: Run green**

Run: `cd event-users && uv run pytest tests/adapters/test_users_db.py -v`
Expected: PASS (existing upsert tests still green).

- [ ] **Step 6: Commit**

```bash
cd event-users
git add pyproject.toml uv.lock event_users/adapters/users_db.py tests/adapters/test_users_db.py
git commit -m "feat: upsert_user_from_crm returns user_id; dev-pin event-schemas path

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 18: user.synced publisher (direct to RabbitMQ)

**Files:**
- Create: `event_users/adapters/sync_publisher.py`
- Test: `tests/adapters/test_sync_publisher.py`

- [ ] **Step 1: Write the failing test** — `tests/adapters/test_sync_publisher.py`:

```python
import json

from event_users.adapters.sync_publisher import UserSyncedPublisher


class FakeBroker:
    def __init__(self) -> None:
        self.calls = []

    async def publish(self, body, **kwargs) -> None:
        self.calls.append({"body": body, **kwargs})


async def test_publish_user_synced() -> None:
    broker = FakeBroker()
    pub = UserSyncedPublisher(broker=broker, exchange="events", publish_timeout=5.0)
    await pub.publish(email="a@b.c", role="client", user_id="550e8400-e29b-41d4-a716-446655440001", time_zone="UTC")
    call = broker.calls[0]
    assert call["routing_key"] == "events.user.synced"
    assert call["priority"] == 10
    assert call["headers"]["ce-type"] == "user.synced"
    assert call["headers"]["ce-source"] == "event-users"
    body = json.loads(call["body"])
    assert body["original"]["user_id"] == "550e8400-e29b-41d4-a716-446655440001"
```

- [ ] **Step 2: Run it red**

Run: `cd event-users && uv run pytest tests/adapters/test_sync_publisher.py -v`
Expected: FAIL.

- [ ] **Step 3: Implement** — `event_users/adapters/sync_publisher.py` (same `to_binary` form as event-db-sync's publisher; `source="event-users"`, type `EventType.USER_SYNCED`, routing key `RoutingKey.USER_SYNCED`, deterministic `ce-id` = `uuid5(NAMESPACE, f"{role}:{email}:{user_id}")`). Build the `{original, normalized:{participants:[]}}` envelope where `original` is the `UserSyncedPayload(...).model_dump(mode="json")`.

- [ ] **Step 4: Run green**

Run: `cd event-users && uv run pytest tests/adapters/test_sync_publisher.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd event-users
git add event_users/adapters/sync_publisher.py tests/adapters/test_sync_publisher.py
git commit -m "feat: direct user.synced publisher

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 19: Handle `user.upserted` in the consumer + emit `user.synced`

**Files:**
- Modify: `event_users/consumer.py` (add `handle_user_upserted` free function + dispatch branch)
- Modify: `event_users/ioc.py` (provide `UserSyncedPublisher`; inject into the consumer)
- Test: `tests/test_consumer.py`

- [ ] **Step 1: Write the failing test** — add to `tests/test_consumer.py`:

```python
from event_users.consumer import handle_user_upserted


class FakeSyncPublisher:
    def __init__(self) -> None:
        self.published = []

    async def publish(self, *, email, role, user_id, time_zone) -> None:
        self.published.append((email, role, str(user_id), time_zone))


async def test_user_upserted_upserts_then_publishes_synced() -> None:
    new_id = uuid.uuid4()
    # upsert_user_from_crm does INSERT...RETURNING id, then a contacts upsert
    sessionmaker = RecordingSessionmaker([[[{"id": new_id}], []]])
    publisher = FakeSyncPublisher()
    await handle_user_upserted(
        sessionmaker=sessionmaker,
        sync_publisher=publisher,
        email="c@ex.com",
        role="client",
        time_zone="UTC",
        name="C",
        contacts=[],
        message_id="ce-1",
    )
    session = sessionmaker.sessions[0]
    queries = [q for q, _ in session.statements]
    assert any("INSERT INTO users" in q for q in queries)
    assert session.committed == 1
    assert publisher.published == [("c@ex.com", "client", str(new_id), "UTC")]
```

- [ ] **Step 2: Run it red**

Run: `cd event-users && uv run pytest tests/test_consumer.py -k user_upserted -v`
Expected: FAIL — `handle_user_upserted` missing.

- [ ] **Step 3: Implement `handle_user_upserted`** — in `event_users/consumer.py` (mirror `handle_email_change`'s session/commit shape; publish AFTER commit):

```python
async def handle_user_upserted(
    *,
    sessionmaker,
    sync_publisher,
    email: str,
    role: str,
    time_zone: str | None,
    name: str | None,
    contacts: list,
    message_id: str | None,
) -> None:
    from event_users.adapters.sql import SqlExecutor
    from event_users.adapters.users_db import UsersDBAdapter
    from event_users.dto.users import CreateUserContactDTO

    contact_dtos = [CreateUserContactDTO(channel=c["channel"], contact_id=c["contact_id"]) for c in contacts]
    async with sessionmaker() as session:
        adapter = UsersDBAdapter(SqlExecutor(session))
        user_id = await adapter.upsert_user_from_crm(
            email=email, role=role, time_zone=time_zone, name=name, contacts=contact_dtos
        )
        await session.commit()
    await sync_publisher.publish(email=email, role=role, user_id=user_id, time_zone=time_zone)
```

- [ ] **Step 4: Add the dispatch branch** — in `EmailChangeConsumer.start()`'s `on_message`, replace the single `if event_type != ...: return` guard with type dispatch (NO elif — use early returns):

```python
            if event_type == "user.email.change_requested":
                original = unwrap_payload(data)
                await handle_email_change(
                    sessionmaker=self._sessionmaker,
                    cache_notifier=self._cache_notifier,
                    user_id_str=original["user_id"],
                    old_email=original["old_email"],
                    new_email=original["new_email"],
                    requested_by=original["requested_by"],
                    message_id=headers.get("ce-id"),
                )
                return

            if event_type == "user.upserted":
                original = unwrap_payload(data)
                await handle_user_upserted(
                    sessionmaker=self._sessionmaker,
                    sync_publisher=self._sync_publisher,
                    email=original["email"],
                    role=original["role"],
                    time_zone=original.get("time_zone"),
                    name=original.get("name"),
                    contacts=original.get("contacts", []),
                    message_id=headers.get("ce-id"),
                )
                return

            logger.warning("Unknown event type, skipping", event_type=event_type)
```

Add `from event_schemas.envelope import unwrap_payload` and accept `sync_publisher` in `EmailChangeConsumer.__init__` (store `self._sync_publisher`).

- [ ] **Step 5: Wire DI** — in `event_users/ioc.py` add an APP-scoped `provide_sync_publisher(self, broker: RabbitBroker, settings: Settings) -> UserSyncedPublisher` (construct `RabbitExchange` name from `EVENTS_EXCHANGE` or pass the name string), and pass it into `provide_email_change_consumer`.

- [ ] **Step 6: Run green**

Run: `cd event-users && uv run pytest tests/ -q`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
cd event-users
git add event_users/consumer.py event_users/ioc.py tests/test_consumer.py
git commit -m "feat: handle user.upserted -> upsert + emit user.synced

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

# PHASE 4 — event-saver (repo `event-saver/`)

### Task 20: Consume user.synced and backfill bookings by email

**Files:**
- Modify: `event-saver/pyproject.toml` (`[tool.uv.sources]` → editable path for dev)
- Modify: `event_saver/adapters/booking_repository.py` (add `backfill_user_id_by_email`)
- Modify: `event_saver/application/use_cases/ingest_event.py` (add `USER_SYNCED` branch before the booking_id guard)
- Test: `tests/application/test_ingest_event.py`, `tests/adapters/test_consumer.py`

- [ ] **Step 1: Switch schema source + lock**

In `event-saver/pyproject.toml`: `event-schemas = { path = "../event-schemas", editable = true }`. Run `cd event-saver && uv lock && uv sync`.

- [ ] **Step 2: Write the failing repository test** — add to `tests/adapters/test_booking_repository.py` (or create) using the repo's `FakeSqlExecutor`:

```python
async def test_backfill_user_id_by_email_updates_client_column() -> None:
    sql = FakeSqlExecutor()
    repo = BookingRepository(sql)
    await repo.backfill_user_id_by_email(email="c@ex.com", role="client", user_id=uuid.uuid4())
    query, values = sql.queries[-1]
    assert "client_user_id = :user_id" in query
    assert "client_user_id IS NULL" in query
    assert values["email"] == "c@ex.com"
```

- [ ] **Step 3: Run it red**

Run: `cd event-saver && uv run pytest tests/adapters/test_booking_repository.py -k backfill_user_id_by_email -v`
Expected: FAIL.

- [ ] **Step 4: Implement the repo method** — in `event_saver/adapters/booking_repository.py`:

```python
_ROLE_COLUMNS = {"organizer": "organizer_user_id", "client": "client_user_id"}

_BACKFILL_BY_EMAIL = """
UPDATE bookings b
SET {column} = :user_id, updated_at = now()
WHERE b.{column} IS NULL
  AND EXISTS (
    SELECT 1 FROM events e
    CROSS JOIN LATERAL jsonb_array_elements(
        CASE WHEN jsonb_typeof(e.payload->'normalized'->'participants') = 'array'
             THEN e.payload->'normalized'->'participants' ELSE '[]'::jsonb END
    ) AS p
    WHERE e.booking_id = b.booking_uid
      AND p->>'role' = :role
      AND lower(COALESCE(p->>'email', '')) = lower(:email)
  )
"""


async def backfill_user_id_by_email(self, email: str, role: str, user_id: uuid.UUID) -> None:
    column = _ROLE_COLUMNS.get(role)
    if column is None:
        return
    await self._sql.execute(_BACKFILL_BY_EMAIL.format(column=column), {"user_id": user_id, "role": role, "email": email})
```

(`column` is from a fixed allowlist — never from event data — so the `.format()` is injection-safe.)

- [ ] **Step 5: Write the failing use-case test** — add a `TestUserSynced` class to `tests/application/test_ingest_event.py`, modeled on `TestClientReassigned`: build a `user.synced` event (ce-type `user.synced`, NO `ce-bookingid`), `original = {"email": "c@ex.com", "role": "client", "user_id": <uuid>, "time_zone": "UTC"}`, run `use_case.execute(...)`, and assert the fake booking repo recorded a `backfill_user_id_by_email("c@ex.com", "client", <uuid>)` call. Extend `FakeBookingRepository` with a `backfill_calls` list + the method.

- [ ] **Step 6: Run it red**

Run: `cd event-saver && uv run pytest tests/application/test_ingest_event.py -k UserSynced -v`
Expected: FAIL.

- [ ] **Step 7: Implement the branch** — in `IngestEventUseCase.execute`, AFTER the dedup `is_inserted` save and BEFORE the `if not event.booking_id: return` guard, add:

```python
        if event.event_type == EventType.USER_SYNCED:
            original = event.payload.get("original", {})
            await self._booking_repository.backfill_user_id_by_email(
                email=original["email"], role=original["role"], user_id=uuid.UUID(original["user_id"])
            )
            return
```

(Import `EventType` from `event_schemas.types` and `uuid` if not already imported. Add `backfill_user_id_by_email` to the `IBookingRepository` protocol/interface.)

- [ ] **Step 8: Add a consumer-level test** — in `tests/adapters/test_consumer.py`, add a case with `ce-type=user.synced`, no `ce-bookingid`, body `{"original": {...}, "normalized": {"participants": []}}`, calling `runner._consume_message(message=..., queue_name="events.user.synced")` and asserting `FakeEventStore.save_event` received `event_type="user.synced"`.

- [ ] **Step 9: Run green**

Run: `cd event-saver && uv run pytest tests/ -q`
Expected: PASS. (The new `USER_SYNCED_QUEUE` is auto-included in `SAVER_QUEUES`; verify `tests/test_consumer_topology.py` still passes — it should, since the queue is saver-owned.)

- [ ] **Step 10: Commit**

```bash
cd event-saver
git add pyproject.toml uv.lock event_saver/adapters/booking_repository.py \
  event_saver/application/use_cases/ingest_event.py event_saver/interfaces/ \
  tests/application/test_ingest_event.py tests/adapters/test_consumer.py tests/adapters/test_booking_repository.py
git commit -m "feat: consume user.synced -> backfill bookings user_id by email

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

# PHASE 5 — Release & documentation

### Task 21: Cut the event-schemas tag, re-pin dependents, re-lock

> **Controller-gated:** this task PUSHES the event-schemas tag — do it only with the user's go-ahead.

**Files:**
- `event-schemas` (push tag), `event-users/pyproject.toml`, `event-saver/pyproject.toml`, `event-db-sync/pyproject.toml`.

- [ ] **Step 1: Push the tag** (after user approval):

```bash
cd event-schemas
git push origin HEAD --follow-tags    # pushes the branch/main + v0.4.0
```

- [ ] **Step 2: Re-pin each dependent** — in `event-users/pyproject.toml`, `event-saver/pyproject.toml`, `event-db-sync/pyproject.toml`, set:
```toml
event-schemas = { git = "https://github.com/Lelikov/event-schemas.git", tag = "v0.4.0" }
```

- [ ] **Step 3: Re-lock each**

Run (in each of the three dirs): `uv lock && uv sync && uv run pytest -q`
Expected: PASS against the tagged dependency.

- [ ] **Step 4: Commit each repo**

```bash
# in each of event-users, event-saver, event-db-sync:
git add pyproject.toml uv.lock
git commit -m "chore: pin event-schemas v0.4.0

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

### Task 22: Documentation updates (per CLAUDE.md "Documentation Requirements")

**Files:**
- Modify: root `CLAUDE.md` — add `event-db-sync` to the services table + data-flow diagram + RabbitMQ topology table (`events.user.synced`); note the trigger exception.
- Modify: `event-receiver/QUEUES_DIGEST.md`, `event-saver/QUEUES_DIGEST.md` — add `events.user.synced` + the two new routing rules + `user.upserted`/`user.synced` event types.
- Modify: `docs/architecture/MESSAGE_CONTRACTS.md` — document `user.upserted` (db-sync→event-users) and `user.synced` (event-users→event-saver) contracts.
- Modify: `event-users/docs/AUDIT.md`, `event-saver/docs/AUDIT.md` — note the new flow.
- Modify: `docs/architecture/ONBOARDING.md` + the host-ports table in root `CLAUDE.md` if `event-db-sync` exposes a host port; and `docs/architecture/ARCHITECTURE.md` data flow.

- [ ] **Step 1** Update root `CLAUDE.md` service table (11th service), data-flow diagram (cal.com DB → event-db-sync → events.user.email; event-users → events.user.synced → event-saver), and the RabbitMQ Queue Routing table (add `events.user.synced` → `event-saver`).

- [ ] **Step 2** Update both `QUEUES_DIGEST.md` files and `MESSAGE_CONTRACTS.md` with the new event types, sources, routing rules, and the `{original, normalized}` shapes from the payload models.

- [ ] **Step 3** Update the AUDIT/ARCHITECTURE/ONBOARDING docs as above; mention the sanctioned cal.com trigger exception and the `POST /admin/full-sync` operation.

- [ ] **Step 4: Commit** (root repo, plus any per-service doc commits in their own repos)

```bash
git add CLAUDE.md docs/architecture/MESSAGE_CONTRACTS.md docs/architecture/ARCHITECTURE.md docs/architecture/ONBOARDING.md
git commit -m "docs: document event-db-sync + user.upserted/user.synced contracts

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
# event-receiver / event-saver / event-users QUEUES_DIGEST + AUDIT committed in their own repos
```

---

## Self-review checklist (run before execution)

- **Spec coverage:** trigger+notify (T11/T12), listener→publish direct (T10/T12), watermark reconcile + own DB (T7/T13), full-sync source-of-truth (T14), event-schemas additions (T1–T4), event-users upsert+emit (T17–T19), event-saver backfill by email (T20), docs (T22). ✅
- **Roles:** `Attendee→client`, `users→organizer` consistent in mapping (T9), reconcile (T13), full-sync (T14). ✅
- **Type consistency:** `upsert_user_from_crm -> uuid.UUID` (T17) consumed by `handle_user_upserted` (T19) and published as `user.synced` (T18) consumed by `backfill_user_id_by_email` (T20). `RoutingKey.USER_SYNCED` / `USER_SYNCED_QUEUE` / `EventType.USER_SYNCED` spelled identically across T3/T20. ✅
- **No-elif rule:** dispatch branches in T19 use early returns. ✅
- **Atomicity caveat:** publish-after-commit in event-users (T19) and event-db-sync is non-atomic; the reconcile sweep (T13) + deterministic ce-id (T10/T18) make a dropped publish self-healing. Documented. ✅
- **cloudevents `to_binary` form:** T10 flags the version-dependent return shape and points to event-receiver's exact form to match. ✅
```
