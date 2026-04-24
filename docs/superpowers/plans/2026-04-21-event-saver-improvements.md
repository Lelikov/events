# Event-Saver Service Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix real issues in event-saver: DLQ alignment, Clean Architecture violations, dead code, silent failures, hash determinism, documentation sync, and add test coverage.

**Architecture:** Severity-first approach. Each phase is independently deployable. Phases: CRITICAL (DLQ) → HIGH (architecture + dead code) → MEDIUM (reliability) → LOW (docs) → Tests.

**Tech Stack:** Python 3.14, FastAPI, FastStream, SQLAlchemy async, Dishka, pytest, pytest-asyncio

**Pre-plan note on resolved audit findings:**
The following issues from the audit are already fixed and require NO work:
- SqlExecutor auto-commit: `execute()` has no `commit()` — commit is only in `event_store_facade.py:85`
- `BOOKING_RESCHEDULED`: already exists in `event-schemas/event_schemas/types.py:22`
- TelegramNotificationProjection NULL user_id: already has null check at `notification_projection.py:189-190`
- `declare=False`: already changed to `declare=True` in `consumer.py:45`
- `_parse_occurred_at` duplication: consumer.py does not duplicate this logic

---

### Task 1: Align consumer queue arguments with event-receiver

**Files:**
- Modify: `event-saver/event_saver/adapters/consumer.py:39-48`

**Context:** event-receiver declares queues with `x-dead-letter-exchange`, `x-dead-letter-routing-key`, and `x-max-priority`. event-saver's consumer only has `x-dead-letter-exchange`. RabbitMQ requires matching arguments — mismatched declarations cause channel errors.

- [ ] **Step 1: Update queue arguments in consumer**

In `event-saver/event_saver/adapters/consumer.py`, replace the queue declaration:

```python
# OLD (line 41-47):
queue=RabbitQueue(
    name=queue_name,
    durable=True,
    routing_key=queue_name,
    declare=True,
    arguments={"x-dead-letter-exchange": "events.dlx"},
),

# NEW:
queue=RabbitQueue(
    name=queue_name,
    durable=True,
    routing_key=queue_name,
    declare=True,
    arguments={
        "x-max-priority": 10,
        "x-dead-letter-exchange": "events.dlx",
        "x-dead-letter-routing-key": f"{queue_name}.dlq",
    },
),
```

- [ ] **Step 2: Verify ruff passes**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && ruff check event_saver/adapters/consumer.py && ruff format --check event_saver/adapters/consumer.py`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-saver
git add event_saver/adapters/consumer.py
git commit -m "fix: align consumer queue arguments with event-receiver DLQ config"
```

---

### Task 2: Create repository protocols

**Files:**
- Create: `event-saver/event_saver/interfaces/repositories.py`
- Modify: `event-saver/event_saver/interfaces/__init__.py`

**Context:** `IngestEventUseCase` (application layer) imports concrete `BookingRepository` and `EventRepository` from infrastructure. Clean Architecture requires depending on abstractions. We create protocol interfaces.

- [ ] **Step 1: Create repository protocols**

Create `event-saver/event_saver/interfaces/repositories.py`:

```python
from __future__ import annotations

import uuid
from datetime import datetime
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from event_saver.domain.models.booking import BookingData
    from event_saver.domain.models.event import ParsedEvent


class IEventRepository(Protocol):
    async def save(self, event: ParsedEvent) -> bool: ...


class IBookingRepository(Protocol):
    async def get_or_none(self, *, booking_id: str, queue_name: str) -> int | None: ...

    async def upsert(
        self,
        *,
        booking_data: BookingData,
        occurred_at: datetime,
        organizer_user_id: uuid.UUID | None,
        client_user_id: uuid.UUID | None,
    ) -> int: ...

    async def save_organizer_history(
        self,
        *,
        booking_id: int,
        organizer_user_id: uuid.UUID,
        source_event_id: str,
        occurred_at: datetime,
    ) -> None: ...
```

- [ ] **Step 2: Update interfaces __init__.py**

Add to `event-saver/event_saver/interfaces/__init__.py`:

```python
from event_saver.interfaces.repositories import IBookingRepository, IEventRepository
```

And add `"IBookingRepository"`, `"IEventRepository"` to `__all__`.

- [ ] **Step 3: Verify ruff passes**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && ruff check event_saver/interfaces/ && ruff format --check event_saver/interfaces/`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-saver
git add event_saver/interfaces/repositories.py event_saver/interfaces/__init__.py
git commit -m "feat: add IEventRepository and IBookingRepository protocols"
```

---

### Task 3: Create projection protocol

**Files:**
- Create: `event-saver/event_saver/interfaces/projection_handler.py`
- Modify: `event-saver/event_saver/interfaces/__init__.py`

**Context:** `ProjectionExecutor` (application layer) imports `BaseProjection` from infrastructure. We need a protocol in `interfaces/` that the application layer can depend on.

- [ ] **Step 1: Create projection handler protocol**

Create `event-saver/event_saver/interfaces/projection_handler.py`:

