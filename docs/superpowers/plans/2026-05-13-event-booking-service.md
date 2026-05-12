# event-booking Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Decompose calendar-bot into the events system by creating a new `event-booking` FastStream consumer service that coordinates booking business logic (constraints, chat, meeting URLs) and distributes notifications to `event-notifier`.

**Architecture:** `event-receiver` publishes booking CloudEvents to RabbitMQ → `event-booking` consumes from `events.booking.lifecycle`, orchestrates side effects (Cal.com DB, GetStream chat, Jitsi meeting URLs via Shortify), then publishes `notification.send_requested` events back to `event-receiver` for `event-notifier` to deliver. A background scheduler handles booking reminders. `event-saver` already receives original booking events for persistence — no changes needed there.

**Tech Stack:** Python 3.14, FastStream (RabbitMQ), FastAPI (health endpoint), Dishka DI, SQLAlchemy (raw SQL to Cal.com DB), httpx, PyJWT, stream-chat, structlog, Pydantic Settings, cloudevents.

**Spec:** `docs/superpowers/specs/2026-05-13-event-booking-service-design.md`

**Source to port from:** `~/PycharmProjects/calendar-bot`

---

## File Structure

### New service: `event-booking/`

```
event-booking/
├── event_booking/
│   ├── __init__.py
│   ├── main.py                        # FastAPI app + lifespan (start consumer, scheduler)
│   ├── config.py                      # Pydantic Settings
│   ├── ioc.py                         # Dishka AppProvider (APP/REQUEST scopes)
│   ├── dtos.py                        # Frozen dataclasses: BookingDTO, UserDTO, etc.
│   ├── consumer.py                    # FastStream RabbitMQ subscriber
│   ├── scheduler.py                   # Asyncio periodic reminder task
│   ├── controllers/
│   │   ├── __init__.py
│   │   ├── booking.py                 # Main orchestrator: dispatch + flow coordination
│   │   ├── constraints.py             # Booking constraint validation logic
│   │   ├── meeting.py                 # Jitsi JWT generation + Shortify URL management
│   │   └── chat.py                    # GetStream chat lifecycle
│   ├── adapters/
│   │   ├── __init__.py
│   │   ├── sql.py                     # SqlExecutor (AsyncSession wrapper)
│   │   ├── db.py                      # BookingDatabaseAdapter (Cal.com queries)
│   │   ├── events.py                  # EventPublisher (HTTP POST to event-receiver)
│   │   ├── get_stream.py              # GetStream Chat SDK wrapper
│   │   └── shortener.py              # Shortify URL shortener client
│   └── interfaces/
│       ├── __init__.py
│       ├── sql.py                     # ISqlExecutor protocol
│       ├── db.py                      # IBookingDatabaseAdapter protocol
│       ├── events.py                  # IEventPublisher protocol
│       ├── chat.py                    # IChatClient protocol
│       ├── shortener.py              # IUrlShortener protocol
│       ├── meeting.py                 # IMeetingController protocol
│       └── constraints.py            # IBookingConstraintsAnalyzer protocol
├── tests/
│   ├── conftest.py                    # Shared fixtures + mock factories
│   ├── factories.py                   # Polyfactory DTO builders
│   ├── controllers/
│   │   ├── conftest.py
│   │   ├── test_booking.py
│   │   ├── test_constraints.py
│   │   ├── test_meeting.py
│   │   └── test_chat.py
│   ├── adapters/
│   │   ├── conftest.py
│   │   ├── test_events.py
│   │   ├── test_db.py
│   │   ├── test_get_stream.py
│   │   └── test_shortener.py
│   ├── test_consumer.py
│   └── test_scheduler.py
├── pyproject.toml
├── CLAUDE.md
├── .env.example
└── Dockerfile
```

### Modified files in existing services

```
event-schemas/event_schemas/
├── types.py                           # Add BOOKING_REJECTED EventType + priority/version
├── booking.py                         # Add BookingRejectedPayload
└── __init__.py                        # Re-export new payload

event-receiver/event_receiver/
└── config.py                          # Add routing rules for booking.rejected

event-notifier/event_notifier/
├── event_types.py                     # Add notification.send_requested + booking.rejected mappings
├── adapters/consumer.py               # Handle notification.send_requested payload structure
├── application/use_cases/
│   └── process_domain_event.py        # Add direct-recipient path for notification.send_requested
├── infrastructure/channels/
│   ├── email.py                       # Add BOOKING_REJECTED template mapping
│   └── telegram.py                    # Add BOOKING_REJECTED message template
└── templates/                         # Jinja2 email templates (ported from calendar-bot) — future task
```

---

## Phase 1: Foundation (event-schemas + event-receiver)

### Task 1: Add BOOKING_REJECTED event type to event-schemas

**Files:**
- Modify: `event-schemas/event_schemas/types.py`
- Modify: `event-schemas/event_schemas/booking.py`
- Modify: `event-schemas/event_schemas/__init__.py`

- [ ] **Step 1: Add BOOKING_REJECTED to EventType enum**

In `event-schemas/event_schemas/types.py`, add after `BOOKING_CLIENT_REASSIGNED`:

```python
BOOKING_REJECTED = "booking.rejected"
```

- [ ] **Step 2: Add priority and schema version mappings**

In `event-schemas/event_schemas/types.py`, add to `EVENT_PRIORITIES`:

```python
EventType.BOOKING_REJECTED: EventPriority.CRITICAL,
```

Add to `EVENT_SCHEMA_VERSIONS`:

```python
EventType.BOOKING_REJECTED: "v1",
```

- [ ] **Step 3: Create BookingRejectedPayload model**

In `event-schemas/event_schemas/booking.py`, add:

```python
class BookingRejectedPayload(BaseModel):
    """Payload for booking.rejected event."""

    client_email: EmailStr = Field(..., description="Client email address")
    rejection_type: str | None = Field(None, description="Type: month_limit, year_limit, min_interval")
    rejection_reasons: list[str] = Field(default_factory=list, description="Human-readable rejection reasons")
    available_from: datetime | None = Field(None, description="Earliest available booking time")
    has_active_booking: bool = Field(False, description="Whether client has an active future booking")
    active_booking_start: datetime | None = Field(None, description="Start time of the active booking if exists")

    model_config = {
        "json_schema_extra": {
            "example": {
                "client_email": "client@example.com",
                "rejection_type": "month_limit",
                "rejection_reasons": ["Monthly booking limit reached"],
                "available_from": "2024-04-01T00:00:00Z",
                "has_active_booking": False,
                "active_booking_start": None,
            }
        }
    }
```

- [ ] **Step 4: Re-export from __init__.py**

In `event-schemas/event_schemas/__init__.py`, add import:

```python
from event_schemas.booking import (
    BookingCancelledPayload,
    BookingCreatedPayload,
    BookingReassignedPayload,
    BookingRejectedPayload,
    BookingReminderSentPayload,
    BookingRescheduledPayload,
)
```

Add `"BookingRejectedPayload"` to `__all__`.

- [ ] **Step 5: Verify**

```bash
cd event-schemas && ruff check . && ruff format . && python -c "from event_schemas import BookingRejectedPayload; print('OK')"
```

- [ ] **Step 6: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-schemas/event_schemas/types.py event-schemas/event_schemas/booking.py event-schemas/event_schemas/__init__.py
git commit -m "feat(event-schemas): add BOOKING_REJECTED event type and payload"
```

---

### Task 2: Add routing rules for booking.rejected in event-receiver

**Files:**
- Modify: `event-receiver/event_receiver/config.py`

- [ ] **Step 1: Add routing rule**

In `event-receiver/event_receiver/config.py`, add to `_default_route_rules()` after the existing booking rules (after the `booking.cancelled` rule):

```python
RouteRule(
    destination="events.booking.lifecycle",
    source_pattern="booking",
    type_pattern="booking.rejected",
),
```

- [ ] **Step 2: Verify**

```bash
cd event-receiver && ruff check event_receiver/config.py
```

- [ ] **Step 3: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-receiver/event_receiver/config.py
git commit -m "feat(event-receiver): add routing rule for booking.rejected"
```

---

## Phase 2: event-booking Service Skeleton

### Task 3: Create project scaffolding

**Files:**
- Create: `event-booking/pyproject.toml`
- Create: `event-booking/.env.example`
- Create: `event-booking/CLAUDE.md`

- [ ] **Step 1: Create pyproject.toml**

```toml
[project]
name = "event-booking"
version = "0.1.0"
description = "Booking orchestration service: constraints, chat, meeting URLs"
requires-python = ">=3.14"
dependencies = [
    "cloudevents>=2.0.0",
    "cryptography>=46.0.1",
    "dishka>=1.8.0",
    "event-schemas @ file:///../event-schemas",
    "fastapi>=0.135.1",
    "faststream[rabbit]>=0.6.7",
    "httpx>=0.28.0",
    "pydantic-settings>=2.13.1",
    "pyjwt>=2.10.1",
    "sqlalchemy[asyncio]>=2.0.48",
    "asyncpg>=0.31.0",
    "stream-chat>=4.29.0",
    "structlog>=25.5.0",
    "uvicorn>=0.41.0",
]

[project.optional-dependencies]
dev = [
    "polyfactory>=2.18.1",
    "pytest>=8.3.4",
    "pytest-asyncio>=0.25.3",
    "ruff>=0.9.3",
    "pre-commit>=4.5.1",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.ruff]
line-length = 120
target-version = "py314"

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "ANN", "S", "B", "A", "C4", "DTZ", "T10", "EM", "ISC", "ICN", "PIE", "PT", "Q", "RET", "SIM", "TID", "ARG", "PTH", "PD", "PGH", "PL", "TRY", "NPY", "RUF"]
ignore = ["ANN101", "ANN102", "ANN401"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
```

- [ ] **Step 2: Create .env.example**

```env
# Cal.com PostgreSQL (read/write)
CALCOM_POSTGRES_DSN=postgresql+asyncpg://user:pass@localhost:5432/calcom

# RabbitMQ
RABBIT_URL=amqp://guest:guest@localhost:5672/
RABBIT_EXCHANGE=events
BOOKING_LIFECYCLE_QUEUE=events.booking.lifecycle

# event-receiver (publish enriched events)
EVENTS_ENDPOINT_URL=http://localhost:8888/event/booking
EVENTS_API_KEY=your-booking-api-key
EVENTS_SOURCE=booking

# Jitsi JWT
JITSI_JWT_SECRET=your-jitsi-jwt-secret
JITSI_JWT_AUD=jitsi
JITSI_JWT_ISS=your-app-id
MEETING_HOST_URL=https://meet.example.com

# GetStream Chat
CHAT_API_KEY=your-getstream-api-key
CHAT_API_SECRET=your-getstream-api-secret
CHAT_USER_ID_ENCRYPTION_KEY=hex-encoded-aes-key

# Shortify URL shortener
SHORTENER_URL=https://shortify.example.com
SHORTENER_API_KEY=your-shortify-api-key

# Booking constraints
IS_ENABLE_BOOKING_CONSTRAINTS=false

# Reminder scheduler
REMINDER_INTERVAL_SECONDS=300
REMINDER_SHIFT_FROM_MINUTES=55
REMINDER_SHIFT_TO_MINUTES=65

# General
DEBUG=false
LOG_LEVEL=INFO
```

- [ ] **Step 3: Create CLAUDE.md**

```markdown
# CLAUDE.md — event-booking

## Commands

\```bash
uv sync                      # install deps
uv run pytest                # run all tests
uv run pytest tests/ -v      # verbose
ruff check --fix .           # lint
ruff format .                # format
pre-commit run --all-files   # all hooks
uvicorn event_booking.main:app --reload  # run locally
\```

## Architecture

FastStream consumer service that orchestrates booking side effects.

### Data Flow

\```
events.booking.lifecycle (RabbitMQ)
        │
        ▼
  consumer.py (CloudEvent parsing)
        │
        ▼
  controllers/booking.py (dispatch by event type)
        │
        ├── controllers/constraints.py (validate booking limits)
        ├── controllers/chat.py (GetStream chat CRUD)
        ├── controllers/meeting.py (Jitsi JWT + Shortify URLs)
        │
        ├── adapters/db.py (Cal.com PostgreSQL read/write)
        └── adapters/events.py (publish to event-receiver)
\```

### Layer Map

| Layer | Path | Responsibility |
|---|---|---|
| Entry point | `main.py` | FastAPI + lifespan: consumer, scheduler, health |
| Consumer | `consumer.py` | FastStream subscriber, CloudEvent parsing |
| Scheduler | `scheduler.py` | Periodic reminder task |
| Controllers | `controllers/` | Business logic orchestration |
| Adapters | `adapters/` | External service integrations |
| Interfaces | `interfaces/` | Protocol contracts |
| DTOs | `dtos.py` | Frozen dataclasses |
| Config | `config.py` | Pydantic Settings |
| DI | `ioc.py` | Dishka providers |
```

- [ ] **Step 4: Create empty package structure**