```python
from __future__ import annotations

import uuid
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from event_saver.domain.models.event import ParsedEvent


class IProjectionHandler(Protocol):
    def can_handle(self, event: ParsedEvent) -> bool: ...

    async def handle(
        self,
        *,
        event: ParsedEvent,
        booking_ref_id: int,
        organizer_user_id: uuid.UUID | None,
        client_user_id: uuid.UUID | None,
        queue_name: str,
    ) -> tuple[str, dict[str, Any]] | None: ...
```

- [ ] **Step 2: Update interfaces __init__.py**

Add to `event-saver/event_saver/interfaces/__init__.py`:

```python
from event_saver.interfaces.projection_handler import IProjectionHandler
```

And add `"IProjectionHandler"` to `__all__`.

- [ ] **Step 3: Verify ruff passes**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && ruff check event_saver/interfaces/ && ruff format --check event_saver/interfaces/`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-saver
git add event_saver/interfaces/projection_handler.py event_saver/interfaces/__init__.py
git commit -m "feat: add IProjectionHandler protocol for projection abstraction"
```

---

### Task 4: Fix Clean Architecture violation in IngestEventUseCase

**Files:**
- Modify: `event-saver/event_saver/application/use_cases/ingest_event.py:9-13,22-30`

**Context:** Lines 9-13 import concrete `ProjectionExecutor`, `BookingRepository`, `EventRepository`. Replace with protocols.

- [ ] **Step 1: Replace imports and type hints**

In `event-saver/event_saver/application/use_cases/ingest_event.py`:

Replace:
```python
from event_saver.application.services.projection_executor import ProjectionExecutor
from event_saver.domain.services import BookingDataExtractor, EventParser, ParticipantExtractor

# TODO: Replace with protocol interface from interfaces/ to fix clean architecture violation
from event_saver.infrastructure.persistence.repositories import BookingRepository, EventRepository
```

With:
```python
from event_saver.domain.services import BookingDataExtractor, EventParser, ParticipantExtractor
from event_saver.interfaces.repositories import IBookingRepository, IEventRepository
```

Replace constructor type hints (lines 22-30):
```python
class IngestEventUseCase:
    """Main use case for event ingestion."""

    def __init__(
        self,
        *,
        event_parser: EventParser,
        participant_extractor: ParticipantExtractor,
        booking_data_extractor: BookingDataExtractor,
        event_repository: IEventRepository,
        booking_repository: IBookingRepository,
        projection_executor: ProjectionExecutor,
    ) -> None:
```

Note: `ProjectionExecutor` stays as a concrete import for now — it lives in the application layer itself, not infrastructure. That's not a violation.

Actually wait — `ProjectionExecutor` is in `application/services/`, which is the same layer. That import is fine. But the import line `from event_saver.application.services.projection_executor import ProjectionExecutor` at line 9 is a same-layer import, not a violation.

Full replacement for the imports section (lines 1-16):
```python
"""Use case for ingesting events - orchestrates the entire event processing flow."""

import uuid
from typing import Any

import structlog
from event_schemas.types import EventType

from event_saver.application.services.projection_executor import ProjectionExecutor
from event_saver.domain.services import BookingDataExtractor, EventParser, ParticipantExtractor
from event_saver.interfaces.repositories import IBookingRepository, IEventRepository


logger = structlog.get_logger(__name__)
```

And the constructor (lines 22-37):
```python
    def __init__(
        self,
        *,
        event_parser: EventParser,
        participant_extractor: ParticipantExtractor,
        booking_data_extractor: BookingDataExtractor,
        event_repository: IEventRepository,
        booking_repository: IBookingRepository,
        projection_executor: ProjectionExecutor,
    ) -> None:
        self._event_parser = event_parser
        self._participant_extractor = participant_extractor
        self._booking_data_extractor = booking_data_extractor
        self._event_repository = event_repository
        self._booking_repository = booking_repository
        self._projection_executor = projection_executor
```

- [ ] **Step 2: Verify ruff passes**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && ruff check event_saver/application/ && ruff format --check event_saver/application/`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-saver
git add event_saver/application/use_cases/ingest_event.py
git commit -m "refactor: replace concrete repository imports with protocols in IngestEventUseCase"
```

---

### Task 5: Fix Clean Architecture violation in ProjectionExecutor

**Files:**
- Modify: `event-saver/event_saver/application/services/projection_executor.py:9-11,20-25`

**Context:** Line 10 imports `BaseProjection` from infrastructure layer.

- [ ] **Step 1: Replace import and type hint**

In `event-saver/event_saver/application/services/projection_executor.py`:

Replace:
```python
from event_saver.domain.models.event import ParsedEvent

# TODO: Replace with protocol interface from interfaces/ to fix clean architecture violation
from event_saver.infrastructure.persistence.projections.base import BaseProjection
from event_saver.interfaces.sql import ISqlExecutor
```

With:
```python
from event_saver.domain.models.event import ParsedEvent
from event_saver.interfaces.projection_handler import IProjectionHandler
from event_saver.interfaces.sql import ISqlExecutor
```

Replace constructor (lines 20-26):
```python
    def __init__(
        self,
        *,
        sql: ISqlExecutor,
        handlers: list[IProjectionHandler],
    ) -> None:
        self._sql = sql
        self._handlers = handlers
```

- [ ] **Step 2: Verify ruff passes**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && ruff check event_saver/application/ && ruff format --check event_saver/application/`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-saver
git add event_saver/application/services/projection_executor.py
git commit -m "refactor: replace BaseProjection import with IProjectionHandler protocol"
```