```bash
mkdir -p event-booking/event_booking/controllers
mkdir -p event-booking/event_booking/adapters
mkdir -p event-booking/event_booking/interfaces
mkdir -p event-booking/tests/controllers
mkdir -p event-booking/tests/adapters
touch event-booking/event_booking/__init__.py
touch event-booking/event_booking/controllers/__init__.py
touch event-booking/event_booking/adapters/__init__.py
touch event-booking/event_booking/interfaces/__init__.py
touch event-booking/tests/__init__.py
touch event-booking/tests/controllers/__init__.py
touch event-booking/tests/adapters/__init__.py
```

- [ ] **Step 5: Install dependencies**

```bash
cd event-booking && uv sync
```

- [ ] **Step 6: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booking/
git commit -m "feat(event-booking): scaffold new service with project structure"
```

---

### Task 4: Settings and DTOs

**Files:**
- Create: `event-booking/event_booking/config.py`
- Create: `event-booking/event_booking/dtos.py`

- [ ] **Step 1: Create config.py**

Port from `calendar-bot/app/settings.py`, keeping only relevant settings:

```python
"""Service configuration via environment variables."""

from pydantic import AmqpDsn, Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    debug: bool = False
    log_level: str = "INFO"

    # Cal.com PostgreSQL
    calcom_postgres_dsn: PostgresDsn = Field(strict=True)

    # RabbitMQ
    rabbit_url: AmqpDsn = "amqp://guest:guest@localhost:5672/"
    rabbit_exchange: str = "events"
    booking_lifecycle_queue: str = "events.booking.lifecycle"

    # event-receiver (publish events)
    events_endpoint_url: str | None = None
    events_api_key: str | None = None
    events_source: str = "booking"
    events_timeout_seconds: float = 5.0

    # Jitsi JWT
    jitsi_jwt_secret: str = Field(strict=True)
    jitsi_jwt_aud: str = Field(strict=True)
    jitsi_jwt_iss: str = Field(strict=True)
    meeting_host_url: str = "http://localhost:8080"

    # GetStream Chat
    chat_api_key: str = Field(strict=True)
    chat_api_secret: str = Field(strict=True)
    chat_user_id_encryption_key: str = Field(strict=True)

    # Shortify
    shortener_url: str = Field(strict=True)
    shortener_api_key: str | None = None

    # Booking constraints
    is_enable_booking_constraints: bool = False

    # Reminder scheduler
    reminder_interval_seconds: int = 300
    reminder_shift_from_minutes: int = 55
    reminder_shift_to_minutes: int = 65
```

- [ ] **Step 2: Create dtos.py**

Port from `calendar-bot/app/dtos.py`, keeping booking-related DTOs only:

```python
"""Frozen dataclasses for inter-layer data transfer."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True, slots=True)
class UserDTO:
    id: int
    name: str
    email: str
    locked: bool
    time_zone: str
    telegram_chat_id: int | None = None


@dataclass(frozen=True, slots=True)
class BookingClientDTO:
    name: str
    email: str
    time_zone: str


@dataclass(slots=True)
class BookingDTO:
    """Mutable: previous_booking is set after initial construction."""

    created_at: datetime
    end_time: datetime
    id: int
    start_time: datetime
    status: str
    title: str
    uid: str
    user: UserDTO | None = None
    client: BookingClientDTO | None = None
    metadata: dict | None = None
    previous_booking: "BookingDTO | None" = None
    event_type_slug: str | None = None
    from_reschedule: str | None = None


@dataclass(frozen=True, slots=True)
class AttendeeBookingDTO:
    booking_id: int
    booking_uid: str
    name: str
    email: str
    start_time: datetime
    end_time: datetime
    status: str


@dataclass(frozen=True, slots=True)
class ConstraintsResult:
    is_allowed: bool
    available_from: datetime | None = None
    has_active_booking: bool = False
    active_booking_start: datetime | None = None
    rejection_reasons: list[str] = field(default_factory=list)
    rejection_type: str | None = None
```

- [ ] **Step 3: Verify**

```bash
cd event-booking && ruff check . && ruff format .
```

- [ ] **Step 4: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booking/event_booking/config.py event-booking/event_booking/dtos.py
git commit -m "feat(event-booking): add settings and DTO definitions"
```

---

### Task 5: Interfaces (Protocol contracts)

**Files:**
- Create: `event-booking/event_booking/interfaces/sql.py`
- Create: `event-booking/event_booking/interfaces/db.py`
- Create: `event-booking/event_booking/interfaces/events.py`
- Create: `event-booking/event_booking/interfaces/chat.py`
- Create: `event-booking/event_booking/interfaces/shortener.py`
- Create: `event-booking/event_booking/interfaces/meeting.py`
- Create: `event-booking/event_booking/interfaces/constraints.py`

- [ ] **Step 1: Create interfaces/sql.py**

```python
"""SQL executor protocol."""

from typing import Protocol

from sqlalchemy.engine import RowMapping


class ISqlExecutor(Protocol):
    async def fetch_one(self, query: str, values: dict) -> RowMapping | None: ...
    async def fetch_all(self, query: str, values: dict) -> list[RowMapping]: ...
    async def execute(self, query: str, values: dict) -> None: ...
    async def execute_in_transaction(self, statements: list[tuple[str, dict]]) -> None: ...
```

- [ ] **Step 2: Create interfaces/db.py**

```python
"""Booking database adapter protocol."""

from datetime import datetime
from typing import Protocol

from event_booking.dtos import AttendeeBookingDTO, BookingDTO, UserDTO


class IBookingDatabaseAdapter(Protocol):
    async def get_booking(self, booking_uid: str) -> BookingDTO | None: ...
    async def get_bookings(self, start_time_from: datetime, start_time_to: datetime) -> list[BookingDTO]: ...
    async def get_attendee_bookings_by_email(self, *, email: str) -> list[AttendeeBookingDTO]: ...
    async def get_user_by_email(self, email: str) -> UserDTO | None: ...
    async def get_organizer_chat_id(self, email: str) -> int | None: ...
    async def update_booking_video_url(self, booking_uid: str, url: str) -> None: ...
    async def delete_booking_and_attendee_by_booking_id(self, *, booking_id: int) -> None: ...
```

- [ ] **Step 3: Create interfaces/events.py**

```python
"""Event publisher protocol."""

from typing import Any, Protocol

from event_schemas.types import EventType


class IEventPublisher(Protocol):
    async def send_event(self, booking_uid: str, event: EventType, data: dict[str, Any] | None = None) -> None: ...
    async def send_notification_command(
        self,
        *,
        booking_uid: str,
        trigger_event: str,
        recipients: list[dict[str, str]],
        template_data: dict[str, Any],
    ) -> None: ...
```

- [ ] **Step 4: Create interfaces/chat.py**

```python
"""GetStream chat client protocol."""

from typing import Protocol


class IChatClient(Protocol):
    async def create_chat(self, *, channel_id: str, organizer_id: str, client_id: str) -> None: ...
    async def delete_chat(self, *, channel_id: str) -> None: ...
    async def send_message(self, *, channel_id: str, user_id: str, message: dict) -> None: ...
    def create_token(self, *, user_id: str, name: str, expires_at: int) -> str: ...
```

- [ ] **Step 5: Create interfaces/shortener.py**

```python
"""URL shortener protocol."""

from typing import Protocol


class IUrlShortener(Protocol):
    async def create_url(self, long_url: str, expires_at: float, not_before: float, external_id: str) -> str | None: ...
    async def get_url(self, external_id: str) -> str | None: ...
    async def update_url_data(
        self, *, long_url: str, expires_at: float, not_before: float, new_external_id: str, old_external_id: str
    ) -> str | None: ...
    async def delete_url(self, *, external_id: str) -> str | None: ...
```

- [ ] **Step 6: Create interfaces/meeting.py**

```python
"""Meeting controller protocol."""

from typing import Protocol

from event_booking.dtos import BookingDTO


class IMeetingController(Protocol):
    async def create_meeting_url(
        self,
        *,
        booking: BookingDTO,
        participant_id: str,
        participant_name: str,
        participant_email: str,
        is_update_url_data: bool = False,
        external_id_prefix: str = "",
    ) -> str: ...
    async def delete_meeting_url(self, *, booking: BookingDTO, external_id_prefix: str = "") -> None: ...
```

- [ ] **Step 7: Create interfaces/constraints.py**

```python
"""Booking constraints analyzer protocol."""

from typing import Protocol

from event_booking.dtos import AttendeeBookingDTO, BookingDTO, ConstraintsResult


class IBookingConstraintsAnalyzer(Protocol):
    def analyze_on_create(self, *, booking: BookingDTO, attendee_bookings: list[AttendeeBookingDTO]) -> ConstraintsResult: ...
```

- [ ] **Step 8: Verify**

```bash
cd event-booking && ruff check . && ruff format .
```

- [ ] **Step 9: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booking/event_booking/interfaces/
git commit -m "feat(event-booking): add protocol-based interface contracts"
```

---

### Task 6: SQL executor and database adapter

**Files:**
- Create: `event-booking/event_booking/adapters/sql.py`
- Create: `event-booking/event_booking/adapters/db.py`
- Create: `event-booking/tests/adapters/test_db.py`

- [ ] **Step 1: Create adapters/sql.py**

Port from `calendar-bot/app/adapters/sql.py`:

```python
"""Thin wrapper over SQLAlchemy AsyncSession for raw SQL."""

from sqlalchemy import text
from sqlalchemy.engine import RowMapping
from sqlalchemy.ext.asyncio import AsyncSession


class SqlExecutor:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def fetch_one(self, query: str, values: dict) -> RowMapping | None:
        result = await self.session.execute(text(query), values)
        return result.mappings().first()

    async def fetch_all(self, query: str, values: dict) -> list[RowMapping]:
        result = await self.session.execute(text(query), values)
        return list(result.mappings().all())

    async def execute(self, query: str, values: dict) -> None:
        await self.session.execute(text(query), values)
        await self.session.commit()

    async def execute_in_transaction(self, statements: list[tuple[str, dict]]) -> None:
        for query, values in statements:
            await self.session.execute(text(query), values)
        await self.session.commit()
```

- [ ] **Step 2: Create adapters/db.py**

Port from `calendar-bot/app/adapters/db.py`. This adapter contains the Cal.com SQL queries. Port all methods: `get_booking`, `get_bookings`, `get_attendee_bookings_by_email`, `get_user_by_email`, `get_organizer_chat_id`, `update_booking_video_url`, `delete_booking_and_attendee_by_booking_id`.

Key reference: `calendar-bot/app/adapters/db.py` — port all SQL queries and the `_fill_booking_dto` / `_normalize_email` static methods. Replace DTO imports with `event_booking.dtos`.

```python
"""Booking database adapter for Cal.com PostgreSQL."""

import re

import structlog
from sqlalchemy.engine import RowMapping

from event_booking.dtos import AttendeeBookingDTO, BookingClientDTO, BookingDTO, UserDTO
from event_booking.interfaces.sql import ISqlExecutor

logger = structlog.get_logger(__name__)


class BookingDatabaseAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    @staticmethod
    def _normalize_email(email: str) -> str:
        email = email.strip().lower()
        local, domain = email.split("@", 1)
        local = re.sub(r"\+.*", "", local)
        return f"{local}@{domain}"

    @staticmethod
    def _fill_booking_dto(row: RowMapping) -> BookingDTO:
        user = None
        if row.get("user_id"):
            user = UserDTO(
                id=row["user_id"],
                name=row.get("user_name", ""),
                email=row.get("user_email", ""),
                locked=row.get("user_locked", False),
                time_zone=row.get("user_time_zone", "UTC"),
                telegram_chat_id=row.get("telegram_chat_id"),
            )
        client = None
        if row.get("client_name"):
            client = BookingClientDTO(
                name=row["client_name"],
                email=row.get("client_email", ""),
                time_zone=row.get("client_time_zone", "UTC"),
            )
        return BookingDTO(
            created_at=row["created_at"],
            end_time=row["end_time"],
            id=row["booking_id"],
            start_time=row["start_time"],
            status=row["status"],
            title=row.get("title", ""),
            uid=row["uid"],
            user=user,
            client=client,
            metadata=row.get("metadata"),
            event_type_slug=row.get("event_type_slug"),
            from_reschedule=row.get("from_reschedule"),
        )

    async def get_booking(self, booking_uid: str) -> BookingDTO | None:
        row = await self._sql.fetch_one(
            """
            SELECT
                b.id AS booking_id, b.uid, b.title, b.status,
                b."startTime" AS start_time, b."endTime" AS end_time,
                b."createdAt" AS created_at, b.metadata,
                b."fromReschedule" AS from_reschedule,
                u.id AS user_id, u.name AS user_name, u.email AS user_email,
                u.locked AS user_locked, u."timeZone" AS user_time_zone,
                u.telegram_chat_id,
                a.name AS client_name, a.email AS client_email, a."timeZone" AS client_time_zone,
                et.slug AS event_type_slug
            FROM "Booking" b
            LEFT JOIN users u ON b."userId" = u.id
            LEFT JOIN "Attendee" a ON a."bookingId" = b.id
            LEFT JOIN "EventType" et ON b."eventTypeId" = et.id
            WHERE b.uid = :uid
            LIMIT 1
            """,
            {"uid": booking_uid},
        )
        if not row:
            return None
        return self._fill_booking_dto(row)

    async def get_bookings(self, start_time_from: "datetime", start_time_to: "datetime") -> list[BookingDTO]:
        from datetime import datetime  # noqa: F811

        rows = await self._sql.fetch_all(
            """
            SELECT
                b.id AS booking_id, b.uid, b.title, b.status,
                b."startTime" AS start_time, b."endTime" AS end_time,
                b."createdAt" AS created_at, b.metadata,
                b."fromReschedule" AS from_reschedule,
                u.id AS user_id, u.name AS user_name, u.email AS user_email,
                u.locked AS user_locked, u."timeZone" AS user_time_zone,
                u.telegram_chat_id,
                a.name AS client_name, a.email AS client_email, a."timeZone" AS client_time_zone,
                et.slug AS event_type_slug
            FROM "Booking" b
            LEFT JOIN users u ON b."userId" = u.id
            LEFT JOIN "Attendee" a ON a."bookingId" = b.id
            LEFT JOIN "EventType" et ON b."eventTypeId" = et.id
            WHERE b.status = 'accepted'
              AND b."startTime" >= :start_from
              AND b."startTime" <= :start_to
            """,
            {"start_from": start_time_from, "start_to": start_time_to},
        )
        return [self._fill_booking_dto(row) for row in rows]

    async def get_attendee_bookings_by_email(self, *, email: str) -> list[AttendeeBookingDTO]:
        normalized = self._normalize_email(email)
        rows = await self._sql.fetch_all(
            """
            SELECT
                b.id AS booking_id, b.uid AS booking_uid,
                a.name, a.email,
                b."startTime" AS start_time, b."endTime" AS end_time,
                b.status
            FROM "Attendee" a
            JOIN "Booking" b ON a."bookingId" = b.id
            WHERE LOWER(REGEXP_REPLACE(a.email, '\\+.*@', '@')) = :email
            """,
            {"email": normalized},
        )
        return [
            AttendeeBookingDTO(
                booking_id=row["booking_id"],
                booking_uid=row["booking_uid"],
                name=row["name"],
                email=row["email"],
                start_time=row["start_time"],
                end_time=row["end_time"],
                status=row["status"],
            )
            for row in rows
        ]

    async def get_user_by_email(self, email: str) -> UserDTO | None:
        row = await self._sql.fetch_one(
            'SELECT id, name, email, locked, "timeZone" AS time_zone, telegram_chat_id FROM users WHERE email = :email',
            {"email": email},
        )
        if not row:
            return None
        return UserDTO(
            id=row["id"],
            name=row["name"],
            email=row["email"],
            locked=row["locked"],
            time_zone=row["time_zone"],
            telegram_chat_id=row.get("telegram_chat_id"),
        )

    async def get_organizer_chat_id(self, email: str) -> int | None:
        row = await self._sql.fetch_one(
            "SELECT telegram_chat_id FROM users WHERE email = :email AND locked = FALSE AND telegram_chat_id IS NOT NULL",
            {"email": email},
        )
        if not row:
            return None
        return row["telegram_chat_id"]

    async def update_booking_video_url(self, booking_uid: str, url: str) -> None:
        await self._sql.execute(
            """
            UPDATE "Booking"
            SET metadata = COALESCE(metadata, '{}'::jsonb) || jsonb_build_object('videoCallUrl', :url)
            WHERE uid = :uid
            """,
            {"uid": booking_uid, "url": url},
        )

    async def delete_booking_and_attendee_by_booking_id(self, *, booking_id: int) -> None:
        await self._sql.execute_in_transaction([
            ('DELETE FROM "Attendee" WHERE "bookingId" = :id', {"id": booking_id}),
            ('DELETE FROM "Booking" WHERE id = :id', {"id": booking_id}),
        ])
```

- [ ] **Step 3: Write tests for database adapter**

Create `event-booking/tests/adapters/test_db.py`:

```python
"""Tests for BookingDatabaseAdapter."""

import pytest

from event_booking.adapters.db import BookingDatabaseAdapter


class TestNormalizeEmail:
    def test_strips_and_lowercases(self) -> None:
        assert BookingDatabaseAdapter._normalize_email("  User@Example.COM  ") == "user@example.com"

    def test_removes_plus_alias(self) -> None:
        assert BookingDatabaseAdapter._normalize_email("user+tag@example.com") == "user@example.com"

    def test_no_alias(self) -> None:
        assert BookingDatabaseAdapter._normalize_email("user@example.com") == "user@example.com"


class TestFillBookingDto:
    def test_fills_all_fields(self) -> None:
        from datetime import UTC, datetime

        row = {
            "booking_id": 1,
            "uid": "abc-123",
            "title": "Test Booking",
            "status": "accepted",
            "start_time": datetime(2026, 6, 15, 10, 0, tzinfo=UTC),
            "end_time": datetime(2026, 6, 15, 11, 0, tzinfo=UTC),
            "created_at": datetime(2026, 6, 1, tzinfo=UTC),
            "metadata": None,
            "from_reschedule": None,
            "user_id": 42,
            "user_name": "Organizer",
            "user_email": "org@test.com",
            "user_locked": False,
            "user_time_zone": "Europe/Moscow",
            "telegram_chat_id": 12345,
            "client_name": "Client",
            "client_email": "client@test.com",
            "client_time_zone": "Europe/Kiev",
            "event_type_slug": "consultation",
        }

        # RowMapping is a dict-like; use a real dict for unit test
        class FakeRow(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        booking = BookingDatabaseAdapter._fill_booking_dto(FakeRow(row))

        assert booking.uid == "abc-123"
        assert booking.user is not None
        assert booking.user.name == "Organizer"
        assert booking.client is not None
        assert booking.client.email == "client@test.com"

    def test_no_user_no_client(self) -> None:
        from datetime import UTC, datetime

        row = {
            "booking_id": 1,
            "uid": "abc-123",
            "title": "Test",
            "status": "accepted",
            "start_time": datetime(2026, 6, 15, 10, 0, tzinfo=UTC),
            "end_time": datetime(2026, 6, 15, 11, 0, tzinfo=UTC),
            "created_at": datetime(2026, 6, 1, tzinfo=UTC),
            "metadata": None,
            "from_reschedule": None,
            "user_id": None,
            "client_name": None,
        }

        class FakeRow(dict):
            def get(self, key, default=None):
                return super().get(key, default)

        booking = BookingDatabaseAdapter._fill_booking_dto(FakeRow(row))
        assert booking.user is None
        assert booking.client is None
```

- [ ] **Step 4: Run tests**

```bash
cd event-booking && uv run pytest tests/adapters/test_db.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booking/event_booking/adapters/sql.py event-booking/event_booking/adapters/db.py event-booking/tests/adapters/test_db.py
git commit -m "feat(event-booking): add SQL executor and Cal.com database adapter"
```

---

### Task 7: Event publisher adapter

**Files:**
- Create: `event-booking/event_booking/adapters/events.py`
- Create: `event-booking/tests/adapters/test_events.py`

- [ ] **Step 1: Create adapters/events.py**

Port from `calendar-bot/app/adapters/events.py`, adding `send_notification_command`:

```python
"""Publishes CloudEvents to event-receiver via HTTP POST."""

from typing import Any

import httpx
import structlog
from cloudevents.conversion import to_binary
from cloudevents.http import CloudEvent

from event_schemas.types import EventType

logger = structlog.get_logger(__name__)


class EventPublisher:
    def __init__(
        self,
        *,
        endpoint_url: str | None,
        api_key: str | None,
        source: str,
        timeout_seconds: float,
    ) -> None:
        self._endpoint_url = endpoint_url
        self._api_key = api_key
        self._source = source
        self._timeout_seconds = timeout_seconds

    async def send_event(
        self,
        booking_uid: str,
        event: EventType,
        data: dict[str, Any] | None = None,
    ) -> None:
        if not self._endpoint_url:
            return
        payload = {"booking_uid": booking_uid, **(data or {})}
        attributes = {
            "type": event.value,
            "source": self._source,
        }
        ce = CloudEvent(attributes, payload)
        headers, body = to_binary(ce)
        headers = dict(headers)
        if self._api_key:
            headers["Authorization"] = self._api_key
        try:
            async with httpx.AsyncClient(timeout=self._timeout_seconds) as client:
                await client.post(self._endpoint_url, headers=headers, content=body)
            logger.info("Event sent", event_type=event.value, booking_uid=booking_uid)
        except Exception:
            logger.exception("Failed to send event", event_type=event.value, booking_uid=booking_uid)

    async def send_notification_command(
        self,
        *,
        booking_uid: str,
        trigger_event: str,
        recipients: list[dict[str, str]],
        template_data: dict[str, Any],
    ) -> None:
        """Publish notification.send_requested event for event-notifier."""
        data: dict[str, Any] = {
            "booking_uid": booking_uid,
            "booking_id": booking_uid,
            "trigger_event": trigger_event,
            "recipients": recipients,
            "template_data": template_data,
        }
        await self.send_event(
            booking_uid=booking_uid,
            event=EventType.NOTIFICATION_SEND_REQUESTED,
            data=data,
        )
```

- [ ] **Step 2: Write tests**

Create `event-booking/tests/adapters/test_events.py`:

```python
"""Tests for EventPublisher."""

from unittest.mock import AsyncMock, patch

import pytest

from event_booking.adapters.events import EventPublisher
from event_schemas.types import EventType


@pytest.fixture
def publisher() -> EventPublisher:
    return EventPublisher(
        endpoint_url="http://test:8888/event/booking",
        api_key="test-key",
        source="booking",
        timeout_seconds=5.0,
    )


class TestSendEvent:
    @pytest.mark.asyncio
    async def test_skips_when_no_endpoint(self) -> None:
        pub = EventPublisher(endpoint_url=None, api_key=None, source="booking", timeout_seconds=5.0)
        # Should not raise
        await pub.send_event("uid-1", EventType.BOOKING_CREATED, {"key": "val"})

    @pytest.mark.asyncio
    async def test_sends_cloudevent(self, publisher: EventPublisher) -> None:
        with patch("event_booking.adapters.events.httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client_cls.return_value.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client_cls.return_value.__aexit__ = AsyncMock(return_value=False)
            mock_client.post = AsyncMock()

            await publisher.send_event("uid-1", EventType.BOOKING_CREATED, {"key": "val"})

            mock_client.post.assert_called_once()
            call_kwargs = mock_client.post.call_args
            assert call_kwargs[0][0] == "http://test:8888/event/booking"
            assert "Authorization" in call_kwargs[1]["headers"]


class TestSendNotificationCommand:
    @pytest.mark.asyncio
    async def test_sends_notification_command(self, publisher: EventPublisher) -> None:
        with patch.object(publisher, "send_event", new_callable=AsyncMock) as mock_send:
            await publisher.send_notification_command(
                booking_uid="uid-1",
                trigger_event="BOOKING_CREATED",
                recipients=[{"email": "org@test.com", "role": "organizer"}],
                template_data={"start_time": "2026-06-15T10:00:00Z"},
            )

            mock_send.assert_called_once()
            call_kwargs = mock_send.call_args
            assert call_kwargs[1]["event"] == EventType.NOTIFICATION_SEND_REQUESTED
```

- [ ] **Step 3: Run tests**

```bash
cd event-booking && uv run pytest tests/adapters/test_events.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booking/event_booking/adapters/events.py event-booking/tests/adapters/test_events.py
git commit -m "feat(event-booking): add CloudEvent publisher adapter"
```

---

### Task 8: GetStream chat adapter

**Files:**
- Create: `event-booking/event_booking/adapters/get_stream.py`
- Create: `event-booking/tests/adapters/test_get_stream.py`

- [ ] **Step 1: Create adapters/get_stream.py**

Port from `calendar-bot/app/adapters/get_stream.py`:

```python
"""GetStream Chat SDK wrapper with AES user ID encryption."""

import base64
import hashlib

import structlog
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.primitives.padding import PKCS7
from stream_chat import StreamChat

logger = structlog.get_logger(__name__)


class GetStreamAdapter:
    def __init__(self, chat_api_key: str, chat_api_secret: str, user_id_encryption_key: str) -> None:
        self._client = StreamChat(api_key=chat_api_key, api_secret=chat_api_secret)
        self._cipher_key = hashlib.sha256(user_id_encryption_key.encode()).digest()[:16]
        self._iv = b"\x00" * 16

    def _encode_user_id(self, *, user_id: str) -> str:
        cipher = Cipher(algorithms.AES128(self._cipher_key), modes.CBC(self._iv))
        encryptor = cipher.encryptor()
        padder = PKCS7(128).padder()
        padded = padder.update(user_id.encode()) + padder.finalize()
        encrypted = encryptor.update(padded) + encryptor.finalize()
        return base64.urlsafe_b64encode(encrypted).decode().rstrip("=")

    async def create_chat(self, *, channel_id: str, organizer_id: str, client_id: str) -> None:
        encoded_organizer = self._encode_user_id(user_id=organizer_id)
        encoded_client = self._encode_user_id(user_id=client_id)
        self._client.upsert_users([
            {"id": encoded_organizer, "name": organizer_id},
            {"id": encoded_client, "name": client_id},
        ])
        channel = self._client.channel("messaging", channel_id)
        channel.create(encoded_organizer, members=[encoded_organizer, encoded_client])
        logger.info("Chat created", channel_id=channel_id)

    async def delete_chat(self, *, channel_id: str) -> None:
        channel = self._client.channel("messaging", channel_id)
        channel.delete()
        logger.info("Chat deleted", channel_id=channel_id)

    async def send_message(self, *, channel_id: str, user_id: str, message: dict) -> None:
        encoded = self._encode_user_id(user_id=user_id)
        channel = self._client.channel("messaging", channel_id)
        channel.send_message(message, encoded)

    def create_token(self, *, user_id: str, name: str, expires_at: int) -> str:
        encoded = self._encode_user_id(user_id=user_id)
        self._client.upsert_users([{"id": encoded, "name": name}])
        return self._client.create_token(encoded, expiration=expires_at)
```

- [ ] **Step 2: Write tests**

Create `event-booking/tests/adapters/test_get_stream.py`:

```python
"""Tests for GetStreamAdapter."""

from event_booking.adapters.get_stream import GetStreamAdapter


class TestEncodeUserId:
    def test_deterministic(self) -> None:
        adapter = GetStreamAdapter(
            chat_api_key="key",
            chat_api_secret="secret",
            user_id_encryption_key="test-encryption-key",
        )
        result1 = adapter._encode_user_id(user_id="user@test.com")
        result2 = adapter._encode_user_id(user_id="user@test.com")
        assert result1 == result2

    def test_different_inputs_different_outputs(self) -> None:
        adapter = GetStreamAdapter(
            chat_api_key="key",
            chat_api_secret="secret",
            user_id_encryption_key="test-encryption-key",
        )
        result1 = adapter._encode_user_id(user_id="user1@test.com")
        result2 = adapter._encode_user_id(user_id="user2@test.com")
        assert result1 != result2
```

- [ ] **Step 3: Run tests**

```bash
cd event-booking && uv run pytest tests/adapters/test_get_stream.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booking/event_booking/adapters/get_stream.py event-booking/tests/adapters/test_get_stream.py
git commit -m "feat(event-booking): add GetStream chat adapter with AES encryption"
```

---

### Task 9: URL shortener adapter

**Files:**
- Create: `event-booking/event_booking/adapters/shortener.py`
- Create: `event-booking/tests/adapters/test_shortener.py`

- [ ] **Step 1: Create adapters/shortener.py**

Port from `calendar-bot/app/adapters/shortener.py`:

```python
"""Shortify URL shortener client."""

import httpx
import structlog

logger = structlog.get_logger(__name__)


class UrlShortenerAdapter:
    def __init__(self, *, base_url: str, api_key: str | None) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key

    def _headers(self) -> dict[str, str]:
        if not self._api_key:
            return {}
        return {"Authorization": f"Bearer {self._api_key}"}

    async def create_url(
        self, long_url: str, expires_at: float, not_before: float, external_id: str
    ) -> str | None:
        if not self._api_key:
            logger.warning("Shortener API key not set, skipping")
            return None
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.post(
                    f"{self._base_url}/api/v1/urls/shorten",
                    json={
                        "long_url": long_url,
                        "expires_at": expires_at,
                        "not_before": not_before,
                        "external_id": external_id,
                    },
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                ident = data.get("ident", "")
                return f"{self._base_url}/{ident}"
        except Exception:
            logger.exception("Failed to shorten URL")
            return None

    async def get_url(self, external_id: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.get(
                    f"{self._base_url}/api/v1/urls/external/{external_id}",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                ident = data.get("ident", "")
                return f"{self._base_url}/{ident}"
        except Exception:
            logger.exception("Failed to get URL")
            return None

    async def update_url_data(
        self,
        *,
        long_url: str,
        expires_at: float,
        not_before: float,
        new_external_id: str,
        old_external_id: str,
    ) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.patch(
                    f"{self._base_url}/api/v1/urls/external/{old_external_id}",
                    json={
                        "long_url": long_url,
                        "expires_at": expires_at,
                        "not_before": not_before,
                        "external_id": new_external_id,
                    },
                    headers=self._headers(),
                )
                resp.raise_for_status()
                data = resp.json()
                ident = data.get("ident", "")
                return f"{self._base_url}/{ident}"
        except Exception:
            logger.exception("Failed to update URL")
            return None

    async def delete_url(self, *, external_id: str) -> str | None:
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                resp = await client.delete(
                    f"{self._base_url}/api/v1/urls/external/{external_id}",
                    headers=self._headers(),
                )
                resp.raise_for_status()
                return external_id
        except Exception:
            logger.exception("Failed to delete URL")
            return None
```

- [ ] **Step 2: Write test**

Create `event-booking/tests/adapters/test_shortener.py`:

```python
"""Tests for UrlShortenerAdapter."""

import pytest

from event_booking.adapters.shortener import UrlShortenerAdapter


class TestCreateUrl:
    @pytest.mark.asyncio
    async def test_returns_none_when_no_api_key(self) -> None:
        adapter = UrlShortenerAdapter(base_url="http://short.test", api_key=None)
        result = await adapter.create_url("http://long.url", 999999.0, 0.0, "ext-1")
        assert result is None
```

- [ ] **Step 3: Run tests**

```bash
cd event-booking && uv run pytest tests/adapters/test_shortener.py -v
```
Expected: PASS

- [ ] **Step 4: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booking/event_booking/adapters/shortener.py event-booking/tests/adapters/test_shortener.py
git commit -m "feat(event-booking): add Shortify URL shortener adapter"
```

---

## Phase 3: Business Logic (Controllers)

### Task 10: Booking constraints analyzer

**Files:**
- Create: `event-booking/event_booking/controllers/constraints.py`
- Create: `event-booking/tests/controllers/test_constraints.py`

- [ ] **Step 1: Write failing tests**

Create `event-booking/tests/controllers/test_constraints.py`. Port test cases from `calendar-bot/tests/controllers/test_booking_constraints.py`:

```python
"""Tests for BookingConstraintsAnalyzer."""

from datetime import UTC, datetime, timedelta

import pytest

from event_booking.controllers.constraints import BookingConstraintsAnalyzer
from event_booking.dtos import AttendeeBookingDTO, BookingDTO, UserDTO


def _make_booking(start_time: datetime | None = None) -> BookingDTO:
    now = start_time or datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    return BookingDTO(
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        end_time=now + timedelta(hours=1),
        id=1,
        start_time=now,
        status="accepted",
        title="Test",
        uid="test-uid",
    )


def _make_attendee_booking(
    start_time: datetime,
    status: str = "accepted",
) -> AttendeeBookingDTO:
    return AttendeeBookingDTO(
        booking_id=1,
        booking_uid="att-uid",
        name="Client",
        email="client@test.com",
        start_time=start_time,
        end_time=start_time + timedelta(hours=1),
        status=status,
    )


class TestAnalyzeOnCreate:
    def test_allowed_when_no_history(self) -> None:
        analyzer = BookingConstraintsAnalyzer()
        result = analyzer.analyze_on_create(booking=_make_booking(), attendee_bookings=[])
        assert result.is_allowed is True

    def test_rejects_when_min_interval_violated(self) -> None:
        analyzer = BookingConstraintsAnalyzer()
        booking = _make_booking(datetime(2026, 6, 15, 10, 0, tzinfo=UTC))
        recent = _make_attendee_booking(datetime(2026, 6, 13, 10, 0, tzinfo=UTC))
        result = analyzer.analyze_on_create(booking=booking, attendee_bookings=[recent])
        assert result.is_allowed is False
        assert result.rejection_type == "min_interval"

    def test_rejects_when_monthly_limit_exceeded(self) -> None:
        analyzer = BookingConstraintsAnalyzer()
        booking = _make_booking(datetime(2026, 6, 20, 10, 0, tzinfo=UTC))
        bookings = [
            _make_attendee_booking(datetime(2026, 6, 1, 10, 0, tzinfo=UTC)),
            _make_attendee_booking(datetime(2026, 6, 8, 10, 0, tzinfo=UTC)),
        ]
        result = analyzer.analyze_on_create(booking=booking, attendee_bookings=bookings)
        assert result.is_allowed is False
        assert result.rejection_type == "month_limit"

    def test_rejects_when_active_booking_exists(self) -> None:
        analyzer = BookingConstraintsAnalyzer()
        booking = _make_booking(datetime(2026, 6, 20, 10, 0, tzinfo=UTC))
        future_active = _make_attendee_booking(datetime(2026, 6, 25, 10, 0, tzinfo=UTC))
        result = analyzer.analyze_on_create(booking=booking, attendee_bookings=[future_active])
        assert result.is_allowed is False
        assert result.has_active_booking is True

    def test_ignores_cancelled_bookings(self) -> None:
        analyzer = BookingConstraintsAnalyzer()
        booking = _make_booking(datetime(2026, 6, 15, 10, 0, tzinfo=UTC))
        cancelled = _make_attendee_booking(datetime(2026, 6, 13, 10, 0, tzinfo=UTC), status="cancelled")
        result = analyzer.analyze_on_create(booking=booking, attendee_bookings=[cancelled])
        assert result.is_allowed is True
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd event-booking && uv run pytest tests/controllers/test_constraints.py -v
```
Expected: FAIL (module not found)

- [ ] **Step 3: Implement constraints analyzer**

Create `event-booking/event_booking/controllers/constraints.py`. Port from `calendar-bot/app/controllers/booking_constraints.py`:

```python
"""Booking constraint validation logic."""

from datetime import datetime, timedelta

import structlog

from event_booking.dtos import AttendeeBookingDTO, BookingDTO, ConstraintsResult

logger = structlog.get_logger(__name__)

MIN_DAYS_BETWEEN_BOOKINGS = 7
MAX_BOOKINGS_PER_MONTH = 2
MAX_BOOKINGS_PER_YEAR = 10
ACTIVE_STATUSES = {"accepted", "rescheduled"}


class BookingConstraintsAnalyzer:
    def analyze_on_create(
        self,
        *,
        booking: BookingDTO,
        attendee_bookings: list[AttendeeBookingDTO],
    ) -> ConstraintsResult:
        active_bookings = [b for b in attendee_bookings if b.status in ACTIVE_STATUSES]

        # Check for active future bookings (overlap)
        future_active = [
            b for b in active_bookings
            if b.start_time > datetime.now(tz=booking.start_time.tzinfo)
            and b.booking_uid != booking.uid
        ]
        if future_active:
            earliest = min(future_active, key=lambda b: b.start_time)
            return ConstraintsResult(
                is_allowed=False,
                has_active_booking=True,
                active_booking_start=earliest.start_time,
                rejection_reasons=["Active future booking exists"],
            )

        # Count bookings in the same month
        same_month = [
            b for b in active_bookings
            if b.start_time.year == booking.start_time.year
            and b.start_time.month == booking.start_time.month
        ]
        if len(same_month) >= MAX_BOOKINGS_PER_MONTH:
            next_month = self._get_next_month_start(booking.start_time)
            return ConstraintsResult(
                is_allowed=False,
                available_from=next_month,
                rejection_type="month_limit",
                rejection_reasons=[f"Monthly limit of {MAX_BOOKINGS_PER_MONTH} reached"],
            )

        # Count bookings in the same year
        same_year = [
            b for b in active_bookings
            if b.start_time.year == booking.start_time.year
        ]
        if len(same_year) >= MAX_BOOKINGS_PER_YEAR:
            next_year_start = datetime(booking.start_time.year + 1, 1, 1, tzinfo=booking.start_time.tzinfo)
            return ConstraintsResult(
                is_allowed=False,
                available_from=next_year_start,
                rejection_type="year_limit",
                rejection_reasons=[f"Yearly limit of {MAX_BOOKINGS_PER_YEAR} reached"],
            )

        # Check minimum interval between bookings
        if active_bookings:
            latest = max(active_bookings, key=lambda b: b.start_time)
            days_since = (booking.start_time - latest.start_time).days
            if days_since < MIN_DAYS_BETWEEN_BOOKINGS:
                available_from = latest.start_time + timedelta(days=MIN_DAYS_BETWEEN_BOOKINGS)
                return ConstraintsResult(
                    is_allowed=False,
                    available_from=available_from,
                    rejection_type="min_interval",
                    rejection_reasons=[f"Minimum {MIN_DAYS_BETWEEN_BOOKINGS} days between bookings"],
                )

        return ConstraintsResult(is_allowed=True)

    @staticmethod
    def _get_next_month_start(target_date: datetime) -> datetime:
        if target_date.month == 12:
            return datetime(target_date.year + 1, 1, 1, tzinfo=target_date.tzinfo)
        return datetime(target_date.year, target_date.month + 1, 1, tzinfo=target_date.tzinfo)
```

- [ ] **Step 4: Run tests**

```bash
cd event-booking && uv run pytest tests/controllers/test_constraints.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booking/event_booking/controllers/constraints.py event-booking/tests/controllers/test_constraints.py
git commit -m "feat(event-booking): add booking constraints analyzer"
```

---

### Task 11: Meeting controller (Jitsi JWT + Shortify)

**Files:**
- Create: `event-booking/event_booking/controllers/meeting.py`
- Create: `event-booking/tests/controllers/test_meeting.py`

- [ ] **Step 1: Write failing tests**

Create `event-booking/tests/controllers/test_meeting.py`:

```python
"""Tests for MeetingController."""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from event_booking.controllers.meeting import MeetingController
from event_booking.dtos import BookingDTO


@pytest.fixture
def booking() -> BookingDTO:
    return BookingDTO(
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        end_time=datetime(2026, 6, 15, 11, 0, tzinfo=UTC),
        id=1,
        start_time=datetime(2026, 6, 15, 10, 0, tzinfo=UTC),
        status="accepted",
        title="Test",
        uid="booking-uid-123",
    )


@pytest.fixture
def mock_shortener() -> AsyncMock:
    shortener = AsyncMock()
    shortener.create_url = AsyncMock(return_value="https://short.test/abc")
    shortener.update_url_data = AsyncMock(return_value="https://short.test/abc")
    shortener.delete_url = AsyncMock(return_value="ext-id")
    return shortener


@pytest.fixture
def mock_chat_client() -> MagicMock:
    chat = MagicMock()
    chat.create_token = MagicMock(return_value="chat-jwt-token")
    return chat


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_events() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def controller(mock_shortener, mock_chat_client, mock_db, mock_events) -> MeetingController:
    return MeetingController(
        shortener=mock_shortener,
        chat_client=mock_chat_client,
        db=mock_db,
        events=mock_events,
        jitsi_jwt_secret="test-secret",
        jitsi_jwt_aud="test-aud",
        jitsi_jwt_iss="test-iss",
        meeting_host_url="https://meet.test",
    )


class TestCreateMeetingUrl:
    @pytest.mark.asyncio
    async def test_returns_shortened_url(self, controller, booking) -> None:
        result = await controller.create_meeting_url(
            booking=booking,
            participant_id="org@test.com",
            participant_name="Organizer",
            participant_email="org@test.com",
        )
        assert result == "https://short.test/abc"

    @pytest.mark.asyncio
    async def test_falls_back_to_long_url(self, controller, booking, mock_shortener) -> None:
        mock_shortener.create_url = AsyncMock(return_value=None)
        result = await controller.create_meeting_url(
            booking=booking,
            participant_id="org@test.com",
            participant_name="Organizer",
            participant_email="org@test.com",
        )
        assert "meet.test" in result
        assert "booking-uid-123" in result

    @pytest.mark.asyncio
    async def test_sends_meeting_url_created_event(self, controller, booking, mock_events) -> None:
        await controller.create_meeting_url(
            booking=booking,
            participant_id="org@test.com",
            participant_name="Organizer",
            participant_email="org@test.com",
        )
        mock_events.send_event.assert_called_once()


class TestDeleteMeetingUrl:
    @pytest.mark.asyncio
    async def test_deletes_and_sends_event(self, controller, booking, mock_shortener, mock_events) -> None:
        await controller.delete_meeting_url(booking=booking)
        mock_shortener.delete_url.assert_called_once()
        mock_events.send_event.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd event-booking && uv run pytest tests/controllers/test_meeting.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement meeting controller**

Create `event-booking/event_booking/controllers/meeting.py`. Port from `calendar-bot/app/controllers/meeting.py`:

```python
"""Jitsi JWT token generation and Shortify URL management."""

import time
from urllib.parse import quote

import jwt
import structlog

from event_booking.dtos import BookingDTO
from event_booking.interfaces.chat import IChatClient
from event_booking.interfaces.db import IBookingDatabaseAdapter
from event_booking.interfaces.events import IEventPublisher
from event_booking.interfaces.shortener import IUrlShortener
from event_schemas.types import EventType

logger = structlog.get_logger(__name__)

BUFFER_MINUTES = 5


class MeetingController:
    def __init__(
        self,
        *,
        shortener: IUrlShortener,
        chat_client: IChatClient,
        db: IBookingDatabaseAdapter,
        events: IEventPublisher,
        jitsi_jwt_secret: str,
        jitsi_jwt_aud: str,
        jitsi_jwt_iss: str,
        meeting_host_url: str,
    ) -> None:
        self._shortener = shortener
        self._chat_client = chat_client
        self._db = db
        self._events = events
        self._jwt_secret = jitsi_jwt_secret
        self._jwt_aud = jitsi_jwt_aud
        self._jwt_iss = jitsi_jwt_iss
        self._meeting_host_url = meeting_host_url.rstrip("/")

    def _create_jitsi_token(
        self,
        *,
        booking: BookingDTO,
        participant_name: str,
        participant_email: str,
        role: str,
    ) -> str:
        nbf = self._get_not_before(booking.start_time)
        exp = self._get_expiration(booking.end_time)
        payload = {
            "aud": self._jwt_aud,
            "iss": self._jwt_iss,
            "sub": "*",
            "room": booking.uid,
            "iat": int(time.time()),
            "nbf": int(nbf),
            "exp": int(exp),
            "context": {
                "user": {
                    "name": participant_name,
                    "role": role,
                    "email": participant_email,
                }
            },
        }
        return jwt.encode(payload, self._jwt_secret, algorithm="HS256")

    async def _generate_long_url(
        self,
        *,
        booking: BookingDTO,
        participant_name: str,
        participant_email: str,
        role: str,
    ) -> str:
        jwt_video = self._create_jitsi_token(
            booking=booking,
            participant_name=participant_name,
            participant_email=participant_email,
            role=role,
        )
        jwt_chat = self._chat_client.create_token(
            user_id=participant_email,
            name=participant_name,
            expires_at=int(self._get_expiration(booking.end_time)),
        )
        return f"{self._meeting_host_url}/{booking.uid}?jwt_video={quote(jwt_video)}&jwt_chat={quote(jwt_chat)}"

    async def create_meeting_url(
        self,
        *,
        booking: BookingDTO,
        participant_id: str,
        participant_name: str,
        participant_email: str,
        is_update_url_data: bool = False,
        external_id_prefix: str = "",
    ) -> str:
        role = "client" if external_id_prefix else "organizer"
        long_url = await self._generate_long_url(
            booking=booking,
            participant_name=participant_name,
            participant_email=participant_email,
            role=role,
        )
        external_id = f"{external_id_prefix}{booking.uid}"
        nbf = self._get_not_before(booking.start_time)
        exp = self._get_expiration(booking.end_time)

        short_url: str | None = None
        if is_update_url_data and booking.from_reschedule:
            old_external_id = f"{external_id_prefix}{booking.from_reschedule}"
            short_url = await self._shortener.update_url_data(
                long_url=long_url,
                expires_at=exp,
                not_before=nbf,
                new_external_id=external_id,
                old_external_id=old_external_id,
            )
        if not short_url:
            short_url = await self._shortener.create_url(long_url, exp, nbf, external_id)

        meeting_url = short_url or long_url
        await self._events.send_event(
            booking.uid,
            EventType.MEETING_URL_CREATED,
            {"meeting_url": meeting_url, "participant_email": participant_email, "role": role},
        )
        return meeting_url

    async def delete_meeting_url(self, *, booking: BookingDTO, external_id_prefix: str = "") -> None:
        external_id = f"{external_id_prefix}{booking.uid}"
        await self._shortener.delete_url(external_id=external_id)
        role = "client" if external_id_prefix else "organizer"
        await self._events.send_event(
            booking.uid,
            EventType.MEETING_URL_DELETED,
            {"participant_role": role},
        )

    @staticmethod
    def _get_not_before(start_time: "datetime") -> float:
        from datetime import timedelta
        return (start_time - timedelta(minutes=BUFFER_MINUTES)).timestamp()

    @staticmethod
    def _get_expiration(end_time: "datetime") -> float:
        from datetime import timedelta
        return (end_time + timedelta(minutes=BUFFER_MINUTES)).timestamp()
```

- [ ] **Step 4: Run tests**

```bash
cd event-booking && uv run pytest tests/controllers/test_meeting.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booking/event_booking/controllers/meeting.py event-booking/tests/controllers/test_meeting.py
git commit -m "feat(event-booking): add meeting controller (Jitsi JWT + Shortify)"
```

---

### Task 12: Chat controller

**Files:**
- Create: `event-booking/event_booking/controllers/chat.py`
- Create: `event-booking/tests/controllers/test_chat.py`

- [ ] **Step 1: Write failing tests**

Create `event-booking/tests/controllers/test_chat.py`:

```python
"""Tests for ChatController."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from event_booking.controllers.chat import ChatController