---

### Task 6: Update ioc.py for new protocols and event_store_facade.py

**Files:**
- Modify: `event-saver/event_saver/ioc.py:24-37,210-227,232-250`
- Modify: `event-saver/event_saver/infrastructure/persistence/event_store_facade.py:7-13,26-27,57-59`

**Context:** ioc.py returns `list[BaseProjection]` and facade imports concrete classes. Update to use protocols.

- [ ] **Step 1: Update ioc.py projection handler list return type**

In `event-saver/event_saver/ioc.py`, replace:
```python
from event_saver.infrastructure.persistence.projections.base import BaseProjection
```
With:
```python
from event_saver.interfaces.projection_handler import IProjectionHandler
```

Replace `provide_projection_handlers` method (line 209-227):
```python
    @provide(scope=Scope.APP)
    def provide_projection_handlers(
        self,
        meeting_link: MeetingLinkProjection,
        email_notification: EmailNotificationProjection,
        telegram_notification: TelegramNotificationProjection,
        email_status_history: EmailStatusHistoryProjection,
        chat_event: ChatEventProjection,
        chat_read_update: ChatReadUpdateProjection,
        video_event: VideoEventProjection,
    ) -> list[IProjectionHandler]:
        """Collect all projection handlers into a list."""
        return [
            meeting_link,
            email_notification,
            telegram_notification,
            email_status_history,
            chat_event,
            chat_read_update,
            video_event,
        ]
```

Update `provide_event_store` signature (line 237):
```python
        projection_handlers: list[IProjectionHandler],
```

- [ ] **Step 2: Update event_store_facade.py**

In `event-saver/event_saver/infrastructure/persistence/event_store_facade.py`, replace:
```python
from event_saver.application.services.projection_executor import ProjectionExecutor
from event_saver.application.use_cases.ingest_event import IngestEventUseCase
from event_saver.domain.services import BookingDataExtractor, EventParser, ParticipantExtractor
from event_saver.infrastructure.persistence.projections.base import BaseProjection
from event_saver.infrastructure.persistence.repositories import BookingRepository, EventRepository
from event_saver.interfaces.event_store import IEventStore
from event_saver.interfaces.sql import ISqlExecutorFactory
```

With:
```python
from event_saver.application.services.projection_executor import ProjectionExecutor
from event_saver.application.use_cases.ingest_event import IngestEventUseCase
from event_saver.domain.services import BookingDataExtractor, EventParser, ParticipantExtractor
from event_saver.infrastructure.persistence.repositories import BookingRepository, EventRepository
from event_saver.interfaces.event_store import IEventStore
from event_saver.interfaces.projection_handler import IProjectionHandler
from event_saver.interfaces.sql import ISqlExecutorFactory
```

Replace `BaseProjection` with `IProjectionHandler` in the constructor (line 26):
```python
        projection_handlers: list[IProjectionHandler],
```

- [ ] **Step 3: Verify ruff passes**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && ruff check event_saver/ && ruff format --check event_saver/`
Expected: no errors

- [ ] **Step 4: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-saver
git add event_saver/ioc.py event_saver/infrastructure/persistence/event_store_facade.py
git commit -m "refactor: wire IProjectionHandler protocol through ioc and facade"
```

---

### Task 7: Remove dead code

**Files:**
- Delete: `event-saver/event_saver/adapters/publisher.py`
- Delete: `event-saver/event_saver/interfaces/publisher.py`
- Delete: `event-saver/event_saver/interfaces/routing.py`
- Modify: `event-saver/event_saver/adapters/__init__.py`
- Modify: `event-saver/event_saver/interfaces/__init__.py`
- Modify: `event-saver/event_saver/adapters/sql.py:21-29` (remove `execute_in_transaction`)
- Modify: `event-saver/event_saver/interfaces/sql.py:17-20` (remove `execute_in_transaction` from protocol)
- Modify: `event-saver/event_saver/ioc.py` (remove topology manager provider)

**Context:** `CloudEventPublisher`, `RabbitTopologyManager`, `EventRouter` are wired but never called. `execute_in_transaction` is never called. Related interfaces (`ICloudEventPublisher`, `ITopologyManager`, `IEventRouter`) are unused.

- [ ] **Step 1: Delete publisher.py adapter**

```bash
rm /Users/alexandrlelikov/PycharmProjects/events/event-saver/event_saver/adapters/publisher.py
```

- [ ] **Step 2: Delete unused interface files**

```bash
rm /Users/alexandrlelikov/PycharmProjects/events/event-saver/event_saver/interfaces/publisher.py
rm /Users/alexandrlelikov/PycharmProjects/events/event-saver/event_saver/interfaces/routing.py
```

- [ ] **Step 3: Update adapters/__init__.py**

Replace entire content of `event-saver/event_saver/adapters/__init__.py` with:

```python
from event_saver.adapters.consumer import RabbitEventConsumerRunner
from event_saver.adapters.event_classification import BookingTimelineClassifier
from event_saver.adapters.sql import SqlExecutor

__all__ = [
    "BookingTimelineClassifier",
    "RabbitEventConsumerRunner",
    "SqlExecutor",
]
```

- [ ] **Step 4: Update interfaces/__init__.py**