@pytest.fixture
def mock_chat_client() -> MagicMock:
    client = MagicMock()
    client.create_chat = AsyncMock()
    client.delete_chat = AsyncMock()
    client.send_message = AsyncMock()
    client.create_token = MagicMock(return_value="token")
    return client


@pytest.fixture
def mock_events() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def controller(mock_chat_client, mock_events) -> ChatController:
    return ChatController(chat_client=mock_chat_client, events=mock_events)


class TestCreateChat:
    @pytest.mark.asyncio
    async def test_creates_channel_and_sends_event(self, controller, mock_chat_client, mock_events) -> None:
        await controller.create_chat(channel_id="booking-uid", organizer_id="org@test.com", client_id="client@test.com")
        mock_chat_client.create_chat.assert_called_once_with(
            channel_id="booking-uid",
            organizer_id="org@test.com",
            client_id="client@test.com",
        )
        mock_events.send_event.assert_called_once()


class TestDeleteChat:
    @pytest.mark.asyncio
    async def test_deletes_channel_and_sends_event(self, controller, mock_chat_client, mock_events) -> None:
        await controller.delete_chat(channel_id="booking-uid", booking_uid="booking-uid")
        mock_chat_client.delete_chat.assert_called_once_with(channel_id="booking-uid")
        mock_events.send_event.assert_called_once()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd event-booking && uv run pytest tests/controllers/test_chat.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement chat controller**

Create `event-booking/event_booking/controllers/chat.py`:

```python
"""GetStream chat lifecycle management."""

import structlog

from event_booking.interfaces.chat import IChatClient
from event_booking.interfaces.events import IEventPublisher
from event_schemas.types import EventType

logger = structlog.get_logger(__name__)


class ChatController:
    def __init__(self, *, chat_client: IChatClient, events: IEventPublisher) -> None:
        self._chat_client = chat_client
        self._events = events

    async def create_chat(self, *, channel_id: str, organizer_id: str, client_id: str) -> None:
        try:
            await self._chat_client.create_chat(
                channel_id=channel_id,
                organizer_id=organizer_id,
                client_id=client_id,
            )
            await self._events.send_event(
                channel_id,
                EventType.CHAT_CREATED,
                {"organizer_id": organizer_id, "client_id": client_id},
            )
        except Exception:
            logger.exception("Failed to create chat", channel_id=channel_id)

    async def delete_chat(self, *, channel_id: str, booking_uid: str) -> None:
        try:
            await self._chat_client.delete_chat(channel_id=channel_id)
            await self._events.send_event(booking_uid, EventType.CHAT_DELETED)
        except Exception:
            logger.exception("Failed to delete chat", channel_id=channel_id)

    async def send_message(self, *, channel_id: str, user_id: str, message: dict) -> None:
        try:
            await self._chat_client.send_message(channel_id=channel_id, user_id=user_id, message=message)
        except Exception:
            logger.exception("Failed to send message", channel_id=channel_id)
```

- [ ] **Step 4: Run tests**

```bash
cd event-booking && uv run pytest tests/controllers/test_chat.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booking/event_booking/controllers/chat.py event-booking/tests/controllers/test_chat.py
git commit -m "feat(event-booking): add chat controller (GetStream lifecycle)"
```

---

### Task 13: Main booking controller (orchestrator)

**Files:**
- Create: `event-booking/event_booking/controllers/booking.py`
- Create: `event-booking/tests/controllers/test_booking.py`
- Create: `event-booking/tests/controllers/conftest.py`
- Create: `event-booking/tests/conftest.py`
- Create: `event-booking/tests/factories.py`

- [ ] **Step 1: Create test fixtures and factories**

Create `event-booking/tests/factories.py`:

```python
"""Polyfactory DTO builders for tests."""

from datetime import UTC, datetime, timedelta

from event_booking.dtos import AttendeeBookingDTO, BookingClientDTO, BookingDTO, UserDTO


def make_user(
    *,
    id: int = 1,
    name: str = "Organizer",
    email: str = "organizer@test.com",
    time_zone: str = "Europe/Moscow",
    telegram_chat_id: int | None = 12345,
) -> UserDTO:
    return UserDTO(id=id, name=name, email=email, locked=False, time_zone=time_zone, telegram_chat_id=telegram_chat_id)


def make_client(
    *,
    name: str = "Client",
    email: str = "client@test.com",
    time_zone: str = "Europe/Kiev",
) -> BookingClientDTO:
    return BookingClientDTO(name=name, email=email, time_zone=time_zone)


def make_booking(
    *,
    uid: str = "booking-uid-123",
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    user: UserDTO | None = None,
    client: BookingClientDTO | None = None,
    status: str = "accepted",
) -> BookingDTO:
    st = start_time or datetime(2026, 6, 15, 10, 0, tzinfo=UTC)
    et = end_time or (st + timedelta(hours=1))
    return BookingDTO(
        created_at=datetime(2026, 6, 1, tzinfo=UTC),
        end_time=et,
        id=1,
        start_time=st,
        status=status,
        title="Test Booking",
        uid=uid,
        user=user or make_user(),
        client=client or make_client(),
    )
```