Remove `ICloudEventPublisher`, `ITopologyManager`, `IEventRouter` imports and `__all__` entries. The file should contain:

```python
from event_saver.interfaces.consumer import IEventConsumerRunner
from event_saver.interfaces.event_store import IEventStore
from event_saver.interfaces.projection import (
    IBookingEventClassifier,
)
from event_saver.interfaces.projection_handler import IProjectionHandler
from event_saver.interfaces.repositories import IBookingRepository, IEventRepository
from event_saver.interfaces.sql import ISqlExecutor, ISqlExecutorFactory


__all__ = [
    "IBookingEventClassifier",
    "IBookingRepository",
    "IEventConsumerRunner",
    "IEventRepository",
    "IEventStore",
    "IProjectionHandler",
    "ISqlExecutor",
    "ISqlExecutorFactory",
]
```

- [ ] **Step 5: Remove execute_in_transaction from SqlExecutor**

In `event-saver/event_saver/adapters/sql.py`, remove lines 21-29 (the `execute_in_transaction` method). File becomes:

```python
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
```

- [ ] **Step 6: Remove execute_in_transaction from ISqlExecutor protocol**

In `event-saver/event_saver/interfaces/sql.py`, remove lines 17-20. File becomes:

```python
from __future__ import annotations

from typing import TYPE_CHECKING, Protocol


if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import AsyncSession


class ISqlExecutor(Protocol):
    async def fetch_one(self, query: str, values: dict) -> RowMapping | None: ...

    async def fetch_all(self, query: str, values: dict) -> list[RowMapping]: ...

    async def execute(self, query: str, values: dict) -> None: ...


class ISqlExecutorFactory(Protocol):
    def __call__(self, session: AsyncSession) -> ISqlExecutor: ...
```

- [ ] **Step 7: Remove topology manager and unused imports from ioc.py**

In `event-saver/event_saver/ioc.py`:

Remove from imports:
```python
from event_saver.adapters import (
    BookingTimelineClassifier,
    RabbitEventConsumerRunner,
    RabbitTopologyManager,
    SqlExecutor,
)
```
Replace with:
```python
from event_saver.adapters import (
    BookingTimelineClassifier,
    RabbitEventConsumerRunner,
    SqlExecutor,
)
```

Remove the import:
```python
from event_saver.interfaces.publisher import ITopologyManager
```

Remove the entire `provide_topology_manager` method (lines 86-101).

- [ ] **Step 8: Verify ruff passes**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && ruff check event_saver/ && ruff format --check event_saver/`
Expected: no errors

- [ ] **Step 9: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-saver
git add -A event_saver/
git commit -m "refactor: remove dead code — publisher, topology manager, execute_in_transaction, unused interfaces"
```

---

### Task 8: Re-raise projection failures

**Files:**
- Modify: `event-saver/event_saver/application/services/projection_executor.py:62-68`

**Context:** Projection errors are caught and logged but silently swallowed. With DLQ now configured (Task 1), re-raising sends the message to DLQ where it's visible for monitoring.

- [ ] **Step 1: Add re-raise after logging**

In `event-saver/event_saver/application/services/projection_executor.py`, replace:

```python
            except Exception:
                logger.exception(
                    "projection_failed",
                    projection_name=handler.__class__.__name__,
                    event_type=event.event_type,
                    booking_id=event.booking_id,
                    event_id=event.event_id,
                )
```

With:

```python
            except Exception:
                logger.exception(
                    "projection_failed",
                    projection_name=handler.__class__.__name__,
                    event_type=event.event_type,
                    booking_id=event.booking_id,
                    event_id=event.event_id,
                )
                raise
```

- [ ] **Step 2: Verify ruff passes**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && ruff check event_saver/application/ && ruff format --check event_saver/application/`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-saver
git add event_saver/application/services/projection_executor.py
git commit -m "fix: re-raise projection failures to trigger DLQ instead of silent swallow"
```

---

### Task 9: Fix payload hash determinism

**Files:**
- Modify: `event-saver/event_saver/domain/services/event_parser.py:7,81-89`

**Context:** `ujson.dumps()` does not guarantee key ordering. Different serialization order → different hash → deduplication misses. Use `json.dumps(sort_keys=True)` for deterministic output.

- [ ] **Step 1: Replace ujson with json for hash computation**

In `event-saver/event_saver/domain/services/event_parser.py`:

Replace the import:
```python
import ujson
```
With:
```python
import json
```

Replace `_compute_payload_hash` (lines 81-89):
```python
    @staticmethod
    def _compute_payload_hash(payload: dict[str, Any]) -> str:
        """Compute MD5 hash of payload for deduplication.

        Uses json.dumps with sort_keys for deterministic serialization
        across Python versions and platforms.
        """
        payload_json = json.dumps(payload, sort_keys=True, ensure_ascii=False)
        return hashlib.md5(payload_json.encode()).hexdigest()
```

- [ ] **Step 2: Verify ruff passes**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && ruff check event_saver/domain/ && ruff format --check event_saver/domain/`
Expected: no errors

- [ ] **Step 3: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-saver
git add event_saver/domain/services/event_parser.py
git commit -m "fix: use deterministic json.dumps(sort_keys=True) for payload hash"
```

---

### Task 10: Sync QUEUES_DIGEST.md with actual config