Create `event-booking/tests/conftest.py`:

```python
"""Shared test fixtures."""

from unittest.mock import AsyncMock, MagicMock

import pytest


@pytest.fixture
def mock_db() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_events() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_chat_controller() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def mock_meeting_controller() -> AsyncMock:
    mock = AsyncMock()
    mock.create_meeting_url = AsyncMock(return_value="https://short.test/abc")
    return mock


@pytest.fixture
def mock_constraints_analyzer() -> MagicMock:
    from event_booking.dtos import ConstraintsResult
    mock = MagicMock()
    mock.analyze_on_create = MagicMock(return_value=ConstraintsResult(is_allowed=True))
    return mock
```

- [ ] **Step 2: Write failing tests for booking controller**

Create `event-booking/tests/controllers/conftest.py`:

```python
"""Controller test fixtures."""
```

Create `event-booking/tests/controllers/test_booking.py`:

```python
"""Tests for BookingController."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from event_booking.controllers.booking import BookingController
from event_booking.dtos import ConstraintsResult
from event_schemas.types import EventType
from tests.factories import make_booking, make_client, make_user


@pytest.fixture
def controller(mock_db, mock_events, mock_chat_controller, mock_meeting_controller, mock_constraints_analyzer):
    return BookingController(
        db=mock_db,
        events=mock_events,
        chat_controller=mock_chat_controller,
        meeting_controller=mock_meeting_controller,
        constraints_analyzer=mock_constraints_analyzer,
        is_enable_constraints=True,
    )


class TestHandleCreated:
    @pytest.mark.asyncio
    async def test_creates_chat_and_meeting_urls(self, controller, mock_db, mock_chat_controller, mock_meeting_controller) -> None:
        booking = make_booking()
        mock_db.get_booking = AsyncMock(return_value=booking)
        mock_db.get_attendee_bookings_by_email = AsyncMock(return_value=[])

        await controller.handle_created(booking_uid="booking-uid-123")

        mock_chat_controller.create_chat.assert_called_once()
        assert mock_meeting_controller.create_meeting_url.call_count == 2  # organizer + client

    @pytest.mark.asyncio
    async def test_rejects_when_constraints_fail(self, controller, mock_db, mock_constraints_analyzer, mock_events) -> None:
        booking = make_booking()
        mock_db.get_booking = AsyncMock(return_value=booking)
        mock_db.get_attendee_bookings_by_email = AsyncMock(return_value=[])
        mock_constraints_analyzer.analyze_on_create = MagicMock(
            return_value=ConstraintsResult(
                is_allowed=False,
                rejection_type="month_limit",
                rejection_reasons=["Monthly limit reached"],
            )
        )

        await controller.handle_created(booking_uid="booking-uid-123")

        mock_db.delete_booking_and_attendee_by_booking_id.assert_called_once()
        mock_events.send_notification_command.assert_called_once()


class TestHandleCancelled:
    @pytest.mark.asyncio
    async def test_deletes_chat_and_meeting_urls(self, controller, mock_db, mock_chat_controller, mock_meeting_controller, mock_events) -> None:
        booking = make_booking()
        mock_db.get_booking = AsyncMock(return_value=booking)

        await controller.handle_cancelled(booking_uid="booking-uid-123", cancellation_reason="Client request")

        mock_chat_controller.delete_chat.assert_called_once()
        assert mock_meeting_controller.delete_meeting_url.call_count == 2  # organizer + client
        mock_events.send_notification_command.assert_called_once()


class TestHandleRescheduled:
    @pytest.mark.asyncio
    async def test_updates_meeting_urls_and_notifies(self, controller, mock_db, mock_meeting_controller, mock_events) -> None:
        booking = make_booking(uid="new-uid")
        booking.from_reschedule = "old-uid"
        mock_db.get_booking = AsyncMock(return_value=booking)

        await controller.handle_rescheduled(booking_uid="new-uid", previous_start_time="2026-06-14T10:00:00Z")

        assert mock_meeting_controller.create_meeting_url.call_count == 2
        mock_events.send_notification_command.assert_called_once()
```

- [ ] **Step 3: Run tests to verify they fail**

```bash
cd event-booking && uv run pytest tests/controllers/test_booking.py -v
```
Expected: FAIL

- [ ] **Step 4: Implement booking controller**

Create `event-booking/event_booking/controllers/booking.py`. This is the main orchestrator. Port logic from `calendar-bot/app/controllers/booking.py`:

```python
"""Main booking orchestrator: dispatches by event type and coordinates side effects."""

from typing import Any

import structlog

from event_booking.dtos import BookingDTO
from event_booking.interfaces.constraints import IBookingConstraintsAnalyzer
from event_booking.interfaces.db import IBookingDatabaseAdapter
from event_booking.interfaces.events import IEventPublisher
from event_booking.interfaces.meeting import IMeetingController
from event_schemas.types import EventType, TriggerEvent

logger = structlog.get_logger(__name__)

CLIENT_PREFIX = "client_"


class BookingController:
    def __init__(
        self,
        *,
        db: IBookingDatabaseAdapter,
        events: IEventPublisher,
        chat_controller: Any,  # IChatController — avoid circular, uses duck typing
        meeting_controller: IMeetingController,
        constraints_analyzer: IBookingConstraintsAnalyzer,
        is_enable_constraints: bool = False,
    ) -> None:
        self._db = db
        self._events = events
        self._chat = chat_controller
        self._meeting = meeting_controller
        self._constraints = constraints_analyzer
        self._is_enable_constraints = is_enable_constraints

    async def handle_created(self, booking_uid: str) -> None:
        booking = await self._db.get_booking(booking_uid)
        if not booking:
            logger.warning("Booking not found", booking_uid=booking_uid)
            return

        # Constraint validation
        if self._is_enable_constraints and booking.client:
            attendee_bookings = await self._db.get_attendee_bookings_by_email(email=booking.client.email)
            result = self._constraints.analyze_on_create(booking=booking, attendee_bookings=attendee_bookings)
            if not result.is_allowed:
                logger.info("Booking rejected by constraints", booking_uid=booking_uid, reason=result.rejection_type)
                await self._db.delete_booking_and_attendee_by_booking_id(booking_id=booking.id)
                await self._send_rejection_notification(booking=booking, result=result)
                await self._events.send_event(booking_uid, EventType.BOOKING_REJECTED, {
                    "client_email": booking.client.email,
                    "rejection_type": result.rejection_type,
                    "rejection_reasons": result.rejection_reasons,
                })
                return

        await self._process_booking_flow(booking)

    async def handle_rescheduled(self, booking_uid: str, previous_start_time: str | None = None) -> None:
        booking = await self._db.get_booking(booking_uid)
        if not booking:
            logger.warning("Booking not found", booking_uid=booking_uid)
            return
        await self._process_booking_flow(booking, is_update_url_data=True)
        template_data = self._build_template_data(booking)
        if previous_start_time:
            template_data["previous_start_time"] = previous_start_time
        await self._send_notification(
            booking=booking,
            trigger_event=TriggerEvent.BOOKING_RESCHEDULED,
            template_data=template_data,
        )

    async def handle_reassigned(
        self,
        booking_uid: str,
        previous_organizer_email: str | None = None,
    ) -> None:
        booking = await self._db.get_booking(booking_uid)
        if not booking:
            logger.warning("Booking not found", booking_uid=booking_uid)
            return

        # Delete old chat, create new
        await self._chat.delete_chat(channel_id=booking_uid, booking_uid=booking_uid)
        if booking.user and booking.client:
            await self._create_chat_with_welcome(booking)

        # Create new meeting URLs
        await self._create_meeting_urls(booking)

        # Notify new organizer + client
        template_data = self._build_template_data(booking)
        if previous_organizer_email:
            template_data["previous_organizer_email"] = previous_organizer_email
        await self._send_notification(
            booking=booking,
            trigger_event=TriggerEvent.BOOKING_REASSIGNED,
            template_data=template_data,
        )

    async def handle_cancelled(self, booking_uid: str, cancellation_reason: str | None = None) -> None:
        booking = await self._db.get_booking(booking_uid)
        if not booking:
            logger.warning("Booking not found", booking_uid=booking_uid)
            return

        # Notify before deleting resources
        template_data = self._build_template_data(booking)
        if cancellation_reason:
            template_data["cancellation_reason"] = cancellation_reason
        await self._send_notification(
            booking=booking,
            trigger_event=TriggerEvent.BOOKING_CANCELLED,
            template_data=template_data,
        )

        # Cleanup
        await self._chat.delete_chat(channel_id=booking_uid, booking_uid=booking_uid)
        await self._meeting.delete_meeting_url(booking=booking)
        await self._meeting.delete_meeting_url(booking=booking, external_id_prefix=CLIENT_PREFIX)

    async def _process_booking_flow(self, booking: BookingDTO, is_update_url_data: bool = False) -> None:
        if booking.user and booking.client:
            await self._create_chat_with_welcome(booking)

        organizer_url = await self._create_meeting_urls(booking, is_update_url_data=is_update_url_data)

        if not is_update_url_data:
            template_data = self._build_template_data(booking, organizer_meeting_url=organizer_url)
            await self._send_notification(
                booking=booking,
                trigger_event=TriggerEvent.BOOKING_CREATED,
                template_data=template_data,
            )

    async def _create_chat_with_welcome(self, booking: BookingDTO) -> None:
        if not booking.user or not booking.client:
            return
        await self._chat.create_chat(
            channel_id=booking.uid,
            organizer_id=booking.user.email,
            client_id=booking.client.email,
        )
        await self._chat.send_message(
            channel_id=booking.uid,
            user_id=booking.user.email,
            message={"text": f"Здравствуйте, я {booking.user.name}, ваш волонтер-психолог."},
        )
        await self._chat.send_message(
            channel_id=booking.uid,
            user_id=booking.user.email,
            message={"text": "Программа запросит доступ к микрофону/камере — разрешите. Нажмите «Присоединиться к звонку»."},
        )

    async def _create_meeting_urls(
        self, booking: BookingDTO, is_update_url_data: bool = False
    ) -> str:
        organizer_url = ""
        if booking.user:
            organizer_url = await self._meeting.create_meeting_url(
                booking=booking,
                participant_id=str(booking.user.id),
                participant_name=booking.user.name,
                participant_email=booking.user.email,
                is_update_url_data=is_update_url_data,
            )
        if booking.client:
            await self._meeting.create_meeting_url(
                booking=booking,
                participant_id=booking.client.email,
                participant_name=booking.client.name,
                participant_email=booking.client.email,
                is_update_url_data=is_update_url_data,
                external_id_prefix=CLIENT_PREFIX,
            )
        return organizer_url

    async def _send_notification(
        self,
        *,
        booking: BookingDTO,
        trigger_event: TriggerEvent,
        template_data: dict[str, Any],
    ) -> None:
        recipients: list[dict[str, str]] = []
        if booking.user:
            recipients.append({"email": booking.user.email, "role": "organizer"})
        if booking.client:
            recipients.append({"email": booking.client.email, "role": "client"})
        if not recipients:
            return
        await self._events.send_notification_command(
            booking_uid=booking.uid,
            trigger_event=trigger_event.value,
            recipients=recipients,
            template_data=template_data,
        )

    async def _send_rejection_notification(self, *, booking: BookingDTO, result: Any) -> None:
        if not booking.client:
            return
        await self._events.send_notification_command(
            booking_uid=booking.uid,
            trigger_event=TriggerEvent.BOOKING_REJECTED.value,
            recipients=[{"email": booking.client.email, "role": "client"}],
            template_data={
                "rejection_type": result.rejection_type,
                "rejection_reasons": result.rejection_reasons,
                "available_from": str(result.available_from) if result.available_from else None,
                "has_active_booking": result.has_active_booking,
                "active_booking_start": str(result.active_booking_start) if result.active_booking_start else None,
            },
        )

    @staticmethod
    def _build_template_data(booking: BookingDTO, organizer_meeting_url: str | None = None) -> dict[str, Any]:
        data: dict[str, Any] = {
            "booking_uid": booking.uid,
            "start_time": booking.start_time.isoformat(),
            "end_time": booking.end_time.isoformat(),
            "title": booking.title,
        }
        if booking.user:
            data["organizer_name"] = booking.user.name
            data["organizer_email"] = booking.user.email
            data["organizer_time_zone"] = booking.user.time_zone
        if booking.client:
            data["client_name"] = booking.client.name
            data["client_email"] = booking.client.email
            data["client_time_zone"] = booking.client.time_zone
        if organizer_meeting_url:
            data["meeting_url"] = organizer_meeting_url
        return data
```