**Files:**
- Modify: `event-saver/QUEUES_DIGEST.md:9,16`

**Context:** The summary table is missing `booking.reminder_sent` from `events.booking.lifecycle`, and `events.mail` shows incorrect type pattern `unisender.*` instead of `unisender.status.created`.

- [ ] **Step 1: Fix the summary table**

In `event-saver/QUEUES_DIGEST.md`:

Replace line 9:
```
| `events.booking.lifecycle` | `*` | `booking.created` / `booking.rescheduled` / `booking.reassigned` / `booking.cancelled` | lifecycle бронирования |
```
With:
```
| `events.booking.lifecycle` | `*` | `booking.created` / `booking.rescheduled` / `booking.reassigned` / `booking.cancelled` / `booking.reminder_sent` | lifecycle бронирования |
```

Replace line 16:
```
| `events.mail` | `unisender-go` | `unisender.*` | события UniSender |
```
With:
```
| `events.mail` | `unisender-go` | `unisender.status.created` | события UniSender |
```

- [ ] **Step 2: Update events.booking.lifecycle section**

In the `## events.booking.lifecycle` section (lines 20-27), add `booking.reminder_sent`:

```markdown
## events.booking.lifecycle

События жизненного цикла бронирования:
- `booking.created`
- `booking.rescheduled`
- `booking.reassigned`
- `booking.cancelled`
- `booking.reminder_sent`
```

- [ ] **Step 3: Update events.mail section**

In the `## events.mail` section (lines 63-66):

```markdown
## events.mail

События UniSender:
- `source_pattern = "unisender-go"`
- `type_pattern = "unisender.status.created"`
```

- [ ] **Step 4: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-saver
git add QUEUES_DIGEST.md
git commit -m "docs: sync QUEUES_DIGEST.md with actual routing config"
```

---

### Task 11: Add mandatory documentation rule to CLAUDE.md files

**Files:**
- Modify: `event-saver/CLAUDE.md`
- Modify: `CLAUDE.md` (root)

- [ ] **Step 1: Add documentation rule to event-saver CLAUDE.md**

Append to the end of `event-saver/CLAUDE.md`:

```markdown

## Documentation Requirements

All code changes MUST include corresponding documentation updates:
- New features or architectural changes → update relevant `docs/` files
- New event types or queue changes → update `QUEUES_DIGEST.md` and `EVENTS_DIGEST.md`
- Changed interfaces or DI wiring → update Architecture section in this file
- Bug fixes for audit findings → update `docs/AUDIT.md` to close the finding
- Migration changes → update `docs/DATA_MODEL.md`
```

- [ ] **Step 2: Add documentation rule to root CLAUDE.md**

Append before `## MCP Tools: code-review-graph` section in the root `CLAUDE.md`:

```markdown

## Documentation Requirements

All code changes MUST include corresponding documentation updates:
- Architectural changes → update `docs/architecture/` files
- New event types or queue changes → update per-service `QUEUES_DIGEST.md` and `EVENTS_DIGEST.md`
- Changed interfaces or cross-service contracts → update `docs/architecture/MESSAGE_CONTRACTS.md`
- Bug fixes for audit findings → update per-service `docs/AUDIT.md`
- New services or endpoints → update `docs/architecture/ONBOARDING.md`

```

- [ ] **Step 3: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-saver/CLAUDE.md CLAUDE.md
git commit -m "docs: add mandatory documentation rule to CLAUDE.md files"
```

---

### Task 12: Update docs/AUDIT.md — close resolved findings

**Files:**
- Modify: `event-saver/docs/AUDIT.md`

- [ ] **Step 1: Mark resolved findings**

Add a `## Resolved` section at the end of `event-saver/docs/AUDIT.md` documenting what was fixed:

```markdown

## Resolved Findings

| ID | Finding | Resolution | Date |
|---|---|---|---|
| C-5 | SqlExecutor auto-commit breaks atomicity | Was already fixed: execute() has no commit(), single commit in event_store_facade | 2026-04-21 |
| H-3 | Missing BOOKING_RESCHEDULED in EventType | Was already present in event-schemas types.py | 2026-04-21 |
| H-1 | Application layer imports concrete infrastructure | Replaced with IEventRepository, IBookingRepository, IProjectionHandler protocols | 2026-04-21 |
| H-4 | Orphaned IEventProjectionStatementFactory | Removed along with all dead code (publisher, topology manager, unused interfaces) | 2026-04-21 |
| M-2 | Projection failures silently swallowed | Added re-raise after logging, failures now trigger DLQ | 2026-04-21 |
| M-1 | Deduplication hash mismatch | Replaced ujson.dumps with json.dumps(sort_keys=True) | 2026-04-21 |
| M-4 | TelegramNotificationProjection NULL user_id | Was already fixed: null check at line 189 | 2026-04-21 |
| M-6 | declare=False on queues | Was already changed to declare=True | 2026-04-21 |
| L-1 | ioc_new.py references | No references found in current CLAUDE.md | 2026-04-21 |
| L-3 | execute_in_transaction unused | Removed from SqlExecutor and ISqlExecutor | 2026-04-21 |
| L-4 | EventRouter/CloudEventPublisher wired but unused | Removed publisher.py, routing interfaces, topology manager | 2026-04-21 |
| L-6 | QUEUES_DIGEST.md incomplete | Synced with actual config.py routing rules | 2026-04-21 |
```

- [ ] **Step 2: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-saver
git add docs/AUDIT.md
git commit -m "docs: close resolved audit findings in AUDIT.md"
```

---

### Task 13: Set up test infrastructure

**Files:**
- Create: `event-saver/tests/__init__.py`
- Create: `event-saver/tests/conftest.py`
- Modify: `event-saver/pyproject.toml` (add pytest deps if missing)

- [ ] **Step 1: Check if pytest is in dependencies**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && grep -E "pytest" pyproject.toml`

If missing, add to `[dependency-groups]` dev section:
```toml
[dependency-groups]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
]
```

- [ ] **Step 2: Create test directory**

Create `event-saver/tests/__init__.py` (empty file).

Create `event-saver/tests/conftest.py`:

```python
"""Shared test fixtures for event-saver."""

import uuid
from datetime import UTC, datetime

import pytest

from event_saver.domain.models.booking import BookingData
from event_saver.domain.models.event import ParsedEvent, RawEventData


@pytest.fixture
def sample_raw_event() -> RawEventData:
    return RawEventData(
        event_id="evt-001",
        event_type="booking.created",
        source="booking",
        occurred_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
        booking_id="book-123",
        payload={
            "normalized": {
                "participants": [
                    {"role": "organizer", "user_id": str(uuid.uuid4())},
                    {"role": "client", "user_id": str(uuid.uuid4())},
                ],
            },
            "original": {
                "start_time": "2026-01-20T10:00:00Z",
                "end_time": "2026-01-20T11:00:00Z",
            },
        },
    )


@pytest.fixture
def sample_parsed_event(sample_raw_event: RawEventData) -> ParsedEvent:
    return ParsedEvent(raw=sample_raw_event, payload_hash="abc123hash")


@pytest.fixture
def sample_booking_data() -> BookingData:
    return BookingData(
        booking_id="book-123",
        start_time=datetime(2026, 1, 20, 10, 0, tzinfo=UTC),
        end_time=datetime(2026, 1, 20, 11, 0, tzinfo=UTC),
        status="created",
    )
```

- [ ] **Step 3: Install dev deps**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && uv sync`

- [ ] **Step 4: Verify pytest discovers the test directory**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && python -m pytest tests/ --collect-only`
Expected: collected 0 items (no tests yet, but no errors)

- [ ] **Step 5: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-saver
git add tests/ pyproject.toml
git commit -m "chore: set up test infrastructure with pytest and shared fixtures"
```

---

### Task 14: Domain layer unit tests

**Files:**
- Create: `event-saver/tests/domain/__init__.py`
- Create: `event-saver/tests/domain/test_event_parser.py`
- Create: `event-saver/tests/domain/test_participant_extractor.py`
- Create: `event-saver/tests/domain/test_booking_extractor.py`

- [ ] **Step 1: Create test directory**

Create `event-saver/tests/domain/__init__.py` (empty file).

- [ ] **Step 2: Write EventParser tests**

Create `event-saver/tests/domain/test_event_parser.py`:

```python
"""Tests for EventParser domain service."""

import hashlib
import json
from datetime import UTC, datetime

import pytest

from event_saver.domain.services.event_parser import EventParser


class TestParse:
    def test_parses_valid_event(self) -> None:
        result = EventParser.parse(
            event_id="evt-001",
            event_type="booking.created",
            source="booking",
            time="2026-01-15T10:00:00Z",
            booking_id="book-123",
            data={"key": "value"},
        )

        assert result.event_id == "evt-001"
        assert result.event_type == "booking.created"
        assert result.source == "booking"
        assert result.booking_id == "book-123"
        assert result.payload == {"key": "value"}
        assert result.occurred_at == datetime(2026, 1, 15, 10, 0, tzinfo=UTC)

    def test_none_data_becomes_empty_dict(self) -> None:
        result = EventParser.parse(
            event_id="evt-001",
            event_type="booking.created",
            source="booking",
            time="2026-01-15T10:00:00Z",
            booking_id=None,
            data=None,
        )

        assert result.payload == {}

    def test_none_time_uses_utc_now(self) -> None:
        before = datetime.now(UTC)
        result = EventParser.parse(
            event_id="evt-001",
            event_type="booking.created",
            source="booking",
            time=None,
            booking_id=None,
            data={},
        )
        after = datetime.now(UTC)

        assert before <= result.occurred_at <= after

    def test_datetime_time_preserved(self) -> None:
        dt = datetime(2026, 6, 1, 12, 0, tzinfo=UTC)
        result = EventParser.parse(
            event_id="evt-001",
            event_type="booking.created",
            source="booking",
            time=dt,
            booking_id=None,
            data={},
        )

        assert result.occurred_at == dt

    def test_naive_datetime_gets_utc(self) -> None:
        dt = datetime(2026, 6, 1, 12, 0)
        result = EventParser.parse(
            event_id="evt-001",
            event_type="booking.created",
            source="booking",
            time=dt,
            booking_id=None,
            data={},
        )

        assert result.occurred_at.tzinfo == UTC

    def test_extensions_passed_through(self) -> None:
        result = EventParser.parse(
            event_id="evt-001",
            event_type="booking.created",
            source="booking",
            time="2026-01-15T10:00:00Z",
            booking_id=None,
            data={},
            idempotency_key="idem-1",
            trace_id="trace-1",
            span_id="span-1",
            dataschema="v1",
        )

        assert result.idempotency_key == "idem-1"
        assert result.trace_id == "trace-1"
        assert result.span_id == "span-1"
        assert result.dataschema == "v1"