- [ ] **Step 5: Run tests**

```bash
cd event-booking && uv run pytest tests/controllers/test_booking.py -v
```
Expected: PASS

- [ ] **Step 6: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booking/event_booking/controllers/booking.py event-booking/tests/controllers/test_booking.py event-booking/tests/controllers/conftest.py event-booking/tests/conftest.py event-booking/tests/factories.py
git commit -m "feat(event-booking): add main booking controller (orchestrator)"
```

---

### Task 14: RabbitMQ consumer

**Files:**
- Create: `event-booking/event_booking/consumer.py`
- Create: `event-booking/tests/test_consumer.py`

- [ ] **Step 1: Write failing test**

Create `event-booking/tests/test_consumer.py`:

```python
"""Tests for BookingConsumer event dispatch."""

from unittest.mock import AsyncMock

import pytest

from event_booking.consumer import BookingConsumer


@pytest.fixture
def mock_booking_controller() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def consumer(mock_booking_controller) -> BookingConsumer:
    return BookingConsumer(booking_controller=mock_booking_controller)


class TestDispatch:
    @pytest.mark.asyncio
    async def test_dispatches_created(self, consumer, mock_booking_controller) -> None:
        await consumer.dispatch(
            event_type="booking.created",
            booking_uid="uid-1",
            data={"start_time": "2026-06-15T10:00:00Z"},
        )
        mock_booking_controller.handle_created.assert_called_once_with(booking_uid="uid-1")

    @pytest.mark.asyncio
    async def test_dispatches_cancelled(self, consumer, mock_booking_controller) -> None:
        await consumer.dispatch(
            event_type="booking.cancelled",
            booking_uid="uid-1",
            data={"cancellation_reason": "Client request"},
        )
        mock_booking_controller.handle_cancelled.assert_called_once_with(
            booking_uid="uid-1",
            cancellation_reason="Client request",
        )

    @pytest.mark.asyncio
    async def test_dispatches_rescheduled(self, consumer, mock_booking_controller) -> None:
        await consumer.dispatch(
            event_type="booking.rescheduled",
            booking_uid="uid-1",
            data={"previous_booking": {"start_time": "2026-06-14T10:00:00Z"}},
        )
        mock_booking_controller.handle_rescheduled.assert_called_once()

    @pytest.mark.asyncio
    async def test_dispatches_reassigned(self, consumer, mock_booking_controller) -> None:
        await consumer.dispatch(
            event_type="booking.reassigned",
            booking_uid="uid-1",
            data={"previous_organizer": {"email": "old@test.com"}},
        )
        mock_booking_controller.handle_reassigned.assert_called_once()

    @pytest.mark.asyncio
    async def test_ignores_unknown_event(self, consumer, mock_booking_controller) -> None:
        await consumer.dispatch(
            event_type="booking.unknown",
            booking_uid="uid-1",
            data={},
        )
        mock_booking_controller.handle_created.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd event-booking && uv run pytest tests/test_consumer.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement consumer**

Create `event-booking/event_booking/consumer.py`:

```python
"""FastStream RabbitMQ consumer for booking lifecycle events."""

from typing import Any

import structlog
from cloudevents.http import from_http
from faststream.rabbit import RabbitBroker, RabbitExchange, RabbitQueue

from event_booking.controllers.booking import BookingController
from event_schemas.types import EventType

logger = structlog.get_logger(__name__)

HANDLED_EVENTS = {
    EventType.BOOKING_CREATED.value,
    EventType.BOOKING_RESCHEDULED.value,
    EventType.BOOKING_REASSIGNED.value,
    EventType.BOOKING_CANCELLED.value,
}


class BookingConsumer:
    def __init__(self, *, booking_controller: BookingController) -> None:
        self._controller = booking_controller

    async def dispatch(self, event_type: str, booking_uid: str, data: dict[str, Any]) -> None:
        if event_type not in HANDLED_EVENTS:
            logger.warning("Unknown event type, skipping", event_type=event_type)
            return

        logger.info("Processing booking event", event_type=event_type, booking_uid=booking_uid)

        if event_type == EventType.BOOKING_CREATED.value:
            await self._controller.handle_created(booking_uid=booking_uid)
            return

        if event_type == EventType.BOOKING_CANCELLED.value:
            await self._controller.handle_cancelled(
                booking_uid=booking_uid,
                cancellation_reason=data.get("cancellation_reason"),
            )
            return

        if event_type == EventType.BOOKING_RESCHEDULED.value:
            prev = data.get("previous_booking", {})
            await self._controller.handle_rescheduled(
                booking_uid=booking_uid,
                previous_start_time=prev.get("start_time"),
            )
            return

        if event_type == EventType.BOOKING_REASSIGNED.value:
            prev_org = data.get("previous_organizer", {})
            await self._controller.handle_reassigned(
                booking_uid=booking_uid,
                previous_organizer_email=prev_org.get("email"),
            )

    def register(self, broker: RabbitBroker, exchange: RabbitExchange, queue_name: str) -> None:
        """Register the consumer with FastStream broker."""
        queue = RabbitQueue(
            name=queue_name,
            durable=True,
            routing_key=queue_name,
            declare=True,
            arguments={
                "x-max-priority": 10,
                "x-dead-letter-exchange": "events.dlx",
                "x-dead-letter-routing-key": f"{queue_name}.dlq",
            },
        )

        @broker.subscriber(queue=queue, exchange=exchange)
        async def handle(body: bytes, headers: dict) -> None:
            try:
                ce = from_http(headers=headers, data=body)
                event_type = ce["type"]
                booking_uid = ce.get("bookingid") or ce.get("booking_id") or ""
                data = ce.data if isinstance(ce.data, dict) else {}
                await self.dispatch(event_type=event_type, booking_uid=booking_uid, data=data)
            except Exception:
                logger.exception("Failed to process message")
                raise
```

- [ ] **Step 4: Run tests**

```bash
cd event-booking && uv run pytest tests/test_consumer.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booking/event_booking/consumer.py event-booking/tests/test_consumer.py
git commit -m "feat(event-booking): add RabbitMQ consumer with event dispatch"
```

---

### Task 15: Reminder scheduler

**Files:**
- Create: `event-booking/event_booking/scheduler.py`
- Create: `event-booking/tests/test_scheduler.py`

- [ ] **Step 1: Write failing test**

Create `event-booking/tests/test_scheduler.py`:

```python
"""Tests for ReminderScheduler."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from event_booking.scheduler import ReminderScheduler
from tests.factories import make_booking, make_client, make_user


class TestSendReminders:
    @pytest.mark.asyncio
    async def test_sends_reminder_for_upcoming_booking(self) -> None:
        booking = make_booking()
        mock_db = AsyncMock()
        mock_db.get_bookings = AsyncMock(return_value=[booking])
        mock_events = AsyncMock()

        scheduler = ReminderScheduler(
            db=mock_db,
            events=mock_events,
            interval_seconds=300,
            shift_from_minutes=55,
            shift_to_minutes=65,
        )

        count = await scheduler.send_reminders()

        assert count == 1
        mock_events.send_notification_command.assert_called_once()

    @pytest.mark.asyncio
    async def test_skips_when_no_bookings(self) -> None:
        mock_db = AsyncMock()
        mock_db.get_bookings = AsyncMock(return_value=[])
        mock_events = AsyncMock()

        scheduler = ReminderScheduler(
            db=mock_db,
            events=mock_events,
            interval_seconds=300,
            shift_from_minutes=55,
            shift_to_minutes=65,
        )

        count = await scheduler.send_reminders()

        assert count == 0
        mock_events.send_notification_command.assert_not_called()
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd event-booking && uv run pytest tests/test_scheduler.py -v
```
Expected: FAIL

- [ ] **Step 3: Implement scheduler**

Create `event-booking/event_booking/scheduler.py`:

```python
"""Periodic reminder scheduler — queries Cal.com DB and publishes reminder events."""

import asyncio
from datetime import UTC, datetime, timedelta

import structlog

from event_booking.interfaces.db import IBookingDatabaseAdapter
from event_booking.interfaces.events import IEventPublisher
from event_schemas.types import TriggerEvent

logger = structlog.get_logger(__name__)


class ReminderScheduler:
    def __init__(
        self,
        *,
        db: IBookingDatabaseAdapter,
        events: IEventPublisher,
        interval_seconds: int,
        shift_from_minutes: int,
        shift_to_minutes: int,
    ) -> None:
        self._db = db
        self._events = events
        self._interval_seconds = interval_seconds
        self._shift_from = shift_from_minutes
        self._shift_to = shift_to_minutes
        self._running = False

    async def send_reminders(self) -> int:
        """Query upcoming bookings and send reminder notifications. Returns count sent."""
        now = datetime.now(UTC)
        start_from = now + timedelta(minutes=self._shift_from)
        start_to = now + timedelta(minutes=self._shift_to)

        bookings = await self._db.get_bookings(start_from, start_to)
        if not bookings:
            return 0

        count = 0
        for booking in bookings:
            if not booking.client:
                continue

            recipients = [{"email": booking.client.email, "role": "client"}]
            template_data = {
                "booking_uid": booking.uid,
                "start_time": booking.start_time.isoformat(),
                "end_time": booking.end_time.isoformat(),
            }
            if booking.user:
                template_data["organizer_name"] = booking.user.name

            await self._events.send_notification_command(
                booking_uid=booking.uid,
                trigger_event=TriggerEvent.BOOKING_REMINDER.value,
                recipients=recipients,
                template_data=template_data,
            )
            count += 1
            logger.info("Reminder sent", booking_uid=booking.uid)

        return count

    async def run_forever(self) -> None:
        """Background loop: send reminders every interval_seconds."""
        self._running = True
        logger.info(
            "Reminder scheduler started",
            interval_seconds=self._interval_seconds,
            shift_from=self._shift_from,
            shift_to=self._shift_to,
        )
        while self._running:
            try:
                count = await self.send_reminders()
                if count:
                    logger.info("Reminder cycle complete", reminders_sent=count)
            except Exception:
                logger.exception("Reminder cycle failed")
            await asyncio.sleep(self._interval_seconds)

    def stop(self) -> None:
        self._running = False
```

- [ ] **Step 4: Run tests**

```bash
cd event-booking && uv run pytest tests/test_scheduler.py -v
```
Expected: PASS

- [ ] **Step 5: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booking/event_booking/scheduler.py event-booking/tests/test_scheduler.py
git commit -m "feat(event-booking): add periodic reminder scheduler"
```

---

### Task 16: DI container and application entry point

**Files:**
- Create: `event-booking/event_booking/ioc.py`
- Create: `event-booking/event_booking/main.py`

- [ ] **Step 1: Create ioc.py**

```python
"""Dishka dependency injection providers."""

from collections.abc import AsyncGenerator

from dishka import Provider, Scope, provide
from faststream.rabbit import RabbitBroker, RabbitExchange, ExchangeType
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from event_booking.adapters.db import BookingDatabaseAdapter
from event_booking.adapters.events import EventPublisher
from event_booking.adapters.get_stream import GetStreamAdapter
from event_booking.adapters.shortener import UrlShortenerAdapter
from event_booking.adapters.sql import SqlExecutor
from event_booking.config import Settings
from event_booking.consumer import BookingConsumer
from event_booking.controllers.booking import BookingController
from event_booking.controllers.chat import ChatController
from event_booking.controllers.constraints import BookingConstraintsAnalyzer
from event_booking.controllers.meeting import MeetingController
from event_booking.scheduler import ReminderScheduler