class TestPayloadHash:
    def test_deterministic_hash(self) -> None:
        result1 = EventParser.parse(
            event_id="e1", event_type="t", source="s",
            time="2026-01-01T00:00:00Z", booking_id=None,
            data={"b": 2, "a": 1},
        )
        result2 = EventParser.parse(
            event_id="e2", event_type="t", source="s",
            time="2026-01-01T00:00:00Z", booking_id=None,
            data={"a": 1, "b": 2},
        )

        assert result1.payload_hash == result2.payload_hash

    def test_hash_matches_sorted_json(self) -> None:
        payload = {"z": 1, "a": 2}
        expected = hashlib.md5(
            json.dumps(payload, sort_keys=True, ensure_ascii=False).encode(),
        ).hexdigest()

        result = EventParser.parse(
            event_id="e1", event_type="t", source="s",
            time="2026-01-01T00:00:00Z", booking_id=None,
            data=payload,
        )

        assert result.payload_hash == expected
```

- [ ] **Step 3: Run EventParser tests**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && python -m pytest tests/domain/test_event_parser.py -v`
Expected: all tests PASS

- [ ] **Step 4: Write ParticipantExtractor tests**

Create `event-saver/tests/domain/test_participant_extractor.py`:

```python
"""Tests for ParticipantExtractor domain service."""

import uuid

from event_saver.domain.services.participant_extractor import ParticipantExtractor


class TestExtract:
    def setup_method(self) -> None:
        self.extractor = ParticipantExtractor()

    def test_extracts_both_participants(self) -> None:
        org_id = uuid.uuid4()
        client_id = uuid.uuid4()
        payload = {
            "normalized": {
                "participants": [
                    {"role": "organizer", "user_id": str(org_id)},
                    {"role": "client", "user_id": str(client_id)},
                ],
            },
        }

        organizer, client = self.extractor.extract(payload)

        assert organizer == org_id
        assert client == client_id

    def test_missing_normalized_returns_nones(self) -> None:
        organizer, client = self.extractor.extract({})

        assert organizer is None
        assert client is None

    def test_empty_participants_returns_nones(self) -> None:
        payload = {"normalized": {"participants": []}}

        organizer, client = self.extractor.extract(payload)

        assert organizer is None
        assert client is None

    def test_invalid_uuid_skipped(self) -> None:
        payload = {
            "normalized": {
                "participants": [
                    {"role": "organizer", "user_id": "not-a-uuid"},
                ],
            },
        }

        organizer, client = self.extractor.extract(payload)

        assert organizer is None
        assert client is None

    def test_non_dict_participants_skipped(self) -> None:
        payload = {
            "normalized": {
                "participants": ["not-a-dict", 42],
            },
        }

        organizer, client = self.extractor.extract(payload)

        assert organizer is None
        assert client is None

    def test_uuid_object_accepted(self) -> None:
        org_id = uuid.uuid4()
        payload = {
            "normalized": {
                "participants": [
                    {"role": "organizer", "user_id": org_id},
                ],
            },
        }

        organizer, client = self.extractor.extract(payload)

        assert organizer == org_id
```

- [ ] **Step 5: Run ParticipantExtractor tests**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && python -m pytest tests/domain/test_participant_extractor.py -v`
Expected: all tests PASS

- [ ] **Step 6: Write BookingDataExtractor tests**

Create `event-saver/tests/domain/test_booking_extractor.py`:

```python
"""Tests for BookingDataExtractor domain service."""

from datetime import UTC, datetime

from event_saver.domain.services.booking_extractor import BookingDataExtractor


class TestExtract:
    def setup_method(self) -> None:
        self.extractor = BookingDataExtractor()

    def test_booking_created_extracts_status(self) -> None:
        result = self.extractor.extract(
            booking_id="book-1",
            event_type="booking.created",
            payload={
                "original": {
                    "start_time": "2026-01-20T10:00:00Z",
                    "end_time": "2026-01-20T11:00:00Z",
                },
            },
        )

        assert result.booking_id == "book-1"
        assert result.status == "created"
        assert result.start_time == datetime(2026, 1, 20, 10, 0, tzinfo=UTC)
        assert result.end_time == datetime(2026, 1, 20, 11, 0, tzinfo=UTC)

    def test_booking_cancelled_status(self) -> None:
        result = self.extractor.extract(
            booking_id="book-1",
            event_type="booking.cancelled",
            payload={"original": {}},
        )

        assert result.status == "cancelled"

    def test_unknown_event_type_no_status(self) -> None:
        result = self.extractor.extract(
            booking_id="book-1",
            event_type="booking.rescheduled",
            payload={"original": {}},
        )

        assert result.status is None

    def test_missing_original_returns_none_times(self) -> None:
        result = self.extractor.extract(
            booking_id="book-1",
            event_type="booking.created",
            payload={},
        )

        assert result.start_time is None
        assert result.end_time is None
        assert result.status == "created"
```

- [ ] **Step 7: Run BookingDataExtractor tests**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && python -m pytest tests/domain/test_booking_extractor.py -v`
Expected: all tests PASS

- [ ] **Step 8: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-saver
git add tests/domain/
git commit -m "test: add unit tests for domain layer services"
```

---

### Task 15: Application layer unit tests

**Files:**
- Create: `event-saver/tests/application/__init__.py`
- Create: `event-saver/tests/application/test_projection_executor.py`

- [ ] **Step 1: Create test directory**

Create `event-saver/tests/application/__init__.py` (empty file).

- [ ] **Step 2: Write ProjectionExecutor tests**

Create `event-saver/tests/application/test_projection_executor.py`:

```python
"""Tests for ProjectionExecutor application service."""

import uuid
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock

import pytest

from event_saver.application.services.projection_executor import ProjectionExecutor
from event_saver.domain.models.event import ParsedEvent, RawEventData


def _make_event(event_type: str = "booking.created") -> ParsedEvent:
    return ParsedEvent(
        raw=RawEventData(
            event_id="evt-001",
            event_type=event_type,
            source="booking",
            occurred_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
            booking_id="book-123",
            payload={},
        ),
        payload_hash="hash123",
    )


class FakeProjection:
    def __init__(self, *, handles: bool, result: tuple[str, dict[str, Any]] | None = None) -> None:
        self._handles = handles
        self._result = result
        self.handle_called = False

    def can_handle(self, event: ParsedEvent) -> bool:
        return self._handles

    async def handle(self, **kwargs: Any) -> tuple[str, dict[str, Any]] | None:
        self.handle_called = True
        return self._result


class FailingProjection:
    def can_handle(self, event: ParsedEvent) -> bool:
        return True

    async def handle(self, **kwargs: Any) -> tuple[str, dict[str, Any]] | None:
        raise ValueError("projection broke")


class TestExecuteProjections:
    @pytest.mark.asyncio
    async def test_executes_matching_handler(self) -> None:
        sql = AsyncMock()
        handler = FakeProjection(handles=True, result=("INSERT INTO t VALUES (:v)", {"v": 1}))
        executor = ProjectionExecutor(sql=sql, handlers=[handler])

        await executor.execute_projections(
            event=_make_event(),
            queue_name="events.booking.lifecycle",
            booking_ref_id=1,
            organizer_user_id=uuid.uuid4(),
            client_user_id=uuid.uuid4(),
        )

        assert handler.handle_called
        sql.execute.assert_called_once_with("INSERT INTO t VALUES (:v)", {"v": 1})

    @pytest.mark.asyncio
    async def test_skips_non_matching_handler(self) -> None:
        sql = AsyncMock()
        handler = FakeProjection(handles=False)
        executor = ProjectionExecutor(sql=sql, handlers=[handler])

        await executor.execute_projections(
            event=_make_event(),
            queue_name="events.booking.lifecycle",
            booking_ref_id=1,
            organizer_user_id=None,
            client_user_id=None,
        )

        assert not handler.handle_called
        sql.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_handler_returning_none_skips_sql(self) -> None:
        sql = AsyncMock()
        handler = FakeProjection(handles=True, result=None)
        executor = ProjectionExecutor(sql=sql, handlers=[handler])

        await executor.execute_projections(
            event=_make_event(),
            queue_name="events.booking.lifecycle",
            booking_ref_id=1,
            organizer_user_id=None,
            client_user_id=None,
        )

        assert handler.handle_called
        sql.execute.assert_not_called()

    @pytest.mark.asyncio
    async def test_projection_failure_propagates(self) -> None:
        sql = AsyncMock()
        handler = FailingProjection()
        executor = ProjectionExecutor(sql=sql, handlers=[handler])

        with pytest.raises(ValueError, match="projection broke"):
            await executor.execute_projections(
                event=_make_event(),
                queue_name="events.booking.lifecycle",
                booking_ref_id=1,
                organizer_user_id=None,
                client_user_id=None,
            )
```

- [ ] **Step 3: Run tests**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-saver && python -m pytest tests/application/test_projection_executor.py -v`
Expected: all tests PASS

- [ ] **Step 4: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-saver
git add tests/application/
git commit -m "test: add unit tests for ProjectionExecutor"
```

---

### Task 16: Update event-saver CLAUDE.md — Known Architecture Issues

**Files:**
- Modify: `event-saver/CLAUDE.md`

- [ ] **Step 1: Remove resolved known issue**

In `event-saver/CLAUDE.md`, find and remove the section:
```markdown
### Known Architecture Issues

- **Application imports infrastructure**: `IngestEventUseCase` imports concrete `BookingRepository`/`EventRepository` and `ProjectionExecutor` imports `BaseProjection` from infrastructure. These should be replaced with protocol interfaces in `interfaces/`.
```

Replace with:
```markdown
### Architecture Notes

- Application layer depends only on protocols from `interfaces/` — no direct infrastructure imports
- `IEventRepository`, `IBookingRepository` — repository abstractions
- `IProjectionHandler` — projection handler abstraction (replaces direct `BaseProjection` import)
```

- [ ] **Step 2: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-saver
git add CLAUDE.md
git commit -m "docs: update CLAUDE.md to reflect resolved architecture issues"
```