class AppProvider(Provider):
    @provide(scope=Scope.APP)
    def provide_settings(self) -> Settings:
        return Settings()

    @provide(scope=Scope.APP)
    async def provide_db_engine(self, settings: Settings) -> AsyncGenerator[AsyncEngine]:
        engine = create_async_engine(str(settings.calcom_postgres_dsn), pool_size=10, max_overflow=20)
        yield engine
        await engine.dispose()

    @provide(scope=Scope.APP)
    def provide_sessionmaker(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    @provide(scope=Scope.APP)
    def provide_broker(self, settings: Settings) -> RabbitBroker:
        return RabbitBroker(str(settings.rabbit_url))

    @provide(scope=Scope.APP)
    def provide_exchange(self, settings: Settings) -> RabbitExchange:
        return RabbitExchange(name=settings.rabbit_exchange, type=ExchangeType.TOPIC, durable=True)

    @provide(scope=Scope.APP)
    def provide_events(self, settings: Settings) -> EventPublisher:
        return EventPublisher(
            endpoint_url=settings.events_endpoint_url,
            api_key=settings.events_api_key,
            source=settings.events_source,
            timeout_seconds=settings.events_timeout_seconds,
        )

    @provide(scope=Scope.APP)
    def provide_chat_client(self, settings: Settings) -> GetStreamAdapter:
        return GetStreamAdapter(
            chat_api_key=settings.chat_api_key,
            chat_api_secret=settings.chat_api_secret,
            user_id_encryption_key=settings.chat_user_id_encryption_key,
        )

    @provide(scope=Scope.APP)
    def provide_chat_controller(self, chat_client: GetStreamAdapter, events: EventPublisher) -> ChatController:
        return ChatController(chat_client=chat_client, events=events)

    @provide(scope=Scope.APP)
    def provide_shortener(self, settings: Settings) -> UrlShortenerAdapter:
        return UrlShortenerAdapter(base_url=settings.shortener_url, api_key=settings.shortener_api_key)

    @provide(scope=Scope.APP)
    def provide_constraints_analyzer(self) -> BookingConstraintsAnalyzer:
        return BookingConstraintsAnalyzer()

    @provide(scope=Scope.APP)
    def provide_meeting_controller(
        self,
        shortener: UrlShortenerAdapter,
        chat_client: GetStreamAdapter,
        events: EventPublisher,
        settings: Settings,
    ) -> MeetingController:
        """Note: db is not injected here because MeetingController doesn't need it in the new design.
        If video URL update is needed, pass db adapter."""
        return MeetingController(
            shortener=shortener,
            chat_client=chat_client,
            db=None,  # type: ignore[arg-type]  # Cal.com video URL update handled differently
            events=events,
            jitsi_jwt_secret=settings.jitsi_jwt_secret,
            jitsi_jwt_aud=settings.jitsi_jwt_aud,
            jitsi_jwt_iss=settings.jitsi_jwt_iss,
            meeting_host_url=settings.meeting_host_url,
        )

    @provide(scope=Scope.APP)
    def provide_booking_controller(
        self,
        events: EventPublisher,
        chat_controller: ChatController,
        meeting_controller: MeetingController,
        constraints_analyzer: BookingConstraintsAnalyzer,
        settings: Settings,
    ) -> BookingController:
        """Note: db adapter needs a session, so BookingController gets db injected
        per-message in the consumer via a session scope."""
        return BookingController(
            db=None,  # type: ignore[arg-type]  # Injected per-message
            events=events,
            chat_controller=chat_controller,
            meeting_controller=meeting_controller,
            constraints_analyzer=constraints_analyzer,
            is_enable_constraints=settings.is_enable_booking_constraints,
        )

    @provide(scope=Scope.APP)
    def provide_consumer(self, booking_controller: BookingController) -> BookingConsumer:
        return BookingConsumer(booking_controller=booking_controller)

    @provide(scope=Scope.APP)
    def provide_scheduler(
        self,
        events: EventPublisher,
        settings: Settings,
    ) -> ReminderScheduler:
        return ReminderScheduler(
            db=None,  # type: ignore[arg-type]  # Injected per-cycle
            events=events,
            interval_seconds=settings.reminder_interval_seconds,
            shift_from_minutes=settings.reminder_shift_from_minutes,
            shift_to_minutes=settings.reminder_shift_to_minutes,
        )
```

> **Note for implementor:** The `db=None` pattern above is a placeholder. In the actual implementation, you'll need to create a session-scoped DB adapter per message/cycle. The recommended approach is to create a fresh `AsyncSession` → `SqlExecutor` → `BookingDatabaseAdapter` inside the consumer handler and scheduler loop, similar to how calendar-bot creates REQUEST-scoped dependencies. Refactor the consumer's `handle` method to create a session per message and inject the db adapter into the controller before calling dispatch.

- [ ] **Step 2: Create main.py**

```python
"""FastAPI application entry point with lifespan management."""

import asyncio
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import structlog
from dishka import make_async_container
from dishka.integrations.fastapi import setup_dishka
from fastapi import FastAPI
from faststream.rabbit import RabbitBroker, RabbitExchange

from event_booking.config import Settings
from event_booking.consumer import BookingConsumer
from event_booking.ioc import AppProvider
from event_booking.scheduler import ReminderScheduler

logger = structlog.get_logger(__name__)


def _setup_logger(*, log_level: str, console_render: bool) -> None:
    structlog.configure(
        wrapper_class=structlog.make_filtering_bound_logger(structlog.get_level_from_name(log_level)),
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.dev.ConsoleRenderer() if console_render else structlog.processors.JSONRenderer(),
        ],
    )


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    container = make_async_container(AppProvider())

    settings = await container.get(Settings)
    _setup_logger(log_level=settings.log_level, console_render=settings.debug)

    broker = await container.get(RabbitBroker)
    exchange = await container.get(RabbitExchange)
    consumer = await container.get(BookingConsumer)
    scheduler = await container.get(ReminderScheduler)

    # Register consumer with broker
    consumer.register(broker, exchange, settings.booking_lifecycle_queue)
    await broker.start()

    # Start reminder scheduler
    scheduler_task = asyncio.create_task(scheduler.run_forever(), name="reminder-scheduler")

    logger.info("event-booking started")
    yield

    # Shutdown
    scheduler.stop()
    scheduler_task.cancel()
    await broker.close()
    await container.close()
    logger.info("event-booking stopped")


app = FastAPI(title="event-booking", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
```

- [ ] **Step 3: Verify syntax**

```bash
cd event-booking && ruff check . && ruff format .
```

- [ ] **Step 4: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booking/event_booking/ioc.py event-booking/event_booking/main.py
git commit -m "feat(event-booking): add DI container and application entry point"
```

---

## Phase 4: event-notifier Updates

### Task 17: Update event-notifier to handle notification.send_requested

**Files:**
- Modify: `event-notifier/event_notifier/event_types.py`
- Modify: `event-notifier/event_notifier/adapters/consumer.py`
- Modify: `event-notifier/event_notifier/application/use_cases/process_domain_event.py`

- [ ] **Step 1: Add BOOKING_REJECTED and notification.send_requested to event_types.py**

In `event-notifier/event_notifier/event_types.py`:

```python
"""Event type constants for event-notifier."""

from event_schemas.types import EventType, TriggerEvent

NOTIFIER_SOURCE = "event-notifier"

# Mapping from CloudEvent type to trigger_event string used by channel adapters
DOMAIN_EVENT_TO_TRIGGER: dict[str, TriggerEvent] = {
    EventType.BOOKING_CREATED: TriggerEvent.BOOKING_CREATED,
    EventType.BOOKING_CANCELLED: TriggerEvent.BOOKING_CANCELLED,
    EventType.BOOKING_RESCHEDULED: TriggerEvent.BOOKING_RESCHEDULED,
    EventType.BOOKING_REASSIGNED: TriggerEvent.BOOKING_REASSIGNED,
    EventType.BOOKING_REMINDER_SENT: TriggerEvent.BOOKING_REMINDER,
    EventType.BOOKING_REJECTED: TriggerEvent.BOOKING_REJECTED,
}

# notification.send_requested carries trigger_event in its payload — handled specially
NOTIFICATION_COMMAND_EVENT = EventType.NOTIFICATION_SEND_REQUESTED.value
```

- [ ] **Step 2: Update consumer to handle notification.send_requested**

In `event-notifier/event_notifier/adapters/consumer.py`, update the `_handle` method. The consumer should recognize `notification.send_requested` and extract `trigger_event` from the payload data rather than from the CloudEvent type.

Add handling after the existing `DOMAIN_EVENT_TO_TRIGGER` check:

```python
from event_notifier.event_types import NOTIFICATION_COMMAND_EVENT

# Inside _handle method, after the existing DOMAIN_EVENT_TO_TRIGGER check:
# If it's a notification command, extract trigger_event from payload
if event_type == NOTIFICATION_COMMAND_EVENT:
    data = ce.data or {}
    trigger_str = data.get("trigger_event", "")
    try:
        trigger = TriggerEvent(trigger_str)
    except ValueError:
        logger.warning("Unknown trigger_event in notification command", trigger_event=trigger_str)
        return
    # ... continue with DomainEvent construction using data from payload
```

- [ ] **Step 3: Update use case to handle direct recipients from notification.send_requested**

In `event-notifier/event_notifier/application/use_cases/process_domain_event.py`, add a code path that reads recipients directly from `event.data["recipients"]` when the event comes from `notification.send_requested`, bypassing the routing rules lookup.

Add to `ProcessDomainEventUseCase.execute()`:

```python
from event_notifier.event_types import NOTIFICATION_COMMAND_EVENT

# In execute(), after idempotency check:
if event.event_type == NOTIFICATION_COMMAND_EVENT:
    # Recipients come directly from payload, not from routing rules
    payload_recipients = event.data.get("recipients", [])
    template_data = event.data.get("template_data", {})
    for recipient in payload_recipients:
        email = recipient.get("email")
        role = recipient.get("role", "client")
        if not email:
            continue
        # Resolve user contacts by email
        contacts = await self._users_client.get_contacts_by_email(email=email, role=role)
        # ... write outbox records with template_data as template_context
    return
```

> **Note for implementor:** The exact implementation depends on `IUsersClient` interface. If it only supports lookup by `user_id`, you'll need to add a `get_contacts_by_email` method or resolve user_id from email first. Check the current `infrastructure/users_client.py` for available methods.

- [ ] **Step 4: Add BOOKING_REJECTED message to Telegram channel**

In `event-notifier/event_notifier/infrastructure/channels/telegram.py`, add to `_MESSAGE_TEMPLATES`:

```python
TriggerEvent.BOOKING_REJECTED: "Бронирование отклонено.",
```

- [ ] **Step 5: Add BOOKING_REJECTED template mapping to Email channel**

In `event-notifier/event_notifier/infrastructure/channels/email.py`, add to `_TEMPLATE_MAP`:

```python
TriggerEvent.BOOKING_REJECTED: "booking_rejected",
```

- [ ] **Step 6: Run existing tests**

```bash
cd event-notifier && uv run pytest -v
```
Expected: PASS (existing tests should still pass; new behavior is additive)

- [ ] **Step 7: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-notifier/event_notifier/event_types.py event-notifier/event_notifier/adapters/consumer.py event-notifier/event_notifier/application/use_cases/process_domain_event.py event-notifier/event_notifier/infrastructure/channels/telegram.py event-notifier/event_notifier/infrastructure/channels/email.py
git commit -m "feat(event-notifier): handle notification.send_requested and booking.rejected"
```

---

## Phase 5: Integration

### Task 18: Run all tests across services

**Files:** None (verification only)

- [ ] **Step 1: Run event-schemas checks**

```bash
cd event-schemas && ruff check . && python -c "from event_schemas import BookingRejectedPayload; print('OK')"
```

- [ ] **Step 2: Run event-receiver checks**

```bash
cd event-receiver && ruff check .
```

- [ ] **Step 3: Run event-booking full test suite**

```bash
cd event-booking && uv run pytest -v
```

- [ ] **Step 4: Run event-notifier tests**

```bash
cd event-notifier && uv run pytest -v
```

- [ ] **Step 5: Verify all services lint clean**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
for svc in event-schemas event-receiver event-booking event-notifier; do echo "=== $svc ===" && cd $svc && ruff check . && cd ..; done
```

---

### Task 19: Documentation updates

**Files:**
- Modify: `docs/architecture/MESSAGE_CONTRACTS.md` — add booking.rejected, notification.send_requested from event-booking
- Modify: `event-receiver/QUEUES_DIGEST.md` — add booking.rejected routing rule
- Create: `event-booking/docs/SERVICE_OVERVIEW.md`
- Create: `event-booking/docs/API_CONTRACTS.md`
- Create: `event-booking/docs/DEPENDENCIES.md`

- [ ] **Step 1: Update MESSAGE_CONTRACTS.md**

Add `booking.rejected` event contract and document that `event-booking` now publishes `notification.send_requested` with enriched template data.

- [ ] **Step 2: Update event-receiver QUEUES_DIGEST.md**

Add the new routing rule:
```
booking.rejected → events.booking.lifecycle (source: booking)
```

- [ ] **Step 3: Create event-booking service docs**

Create `SERVICE_OVERVIEW.md`, `API_CONTRACTS.md`, `DEPENDENCIES.md` following the conventions of other services.

- [ ] **Step 4: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add docs/ event-receiver/QUEUES_DIGEST.md event-booking/docs/
git commit -m "docs: add event-booking service documentation and update contracts"
```

---

## Out of Scope (separate plans)

- **Telegram bot webhook endpoint** in event-receiver (`POST /telegram`) — mentioned in spec Phase 3 but independent from the core booking flow. Should be a separate plan.
- **Jinja2 email templates** migration from calendar-bot to event-notifier — currently event-notifier uses UniSender template IDs. Migrating to local Jinja2 rendering is a separate task that requires deciding on rendering strategy.
- **Redis notification deduplication** — calendar-bot used Redis for reminder dedup. Re-evaluate if needed after the scheduler is running.

## Summary of Changes

| Service | What changes |
|---|---|
| **event-schemas** | +`BOOKING_REJECTED` EventType, +`BookingRejectedPayload` |
| **event-receiver** | +routing rule for `booking.rejected` |
| **event-booking** (NEW) | Full service: consumer, controllers, adapters, scheduler, DI, health endpoint |
| **event-notifier** | +handle `notification.send_requested`, +`BOOKING_REJECTED` templates |
| **event-saver** | No changes (already consumes `events.booking.lifecycle`) |
