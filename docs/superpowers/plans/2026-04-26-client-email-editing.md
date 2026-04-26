# Client Email Editing — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow admins to change a client's email from the frontend, propagating the change through RabbitMQ to event-users (with changelog and CRM webhook outbox), while preventing CRM sync from overwriting admin-set emails.

**Architecture:** event-admin publishes a CloudEvent via event-receiver → RabbitMQ → new consumer in event-users. Pre-validation (email uniqueness) is done synchronously via existing REST. CRM overwrite protection uses an `email_source` flag on the `users` table. Webhook delivery to CRM uses an outbox pattern with retries.

**Tech Stack:** Python 3.14, FastAPI, FastStream, SQLAlchemy (raw SQL), Dishka DI, Pydantic, React 19, TypeScript, Vite

---

## File Map

### event-schemas
| Action | File | Purpose |
|--------|------|---------|
| Modify | `event_schemas/types.py` | Add `USER_EMAIL_CHANGE_REQUESTED` to EventType, priority, version |
| Create | `event_schemas/user.py` | New payload model `UserEmailChangeRequestedPayload` |
| Modify | `event_schemas/__init__.py` | Export new type and payload |

### event-receiver
| Action | File | Purpose |
|--------|------|---------|
| Modify | `event_receiver/config.py` | Add routing rule for `admin` / `user.email.*` → `events.user.email`; add `admin_api_key` setting |
| Modify | `event_receiver/controllers/ingest.py` | Add `ingest_admin()` method |
| Modify | `event_receiver/interfaces/ingest.py` | Add `ingest_admin` to protocol |
| Modify | `event_receiver/routes.py` | Register `/event/admin` route |

### event-admin
| Action | File | Purpose |
|--------|------|---------|
| Create | `event_admin/adapters/event_publisher.py` | HTTP client to POST CloudEvents to event-receiver |
| Create | `event_admin/interfaces/event_publisher.py` | `IEventPublisher` protocol |
| Modify | `event_admin/config.py` | Add `event_receiver_url`, `event_receiver_api_token` |
| Modify | `event_admin/ioc.py` | Provide `IEventPublisher` |
| Modify | `event_admin/interfaces/users.py` | Add `get_user_by_email_role`, `get_email_changelog` methods to protocol |
| Modify | `event_admin/adapters/users_client.py` | Implement new methods |
| Modify | `event_admin/routes.py` | Add `POST /api/users/{user_id}/change-email` and `GET /api/users/{user_id}/email-changelog` |

### event-users
| Action | File | Purpose |
|--------|------|---------|
| Modify | `event_users/db/models.py` | Add `email_source` to User; add `UserEmailChangelog`, `WebhookOutbox` models |
| Create | `alembic/versions/0004_email_changelog_and_webhook_outbox.py` | Migration for new column + tables |
| Modify | `event_users/config.py` | Add RabbitMQ + webhook settings |
| Create | `event_users/dto/changelog.py` | `EmailChangelogDTO`, `EmailChangelogEntryDTO` |
| Create | `event_users/schemas/changelog.py` | Pydantic request/response for changelog endpoint |
| Create | `event_users/interfaces/changelog.py` | `IEmailChangelogDBAdapter` protocol |
| Create | `event_users/adapters/changelog_db.py` | SQL adapter for changelog + outbox |
| Create | `event_users/consumer.py` | FastStream RabbitMQ consumer for `events.user.email` |
| Create | `event_users/webhook/sender.py` | Outbox poller + HTTP webhook sender |
| Create | `event_users/webhook/client.py` | HTTP client for CRM webhook |
| Modify | `event_users/adapters/users_db.py` | Update `upsert_user_from_crm` with email_source protection |
| Modify | `event_users/routes.py` | Add `GET /api/users/{user_id}/email-changelog` endpoint |
| Modify | `event_users/ioc.py` | Provide new adapters, consumer, webhook sender |
| Modify | `event_users/main.py` | Start consumer + webhook poller in lifespan |

### event-admin-frontend
| Action | File | Purpose |
|--------|------|---------|
| Create | `src/modules/participants/EmailChangeModal.tsx` | Modal component for editing email + viewing changelog |
| Create | `src/modules/participants/emailChangeApi.ts` | API calls for change-email + changelog |
| Modify | `src/modules/participants/ParticipantsPage.tsx` | Add edit button per client row |
| Modify | `src/modules/bookings/BookingDetailsPage.tsx` | Add edit button next to client info |

---

## Task 1: event-schemas — New Event Type and Payload

**Files:**
- Modify: `event-schemas/event_schemas/types.py`
- Create: `event-schemas/event_schemas/user.py`
- Modify: `event-schemas/event_schemas/__init__.py`

- [ ] **Step 1: Add event type to types.py**

In `event_schemas/types.py`, add to `SourceType`:

```python
ADMIN = "admin"
```

Add to `EventType` enum (after the `# Notifications` group):

```python
# User management
USER_EMAIL_CHANGE_REQUESTED = "user.email.change_requested"
```

Add to `EVENT_PRIORITIES`:

```python
EventType.USER_EMAIL_CHANGE_REQUESTED: EventPriority.CRITICAL,
```

Add to `EVENT_SCHEMA_VERSIONS`:

```python
EventType.USER_EMAIL_CHANGE_REQUESTED: "v1",
```

- [ ] **Step 2: Create user payload model**

Create `event-schemas/event_schemas/user.py`:

```python
"""User management event payload schemas."""

from pydantic import BaseModel, EmailStr, Field


class UserEmailChangeRequestedPayload(BaseModel):
    """Payload for user.email.change_requested event."""

    user_id: str = Field(..., description="UUID of the client user")
    old_email: EmailStr = Field(..., description="Current email before change")
    new_email: EmailStr = Field(..., description="New email to set")
    requested_by: str = Field(..., description="Admin email who requested the change")

    model_config = {
        "json_schema_extra": {
            "example": {
                "user_id": "550e8400-e29b-41d4-a716-446655440001",
                "old_email": "old@example.com",
                "new_email": "new@example.com",
                "requested_by": "admin@company.com",
            }
        }
    }
```

- [ ] **Step 3: Update __init__.py exports**

In `event_schemas/__init__.py`, add import:

```python
from event_schemas.user import UserEmailChangeRequestedPayload
```

Add to `__all__`:

```python
"UserEmailChangeRequestedPayload",
```

- [ ] **Step 4: Verify lint passes**

Run: `cd event-schemas && ruff check . && ruff format --check .`

- [ ] **Step 5: Commit**

```bash
git add event-schemas/
git commit -m "feat(event-schemas): add USER_EMAIL_CHANGE_REQUESTED event type and payload"
```

---

## Task 2: event-receiver — Admin Ingest Endpoint and Routing

**Files:**
- Modify: `event-receiver/event_receiver/config.py`
- Modify: `event-receiver/event_receiver/interfaces/ingest.py`
- Modify: `event-receiver/event_receiver/controllers/ingest.py`
- Modify: `event-receiver/event_receiver/routes.py`

- [ ] **Step 1: Add routing rule and admin API key to config.py**

In `event_receiver/config.py`, add to `_default_route_rules()` list (before the closing `]`):

```python
RouteRule(
    destination="events.user.email",
    source_pattern="admin",
    type_pattern="user.email.*",
),
```

In `Settings` class, add field:

```python
admin_api_key: str = Field(strict=True)
```

- [ ] **Step 2: Add ingest_admin to IIngestController protocol**

In `event_receiver/interfaces/ingest.py`, add method:

```python
async def ingest_admin(self, *, headers: Mapping[str, str], body: bytes) -> None: ...
```

- [ ] **Step 3: Implement ingest_admin in IngestController**

In `event_receiver/controllers/ingest.py`, add method to `IngestController` class. Follow the `ingest_booking` pattern — API key auth, CloudEvent parsing, publish:

```python
async def ingest_admin(self, *, headers: Mapping[str, str], body: bytes) -> None:
    trace_id = extract_trace_id_from_headers(dict(headers))
    logger.info("Started Admin ingest", trace_id=trace_id)

    if self._settings.admin_api_key != headers.get("Authorization"):
        logger.warning("Admin ingest failed: invalid API key")
        raise UnauthorizedError("Invalid Admin API key")

    try:
        incoming = from_http(headers=headers, data=body)
    except Exception as exc:
        logger.warning("Admin event parsing failed")
        raise BadRequestError("Invalid Admin event payload or headers") from exc

    data = dict(incoming.data) if incoming.data else {}

    await self._publisher.publish(
        source=incoming.source,
        event_type=incoming.type,
        event_id=incoming.id,
        event_time=incoming.time,
        data=data,
        trace_id=trace_id,
    )
    logger.info(
        "Admin ingest completed",
        source=incoming.source,
        event_type=incoming.type,
        event_id=incoming.id,
        trace_id=trace_id,
    )
```

- [ ] **Step 4: Register /event/admin route**

In `event_receiver/routes.py`, add to `INGEST_ROUTE_TO_METHOD`:

```python
"/event/admin": "ingest_admin",
```

- [ ] **Step 5: Verify lint passes**

Run: `cd event-receiver && ruff check . && ruff format --check .`

- [ ] **Step 6: Commit**

```bash
git add event-receiver/
git commit -m "feat(event-receiver): add /event/admin ingest endpoint with routing to events.user.email"
```

---

## Task 3: event-admin — Event Publisher Adapter

**Files:**
- Create: `event-admin/event_admin/interfaces/event_publisher.py`
- Create: `event-admin/event_admin/adapters/event_publisher.py`
- Modify: `event-admin/event_admin/config.py`
- Modify: `event-admin/event_admin/ioc.py`

- [ ] **Step 1: Create IEventPublisher protocol**

Create `event-admin/event_admin/interfaces/event_publisher.py`:

```python
"""Interface for publishing CloudEvents to event-receiver."""

from typing import Any, Protocol


class IEventPublisher(Protocol):
    async def publish(
        self,
        *,
        source: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None: ...
```

- [ ] **Step 2: Create EventPublisherClient adapter**

Create `event-admin/event_admin/adapters/event_publisher.py`:

```python
"""HTTP client for publishing CloudEvents to event-receiver."""

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from cloudevents.conversion import to_binary
from cloudevents.http import CloudEvent
from httpx import AsyncClient


logger = structlog.get_logger(__name__)


class EventPublisherClient:
    def __init__(self, *, http_client: AsyncClient, api_key: str) -> None:
        self._client = http_client
        self._api_key = api_key

    async def publish(
        self,
        *,
        source: str,
        event_type: str,
        data: dict[str, Any],
    ) -> None:
        event = CloudEvent(
            {
                "type": event_type,
                "source": source,
                "id": str(uuid.uuid4()),
                "time": datetime.now(UTC).isoformat(),
                "specversion": "1.0",
            },
            data,
        )
        headers, body = to_binary(event)
        headers["Authorization"] = self._api_key

        response = await self._client.post(
            "/event/admin",
            content=body,
            headers=dict(headers),
        )
        response.raise_for_status()
        logger.info(
            "CloudEvent published to event-receiver",
            source=source,
            event_type=event_type,
        )
```

- [ ] **Step 3: Add config settings**

In `event-admin/event_admin/config.py`, add to `Settings`:

```python
event_receiver_url: AnyHttpUrl = Field(strict=True)
event_receiver_api_key: str = Field(strict=True)
```

- [ ] **Step 4: Wire up DI in ioc.py**

In `event-admin/event_admin/ioc.py`, add imports:

```python
from event_admin.adapters.event_publisher import EventPublisherClient
from event_admin.interfaces.event_publisher import IEventPublisher
```

Add providers to `AppProvider`:

```python
@provide(scope=Scope.APP)
async def provide_event_receiver_client(self, settings: Settings) -> AsyncGenerator[AsyncClient]:
    async with AsyncClient(base_url=str(settings.event_receiver_url), timeout=10) as client:
        yield client

@provide(scope=Scope.APP)
def provide_event_publisher(
    self, event_receiver_client: AsyncClient, settings: Settings
) -> IEventPublisher:
    return EventPublisherClient(
        http_client=event_receiver_client,
        api_key=settings.event_receiver_api_key,
    )
```

Note: The existing `provide_http_client` creates an `AsyncClient` for event-users. We need a separate one for event-receiver. Rename the existing one's internal variable to avoid DI ambiguity. Use Dishka's `component` parameter or a wrapper type. Simplest approach: combine both clients into the same provider or use a named wrapper.

Alternative (simpler): don't use DI for the event-receiver client. Create it inside `provide_event_publisher`:

```python
@provide(scope=Scope.APP)
async def provide_event_publisher(self, settings: Settings) -> AsyncGenerator[IEventPublisher]:
    async with AsyncClient(base_url=str(settings.event_receiver_url), timeout=10) as client:
        yield EventPublisherClient(
            http_client=client,
            api_key=settings.event_receiver_api_key,
        )
```

- [ ] **Step 5: Verify lint passes**

Run: `cd event-admin && ruff check . && ruff format --check .`

- [ ] **Step 6: Commit**

```bash
git add event-admin/
git commit -m "feat(event-admin): add EventPublisher adapter for CloudEvent publishing to event-receiver"
```

---

## Task 4: event-admin — Change Email and Changelog Endpoints

**Files:**
- Modify: `event-admin/event_admin/interfaces/users.py`
- Modify: `event-admin/event_admin/adapters/users_client.py`
- Modify: `event-admin/event_admin/routes.py`

- [ ] **Step 1: Extend IUsersClient protocol**

In `event-admin/event_admin/interfaces/users.py`, add methods:

```python
async def get_user_by_email_role(self, email: str, role: str) -> dict[str, Any] | None: ...
async def get_email_changelog(self, user_id: uuid.UUID, *, limit: int, offset: int) -> dict[str, Any]: ...
```

- [ ] **Step 2: Implement in UsersClient**

In `event-admin/event_admin/adapters/users_client.py`, add methods:

```python
async def get_user_by_email_role(self, email: str, role: str) -> dict[str, Any] | None:
    response = await self._client.get(
        f"/api/users/roles/{role}/emails/{email}",
        headers=self._headers,
    )
    if response.status_code == 404:
        return None
    response.raise_for_status()
    return response.json()

async def get_email_changelog(self, user_id: uuid.UUID, *, limit: int, offset: int) -> dict[str, Any]:
    response = await self._client.get(
        f"/api/users/{user_id}/email-changelog",
        params={"limit": limit, "offset": offset},
        headers=self._headers,
    )
    response.raise_for_status()
    return response.json()
```

- [ ] **Step 3: Add change-email and changelog routes**

In `event-admin/event_admin/routes.py`, add imports:

```python
from event_admin.auth import get_current_user, require_admin
from event_admin.interfaces.event_publisher import IEventPublisher
from pydantic import BaseModel, EmailStr
```

Add request schema (inline in routes.py or separate file — follow existing pattern of inline for simple schemas):

```python
class ChangeEmailRequest(BaseModel):
    new_email: EmailStr
```

Add endpoints to `users_router`:

```python
@users_router.post(
    "/id/{user_id}/change-email",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Request client email change",
    description="Pre-validates uniqueness, then publishes email change event via RabbitMQ.",
)
async def change_user_email(
    user_id: uuid.UUID,
    body: ChangeEmailRequest,
    client: FromDishka[IUsersClient],
    publisher: FromDishka[IEventPublisher],
    user: Annotated[TokenPayload, Depends(require_admin)],
) -> dict[str, str]:
    # Fetch current user — verify exists and is a client
    try:
        current_user = await client.get_user(user_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found") from exc
        raise HTTPException(status_code=exc.response.status_code) from exc

    if current_user.get("role") != "client":
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Only client emails can be changed",
        )

    old_email = current_user["email"]
    if old_email == body.new_email:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="New email is the same as current email",
        )

    # Pre-validate uniqueness
    existing = await client.get_user_by_email_role(body.new_email, "client")
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Email already in use by another client",
        )

    await publisher.publish(
        source="admin",
        event_type="user.email.change_requested",
        data={
            "user_id": str(user_id),
            "old_email": old_email,
            "new_email": body.new_email,
            "requested_by": user.sub,
        },
    )

    return {"status": "accepted"}


@users_router.get(
    "/id/{user_id}/email-changelog",
    summary="Get email change history",
    description="Proxy to event-users service. Returns email change audit log.",
)
async def get_email_changelog(
    user_id: uuid.UUID,
    client: FromDishka[IUsersClient],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> dict:
    try:
        return await client.get_email_changelog(user_id, limit=limit, offset=offset)
    except httpx.HTTPStatusError as exc:
        raise HTTPException(status_code=exc.response.status_code) from exc
```

Add `TokenPayload` import:

```python
from event_admin.auth import TokenPayload, get_current_user, require_admin
```

- [ ] **Step 4: Verify lint passes**

Run: `cd event-admin && ruff check . && ruff format --check .`

- [ ] **Step 5: Commit**

```bash
git add event-admin/
git commit -m "feat(event-admin): add change-email and email-changelog endpoints"
```

---

## Task 5: event-users — Database Migration

**Files:**
- Modify: `event-users/event_users/db/models.py`
- Create: `event-users/alembic/versions/0004_email_source_changelog_webhook_outbox.py`

- [ ] **Step 1: Update SQLAlchemy models**

In `event-users/event_users/db/models.py`, add `email_source` to `User`:

```python
email_source: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'crm'"))
```

Add new models:

```python
class UserEmailChangelog(Base):
    __tablename__ = "user_email_changelog"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    old_email: Mapped[str] = mapped_column(Text, nullable=False)
    new_email: Mapped[str] = mapped_column(Text, nullable=False)
    changed_by: Mapped[str] = mapped_column(Text, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )

    __table_args__ = (
        Index("ix_user_email_changelog_user_id", "user_id"),
        Index("ix_user_email_changelog_changed_at", "changed_at"),
    )


class WebhookOutbox(Base):
    __tablename__ = "webhook_outbox"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    attempts: Mapped[int] = mapped_column(nullable=False, server_default=text("0"))
    max_attempts: Mapped[int] = mapped_column(nullable=False, server_default=text("5"))
    next_retry_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    delivered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    __table_args__ = (
        Index(
            "ix_webhook_outbox_pending",
            "status",
            "next_retry_at",
            postgresql_where=text("status IN ('pending', 'processing')"),
        ),
    )
```

Add import for `JSONB`:

```python
from sqlalchemy.dialects.postgresql import JSONB, UUID
```

- [ ] **Step 2: Generate Alembic migration**

Run: `cd event-users && alembic revision --autogenerate -m "add email_source, changelog, webhook_outbox"`

Then verify the generated migration and adjust if needed. The migration should:
1. Add `email_source TEXT NOT NULL DEFAULT 'crm'` to `users`
2. Create `user_email_changelog` table with indexes
3. Create `webhook_outbox` table with partial index

- [ ] **Step 3: Verify migration applies cleanly**

Run: `cd event-users && alembic upgrade head`

- [ ] **Step 4: Commit**

```bash
git add event-users/event_users/db/models.py event-users/alembic/
git commit -m "feat(event-users): add email_source column, changelog and webhook_outbox tables"
```

---

## Task 6: event-users — Changelog DTO, Schema, and REST Endpoint

**Files:**
- Create: `event-users/event_users/dto/changelog.py`
- Create: `event-users/event_users/schemas/changelog.py`
- Create: `event-users/event_users/interfaces/changelog.py`
- Create: `event-users/event_users/adapters/changelog_db.py`
- Modify: `event-users/event_users/routes.py`
- Modify: `event-users/event_users/ioc.py`

- [ ] **Step 1: Create changelog DTO**

Create `event-users/event_users/dto/changelog.py`:

```python
import uuid
from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class EmailChangelogEntryDTO:
    id: uuid.UUID
    old_email: str
    new_email: str
    changed_by: str
    changed_at: datetime
```

- [ ] **Step 2: Create changelog schema**

Create `event-users/event_users/schemas/changelog.py`:

```python
import uuid
from datetime import datetime

from pydantic import BaseModel

from event_users.dto.changelog import EmailChangelogEntryDTO


class EmailChangelogEntryResponse(BaseModel):
    id: uuid.UUID
    old_email: str
    new_email: str
    changed_by: str
    changed_at: datetime

    @classmethod
    def from_dto(cls, dto: EmailChangelogEntryDTO) -> EmailChangelogEntryResponse:
        return cls(
            id=dto.id,
            old_email=dto.old_email,
            new_email=dto.new_email,
            changed_by=dto.changed_by,
            changed_at=dto.changed_at,
        )


class EmailChangelogResponse(BaseModel):
    items: list[EmailChangelogEntryResponse]
    total: int
```

- [ ] **Step 3: Create changelog interface**

Create `event-users/event_users/interfaces/changelog.py`:

```python
import uuid
from typing import Protocol

from event_users.dto.changelog import EmailChangelogEntryDTO


class IEmailChangelogDBAdapter(Protocol):
    async def get_changelog(
        self, user_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[EmailChangelogEntryDTO], int]: ...

    async def add_entry(
        self,
        *,
        user_id: uuid.UUID,
        old_email: str,
        new_email: str,
        changed_by: str,
    ) -> None: ...

    async def is_email_changed_by_admin(self, email: str, role: str) -> bool: ...

    async def add_webhook_outbox(
        self,
        *,
        event_type: str,
        payload: dict,
    ) -> None: ...
```

- [ ] **Step 4: Create changelog DB adapter**

Create `event-users/event_users/adapters/changelog_db.py`:

```python
import uuid

import structlog
from sqlalchemy.engine import RowMapping

from event_users.dto.changelog import EmailChangelogEntryDTO
from event_users.interfaces.sql import ISqlExecutor


logger = structlog.get_logger(__name__)


def _entry_from_row(row: RowMapping) -> EmailChangelogEntryDTO:
    return EmailChangelogEntryDTO(
        id=row["id"],
        old_email=row["old_email"],
        new_email=row["new_email"],
        changed_by=row["changed_by"],
        changed_at=row["changed_at"],
    )


class EmailChangelogDBAdapter:
    def __init__(self, sql_executor: ISqlExecutor) -> None:
        self._sql = sql_executor

    async def get_changelog(
        self, user_id: uuid.UUID, *, limit: int, offset: int
    ) -> tuple[list[EmailChangelogEntryDTO], int]:
        count_row = await self._sql.fetch_one(
            "SELECT COUNT(*) AS total FROM user_email_changelog WHERE user_id = :user_id",
            {"user_id": user_id},
        )
        total: int = count_row["total"] if count_row else 0

        rows = await self._sql.fetch_all(
            """
            SELECT id, old_email, new_email, changed_by, changed_at
            FROM user_email_changelog
            WHERE user_id = :user_id
            ORDER BY changed_at DESC
            LIMIT :limit OFFSET :offset
            """,
            {"user_id": user_id, "limit": limit, "offset": offset},
        )
        return [_entry_from_row(r) for r in rows], total

    async def add_entry(
        self,
        *,
        user_id: uuid.UUID,
        old_email: str,
        new_email: str,
        changed_by: str,
    ) -> None:
        await self._sql.execute(
            """
            INSERT INTO user_email_changelog (user_id, old_email, new_email, changed_by)
            VALUES (:user_id, :old_email, :new_email, :changed_by)
            """,
            {
                "user_id": user_id,
                "old_email": old_email,
                "new_email": new_email,
                "changed_by": changed_by,
            },
        )
        logger.info(
            "Email changelog entry added",
            user_id=str(user_id),
            old_email=old_email,
            new_email=new_email,
        )

    async def is_email_changed_by_admin(self, email: str, role: str) -> bool:
        """Check if this email was recently changed away from by an admin (for CRM sync protection)."""
        row = await self._sql.fetch_one(
            """
            SELECT 1 FROM user_email_changelog
            WHERE old_email = :email
              AND user_id IN (
                  SELECT id FROM users WHERE role = :role AND email_source = 'admin'
              )
            LIMIT 1
            """,
            {"email": email, "role": role},
        )
        return row is not None

    async def add_webhook_outbox(self, *, event_type: str, payload: dict) -> None:
        await self._sql.execute(
            """
            INSERT INTO webhook_outbox (event_type, payload)
            VALUES (:event_type, :payload::jsonb)
            """,
            {"event_type": event_type, "payload": __import__("json").dumps(payload)},
        )
        logger.info("Webhook outbox entry added", event_type=event_type)
```

- [ ] **Step 5: Add changelog endpoint to routes.py**

In `event-users/event_users/routes.py`, add imports:

```python
from event_users.interfaces.changelog import IEmailChangelogDBAdapter
from event_users.schemas.changelog import EmailChangelogEntryResponse, EmailChangelogResponse
```

Add endpoint:

```python
@users_router.get("/{user_id}/email-changelog", response_model=EmailChangelogResponse)
async def get_email_changelog(
    user_id: uuid.UUID,
    changelog_adapter: FromDishka[IEmailChangelogDBAdapter],
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> EmailChangelogResponse:
    entries, total = await changelog_adapter.get_changelog(user_id, limit=limit, offset=offset)
    return EmailChangelogResponse(
        items=[EmailChangelogEntryResponse.from_dto(e) for e in entries],
        total=total,
    )
```

- [ ] **Step 6: Register changelog adapter in DI**

In `event-users/event_users/ioc.py`, add imports:

```python
from event_users.adapters.changelog_db import EmailChangelogDBAdapter
from event_users.interfaces.changelog import IEmailChangelogDBAdapter
```

Add provider:

```python
@provide(scope=Scope.REQUEST)
def provide_changelog_adapter(self, sql_executor: ISqlExecutor) -> IEmailChangelogDBAdapter:
    return EmailChangelogDBAdapter(sql_executor)
```

- [ ] **Step 7: Verify lint passes**

Run: `cd event-users && ruff check . && ruff format --check .`

- [ ] **Step 8: Commit**

```bash
git add event-users/
git commit -m "feat(event-users): add email changelog DTO, adapter, schema, and REST endpoint"
```

---

## Task 7: event-users — RabbitMQ Consumer

**Files:**
- Create: `event-users/event_users/consumer.py`
- Modify: `event-users/event_users/config.py`
- Modify: `event-users/event_users/main.py`
- Modify: `event-users/event_users/ioc.py`

- [ ] **Step 1: Add RabbitMQ settings to config**

In `event-users/event_users/config.py`, add:

```python
# RabbitMQ consumer
rabbit_url: str = "amqp://guest:guest@localhost:5672/"
is_consumer_enabled: bool = False
```

- [ ] **Step 2: Create the consumer module**

Create `event-users/event_users/consumer.py`:

```python
"""RabbitMQ consumer for user email change events."""

import json
import uuid
from datetime import UTC, datetime

import structlog
from cloudevents.http import from_http
from faststream.rabbit import RabbitBroker, RabbitQueue

from event_users.adapters.changelog_db import EmailChangelogDBAdapter
from event_users.adapters.sql import SqlExecutor
from event_users.interfaces.sql import ISqlExecutor
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


logger = structlog.get_logger(__name__)


async def handle_email_change(
    *,
    sessionmaker: async_sessionmaker[AsyncSession],
    user_id_str: str,
    old_email: str,
    new_email: str,
    requested_by: str,
) -> None:
    """Process email change in a single transaction."""
    async with sessionmaker() as session:
        try:
            sql: ISqlExecutor = SqlExecutor(session)
            changelog_db = EmailChangelogDBAdapter(sql)

            user_id = uuid.UUID(user_id_str)

            # Update user email and set email_source = 'admin'
            await sql.execute(
                """
                UPDATE users
                SET email = :new_email, email_source = 'admin', updated_at = now()
                WHERE id = :user_id
                """,
                {"new_email": new_email, "user_id": user_id},
            )

            # Update email contact
            await sql.execute(
                """
                INSERT INTO user_contacts (user_id, channel, contact_id)
                VALUES (:user_id, 'email', :new_email)
                ON CONFLICT (user_id, channel)
                DO UPDATE SET contact_id = EXCLUDED.contact_id, updated_at = now()
                """,
                {"user_id": user_id, "new_email": new_email},
            )

            # Add changelog entry
            await changelog_db.add_entry(
                user_id=user_id,
                old_email=old_email,
                new_email=new_email,
                changed_by=requested_by,
            )

            # Add webhook outbox entry
            await changelog_db.add_webhook_outbox(
                event_type="user.email.changed",
                payload={
                    "user_id": user_id_str,
                    "old_email": old_email,
                    "new_email": new_email,
                    "changed_at": datetime.now(UTC).isoformat(),
                },
            )

            await session.commit()
            logger.info(
                "Email change processed",
                user_id=user_id_str,
                old_email=old_email,
                new_email=new_email,
            )
        except Exception:
            await session.rollback()
            logger.exception(
                "Email change failed",
                user_id=user_id_str,
            )
            raise


class EmailChangeConsumer:
    """Manages RabbitMQ subscription for email change events."""

    def __init__(
        self,
        *,
        broker: RabbitBroker,
        sessionmaker: async_sessionmaker[AsyncSession],
    ) -> None:
        self._broker = broker
        self._sessionmaker = sessionmaker
        self._queue = RabbitQueue(
            "events.user.email",
            durable=True,
            arguments={
                "x-max-priority": 10,
                "x-dead-letter-exchange": "events.dlx",
                "x-dead-letter-routing-key": "events.user.email.dlq",
            },
        )

    async def start(self) -> None:
        @self._broker.subscriber(self._queue)
        async def on_message(message) -> None:
            try:
                event = from_http(
                    headers=dict(message.headers or {}),
                    data=message.body,
                )
            except Exception:
                logger.exception("Failed to parse CloudEvent from message")
                return

            event_type = event["type"]
            if event_type != "user.email.change_requested":
                logger.warning("Unknown event type, skipping", event_type=event_type)
                return

            data = event.data or {}
            await handle_email_change(
                sessionmaker=self._sessionmaker,
                user_id_str=data["user_id"],
                old_email=data["old_email"],
                new_email=data["new_email"],
                requested_by=data["requested_by"],
            )

        await self._broker.start()
        logger.info("Email change consumer started")

    async def stop(self) -> None:
        await self._broker.close()
        logger.info("Email change consumer stopped")
```

Note: The consumer creates its own session per message (like CRM sync does) for transactional isolation. Email contact upsert uses raw SQL directly rather than calling through UsersDBAdapter to keep the consumer self-contained.

- [ ] **Step 3: Wire up in ioc.py**

In `event-users/event_users/ioc.py`, add imports:

```python
from faststream.rabbit import RabbitBroker
from event_users.consumer import EmailChangeConsumer
```

Add providers:

```python
@provide(scope=Scope.APP)
def provide_rabbit_broker(self, settings: Settings) -> RabbitBroker:
    return RabbitBroker(str(settings.rabbit_url))

@provide(scope=Scope.APP)
def provide_email_change_consumer(
    self,
    broker: RabbitBroker,
    sessionmaker: async_sessionmaker[AsyncSession],
) -> EmailChangeConsumer:
    return EmailChangeConsumer(broker=broker, sessionmaker=sessionmaker)
```

- [ ] **Step 4: Start consumer in lifespan**

In `event-users/event_users/main.py`, update the `lifespan` function. After the CRM sync block, add:

```python
if settings.is_consumer_enabled:
    email_consumer = await container.get(EmailChangeConsumer)
    await email_consumer.start()
    logger.info("Email change consumer started")
```

In the shutdown section, add:

```python
if settings.is_consumer_enabled:
    await email_consumer.stop()
```

- [ ] **Step 5: Add `faststream[rabbit]` dependency**

Run: `cd event-users && uv add "faststream[rabbit]"` and also `uv add cloudevents`

- [ ] **Step 6: Verify lint passes**

Run: `cd event-users && ruff check . && ruff format --check .`

- [ ] **Step 7: Commit**

```bash
git add event-users/
git commit -m "feat(event-users): add RabbitMQ consumer for email change events"
```

---

## Task 8: event-users — CRM Sync Protection

**Files:**
- Modify: `event-users/event_users/adapters/users_db.py`
- Modify: `event-users/event_users/crm/sync.py`

- [ ] **Step 1: Update upsert_user_from_crm with email_source WHERE clause**

In `event-users/event_users/adapters/users_db.py`, modify `upsert_user_from_crm` method. Replace the existing SQL (lines 270-281):

```python
async def upsert_user_from_crm(
    self,
    email: str,
    role: str,
    time_zone: str | None,
    name: str | None = None,
    contacts: list[CreateUserContactDTO] | None = None,
) -> None:
    # COALESCE preserves existing values when CRM sends NULL. This is intentional:
    # CRM null means "not provided", not "clear this field".
    # If CRM semantics change, switch to direct assignment.
    await self._sql.execute(
        """
        INSERT INTO users (email, name, role, time_zone, email_source)
        VALUES (:email, :name, :role, :time_zone, 'crm')
        ON CONFLICT (email, role)
        DO UPDATE SET
            name = COALESCE(EXCLUDED.name, users.name),
            time_zone = COALESCE(EXCLUDED.time_zone, users.time_zone),
            updated_at = now()
        WHERE users.email_source != 'admin'
        """,
        {"email": email, "name": name, "role": role, "time_zone": time_zone},
    )

    user_row = await self._sql.fetch_one(
        "SELECT id FROM users WHERE email = :email AND role = :role",
        {"email": email, "role": role},
    )
    if user_row is not None:
        contacts_for_upsert: list[CreateUserContactDTO] = [
            *(contacts or []),
            CreateUserContactDTO(channel="email", contact_id=email),
        ]
        await self._upsert_contacts(user_row["id"], contacts_for_upsert)

    logger.debug("User upserted from CRM", email=email, role=role)
```

- [ ] **Step 2: Add pre-upsert check in CRM sync**

In `event-users/event_users/crm/sync.py`, modify the `CrmSyncService.sync()` method. The `for user in users` loop needs to check the changelog before upserting. Import and inject the changelog adapter:

Update `CrmSyncService.__init__`:

```python
class CrmSyncService:
    def __init__(
        self,
        crm_client: CrmClient,
        db_adapter: IUsersDBAdapter,
        changelog_adapter: IEmailChangelogDBAdapter,
        encryption_key: bytes,
    ) -> None:
        self._client = crm_client
        self._db = db_adapter
        self._changelog = changelog_adapter
        self._key = encryption_key
```

Update the sync loop body (inside `for user in users:`):

```python
for user in users:
    # Skip users whose email was changed by admin (prevents duplicate creation)
    if await self._changelog.is_email_changed_by_admin(user.email, user.role):
        logger.info(
            "Skipping CRM upsert: email was changed by admin",
            email=user.email,
            role=user.role,
        )
        continue
    await self._db.upsert_user_from_crm(
        email=user.email,
        role=user.role,
        name=user.name,
        time_zone=user.time_zone,
        contacts=user.contacts,
    )
```

Update `CrmSyncRunner.run()` to create the changelog adapter alongside the DB adapter:

```python
async with self._sessionmaker() as session:
    sql_executor = SqlExecutor(session)
    db_adapter = UsersDBAdapter(sql_executor=sql_executor)
    changelog_adapter = EmailChangelogDBAdapter(sql_executor=sql_executor)
    service = CrmSyncService(
        crm_client=self._client,
        db_adapter=db_adapter,
        changelog_adapter=changelog_adapter,
        encryption_key=self._key,
    )
    await service.sync()
```

Add import:

```python
from event_users.adapters.changelog_db import EmailChangelogDBAdapter
```

- [ ] **Step 3: Verify lint passes**

Run: `cd event-users && ruff check . && ruff format --check .`

- [ ] **Step 4: Commit**

```bash
git add event-users/
git commit -m "feat(event-users): add CRM sync protection for admin-set emails"
```

---

## Task 9: event-users — Webhook Outbox Poller

**Files:**
- Create: `event-users/event_users/webhook/client.py`
- Create: `event-users/event_users/webhook/sender.py`
- Modify: `event-users/event_users/config.py`
- Modify: `event-users/event_users/ioc.py`
- Modify: `event-users/event_users/main.py`

- [ ] **Step 1: Add webhook config**

In `event-users/event_users/config.py`, add:

```python
# CRM webhook
crm_webhook_url: str = ""
crm_webhook_token: str = ""
is_webhook_enabled: bool = False
webhook_poll_interval_seconds: int = 1
webhook_batch_size: int = 10
```

- [ ] **Step 2: Create webhook HTTP client**

Create `event-users/event_users/webhook/__init__.py` (empty file).

Create `event-users/event_users/webhook/client.py`:

```python
"""HTTP client for CRM webhook delivery."""

from typing import Any

import structlog
from httpx import AsyncClient


logger = structlog.get_logger(__name__)


class CrmWebhookClient:
    def __init__(self, *, http_client: AsyncClient, token: str) -> None:
        self._client = http_client
        self._token = token

    async def send(self, payload: dict[str, Any]) -> None:
        response = await self._client.post(
            "",
            json=payload,
            headers={"Authorization": f"Bearer {self._token}"},
        )
        response.raise_for_status()
        logger.info("Webhook delivered to CRM", event_type=payload.get("event_type"))
```

- [ ] **Step 3: Create webhook outbox sender**

Create `event-users/event_users/webhook/sender.py`:

```python
"""Outbox poller that delivers webhook payloads to CRM."""

import asyncio
import json
from contextlib import suppress

import structlog
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from event_users.adapters.sql import SqlExecutor
from event_users.webhook.client import CrmWebhookClient


logger = structlog.get_logger(__name__)


class WebhookOutboxSender:
    def __init__(
        self,
        *,
        sessionmaker: async_sessionmaker[AsyncSession],
        webhook_client: CrmWebhookClient,
        poll_interval: int = 1,
        batch_size: int = 10,
    ) -> None:
        self._sessionmaker = sessionmaker
        self._webhook_client = webhook_client
        self._poll_interval = poll_interval
        self._batch_size = batch_size

    async def run(self) -> None:
        """Long-running loop: poll outbox, deliver, update status."""
        while True:
            try:
                await self._process_batch()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Webhook outbox poll failed")
            await asyncio.sleep(self._poll_interval)

    async def _process_batch(self) -> None:
        async with self._sessionmaker() as session:
            sql = SqlExecutor(session)

            rows = await sql.fetch_all(
                """
                SELECT id, event_type, payload, attempts, max_attempts
                FROM webhook_outbox
                WHERE status IN ('pending', 'processing')
                  AND next_retry_at <= now()
                ORDER BY created_at
                LIMIT :batch_size
                FOR UPDATE SKIP LOCKED
                """,
                {"batch_size": self._batch_size},
            )

            for row in rows:
                outbox_id = row["id"]
                attempts = row["attempts"] + 1
                payload = row["payload"] if isinstance(row["payload"], dict) else json.loads(row["payload"])

                try:
                    await self._webhook_client.send(payload)
                    # Success: mark delivered and reset email_source
                    await sql.execute(
                        """
                        UPDATE webhook_outbox
                        SET status = 'delivered', delivered_at = now(), attempts = :attempts
                        WHERE id = :id
                        """,
                        {"id": outbox_id, "attempts": attempts},
                    )

                    # Reset email_source to 'crm' — CRM now has the new email
                    user_id = payload.get("user_id")
                    if user_id:
                        await sql.execute(
                            "UPDATE users SET email_source = 'crm' WHERE id = :user_id",
                            {"user_id": user_id},
                        )

                    logger.info("Webhook delivered", outbox_id=str(outbox_id))

                except Exception as exc:
                    error_msg = str(exc)[:500]
                    if attempts >= row["max_attempts"]:
                        await sql.execute(
                            """
                            UPDATE webhook_outbox
                            SET status = 'failed', attempts = :attempts, last_error = :error
                            WHERE id = :id
                            """,
                            {"id": outbox_id, "attempts": attempts, "error": error_msg},
                        )
                        logger.error(
                            "Webhook permanently failed",
                            outbox_id=str(outbox_id),
                            attempts=attempts,
                        )
                    else:
                        delay_seconds = 10 * attempts * attempts
                        await sql.execute(
                            """
                            UPDATE webhook_outbox
                            SET status = 'pending',
                                attempts = :attempts,
                                last_error = :error,
                                next_retry_at = now() + make_interval(secs => :delay)
                            WHERE id = :id
                            """,
                            {
                                "id": outbox_id,
                                "attempts": attempts,
                                "error": error_msg,
                                "delay": delay_seconds,
                            },
                        )
                        logger.warning(
                            "Webhook delivery failed, will retry",
                            outbox_id=str(outbox_id),
                            attempts=attempts,
                            next_retry_seconds=delay_seconds,
                        )

            await session.commit()
```

- [ ] **Step 4: Wire up DI and lifespan**

In `event-users/event_users/ioc.py`, add imports:

```python
from event_users.webhook.client import CrmWebhookClient
from event_users.webhook.sender import WebhookOutboxSender
```

Add providers:

```python
@provide(scope=Scope.APP)
async def provide_webhook_client(self, settings: Settings) -> AsyncGenerator[CrmWebhookClient]:
    async with AsyncClient(base_url=settings.crm_webhook_url, timeout=30) as client:
        yield CrmWebhookClient(http_client=client, token=settings.crm_webhook_token)

@provide(scope=Scope.APP)
def provide_webhook_sender(
    self,
    settings: Settings,
    sessionmaker: async_sessionmaker[AsyncSession],
    webhook_client: CrmWebhookClient,
) -> WebhookOutboxSender:
    return WebhookOutboxSender(
        sessionmaker=sessionmaker,
        webhook_client=webhook_client,
        poll_interval=settings.webhook_poll_interval_seconds,
        batch_size=settings.webhook_batch_size,
    )
```

In `event-users/event_users/main.py`, add to lifespan (after CRM sync and consumer blocks):

```python
if settings.is_webhook_enabled:
    webhook_sender = await container.get(WebhookOutboxSender)
    webhook_task = asyncio.create_task(webhook_sender.run(), name="webhook-outbox")
    logger.info("Webhook outbox sender started")
```

In shutdown:

```python
if settings.is_webhook_enabled:
    webhook_task.cancel()
    with suppress(asyncio.CancelledError):
        await webhook_task
```

Add import at top of `main.py`:

```python
from event_users.webhook.sender import WebhookOutboxSender
```

- [ ] **Step 5: Add httpx dependency if not present**

Run: `cd event-users && uv add httpx` (likely already present — check pyproject.toml first)

- [ ] **Step 6: Verify lint passes**

Run: `cd event-users && ruff check . && ruff format --check .`

- [ ] **Step 7: Commit**

```bash
git add event-users/
git commit -m "feat(event-users): add webhook outbox poller for CRM delivery"
```

---

## Task 10: event-admin-frontend — Email Change API

**Files:**
- Create: `event-admin-frontend/src/modules/participants/emailChangeApi.ts`

- [ ] **Step 1: Create API module**

Create `event-admin-frontend/src/modules/participants/emailChangeApi.ts`:

```typescript
import { apiRequest } from '../shared/api.ts'

export type EmailChangelogEntry = {
  id: string
  old_email: string
  new_email: string
  changed_by: string
  changed_at: string
}

export type EmailChangelogResponse = {
  items: EmailChangelogEntry[]
  total: number
}

export async function requestEmailChange(userId: string, newEmail: string): Promise<void> {
  await apiRequest(`/api/users/id/${userId}/change-email`, {
    method: 'POST',
    body: { new_email: newEmail },
  })
}

export async function getEmailChangelog(
  userId: string,
  limit = 20,
  offset = 0,
): Promise<EmailChangelogResponse> {
  return apiRequest<EmailChangelogResponse>(
    `/api/users/id/${userId}/email-changelog?limit=${limit}&offset=${offset}`,
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add event-admin-frontend/src/modules/participants/emailChangeApi.ts
git commit -m "feat(frontend): add email change and changelog API functions"
```

---

## Task 11: event-admin-frontend — Email Change Modal

**Files:**
- Create: `event-admin-frontend/src/modules/participants/EmailChangeModal.tsx`

- [ ] **Step 1: Create the modal component**

Create `event-admin-frontend/src/modules/participants/EmailChangeModal.tsx`:

```tsx
import { type FormEvent, useEffect, useState } from 'react'
import { ApiError } from '../shared/api.ts'
import { formatDateTime } from '../shared/format.ts'
import { useTimeZone } from '../settings/TimeZoneContext.tsx'
import {
  getEmailChangelog,
  requestEmailChange,
  type EmailChangelogEntry,
} from './emailChangeApi.ts'

type Props = {
  userId: string
  currentEmail: string
  onClose: () => void
  onSuccess: () => void
}

export function EmailChangeModal({ userId, currentEmail, onClose, onSuccess }: Props) {
  const { timeZone } = useTimeZone()
  const [newEmail, setNewEmail] = useState('')
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [success, setSuccess] = useState(false)
  const [changelog, setChangelog] = useState<EmailChangelogEntry[]>([])
  const [changelogLoading, setChangelogLoading] = useState(true)

  useEffect(() => {
    setChangelogLoading(true)
    getEmailChangelog(userId)
      .then((data) => setChangelog(data.items))
      .catch(() => setChangelog([]))
      .finally(() => setChangelogLoading(false))
  }, [userId])

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError(null)

    const trimmed = newEmail.trim().toLowerCase()
    if (!trimmed) {
      setError('Введите email')
      return
    }
    if (trimmed === currentEmail.toLowerCase()) {
      setError('Новый email совпадает с текущим')
      return
    }

    setSubmitting(true)
    try {
      await requestEmailChange(userId, trimmed)
      setSuccess(true)
      setTimeout(() => {
        onSuccess()
        onClose()
      }, 1500)
    } catch (err) {
      if (err instanceof ApiError) {
        setError(err.message)
      } else {
        setError('Не удалось отправить запрос')
      }
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="modal-overlay" onClick={onClose}>
      <div className="modal-content" onClick={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Изменить email клиента</h2>
          <button type="button" className="modal-close" onClick={onClose}>
            &times;
          </button>
        </div>

        <div className="modal-body">
          <div className="field" style={{ marginBottom: '1rem' }}>
            <span className="field-label">Текущий email</span>
            <span className="field-value">{currentEmail}</span>
          </div>

          {success ? (
            <p className="success-text">Запрос на изменение отправлен</p>
          ) : (
            <form onSubmit={handleSubmit}>
              <label className="field">
                <span>Новый email</span>
                <input
                  type="email"
                  placeholder="new@example.com"
                  value={newEmail}
                  onChange={(e) => setNewEmail(e.target.value)}
                  disabled={submitting}
                  autoFocus
                  required
                />
              </label>

              {error && <p className="error-text">{error}</p>}

              <div className="inline-actions" style={{ marginTop: '1rem' }}>
                <button type="submit" disabled={submitting}>
                  {submitting ? 'Отправка…' : 'Сохранить'}
                </button>
                <button type="button" className="secondary" onClick={onClose} disabled={submitting}>
                  Отмена
                </button>
              </div>
            </form>
          )}

          <div style={{ marginTop: '2rem' }}>
            <h3>История изменений</h3>
            {changelogLoading && <p>Загрузка…</p>}
            {!changelogLoading && changelog.length === 0 && (
              <p className="muted">Нет истории изменений</p>
            )}
            {!changelogLoading && changelog.length > 0 && (
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Дата</th>
                      <th>Старый email</th>
                      <th>Новый email</th>
                      <th>Кто изменил</th>
                    </tr>
                  </thead>
                  <tbody>
                    {changelog.map((entry) => (
                      <tr key={entry.id}>
                        <td>{formatDateTime(entry.changed_at, timeZone)}</td>
                        <td>{entry.old_email}</td>
                        <td>{entry.new_email}</td>
                        <td>{entry.changed_by}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Commit**

```bash
git add event-admin-frontend/src/modules/participants/EmailChangeModal.tsx
git commit -m "feat(frontend): add EmailChangeModal component with changelog display"
```

---

## Task 12: event-admin-frontend — Integrate Modal into Pages

**Files:**
- Modify: `event-admin-frontend/src/modules/participants/ParticipantsPage.tsx`
- Modify: `event-admin-frontend/src/modules/bookings/BookingDetailsPage.tsx`

- [ ] **Step 1: Add edit button to ParticipantsPage**

In `event-admin-frontend/src/modules/participants/ParticipantsPage.tsx`, add imports:

```typescript
import { EmailChangeModal } from './EmailChangeModal.tsx'
```

Add state for modal:

```typescript
const [editingUser, setEditingUser] = useState<{ id: string; email: string } | null>(null)
```

In the table, add a 7th column header `<th></th>` after `<th>Дата регистрации</th>`.

In each row, add a cell after the registration date cell:

```tsx
<td>
  {item.role === 'client' && (
    <button
      type="button"
      className="secondary small"
      onClick={() => setEditingUser({ id: item.id, email: item.email })}
    >
      Изменить email
    </button>
  )}
</td>
```

After the closing `</section>` tag (but inside the return), add the modal:

```tsx
{editingUser && (
  <EmailChangeModal
    userId={editingUser.id}
    currentEmail={editingUser.email}
    onClose={() => setEditingUser(null)}
    onSuccess={() => void load(emailInput || undefined, selectedRole || undefined, currentPage)}
  />
)}
```

- [ ] **Step 2: Add edit button to BookingDetailsPage**

In `event-admin-frontend/src/modules/bookings/BookingDetailsPage.tsx`, add imports:

```typescript
import { EmailChangeModal } from '../participants/EmailChangeModal.tsx'
```

Add state:

```typescript
const [editingClientEmail, setEditingClientEmail] = useState<{ id: string; email: string } | null>(null)
```

Find the section where the client participant is displayed in the "Current participants" card. Locate the `UserInfo` component for the client user_id. Add an edit button next to it — the exact location depends on the JSX structure. Look for a section that renders `booking.client_user_id` or similar, and add:

```tsx
<button
  type="button"
  className="secondary small"
  onClick={() => {
    if (booking.client_user_id) {
      // Need to get the email — load from user data
      setEditingClientEmail({
        id: booking.client_user_id,
        email: '', // Will be populated from UserInfo
      })
    }
  }}
  style={{ marginLeft: '0.5rem' }}
>
  Изменить email
</button>
```

Since getting the current email requires the user data loaded by `UserInfo`, consider creating a small wrapper or using the `userBatchLoader` cache. The simplest approach: use `getCachedUser` from `userBatchLoader.ts`:

```typescript
import { getCachedUser } from '../shared/userBatchLoader.ts'
```

Then in the click handler:

```typescript
onClick={() => {
  if (!booking.client_user_id) return
  const cached = getCachedUser(booking.client_user_id)
  setEditingClientEmail({
    id: booking.client_user_id,
    email: cached?.email ?? '',
  })
}}
```

Add the modal at the end of the component:

```tsx
{editingClientEmail && (
  <EmailChangeModal
    userId={editingClientEmail.id}
    currentEmail={editingClientEmail.email}
    onClose={() => setEditingClientEmail(null)}
    onSuccess={() => {
      setEditingClientEmail(null)
      // Reload booking details
      void loadBookingDetails()
    }}
  />
)}
```

Note: Wrap the existing `useEffect` data loading into a named function `loadBookingDetails()` so it can be re-called on success.

- [ ] **Step 3: Add modal CSS**

Add basic modal styles. Find the main CSS file (likely `src/index.css` or `src/App.css`) and add:

```css
.modal-overlay {
  position: fixed;
  inset: 0;
  background: rgba(0, 0, 0, 0.5);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 100;
}

.modal-content {
  background: var(--color-surface, #fff);
  border-radius: 8px;
  padding: 1.5rem;
  max-width: 600px;
  width: 90%;
  max-height: 80vh;
  overflow-y: auto;
}

.modal-header {
  display: flex;
  justify-content: space-between;
  align-items: center;
  margin-bottom: 1rem;
}

.modal-close {
  background: none;
  border: none;
  font-size: 1.5rem;
  cursor: pointer;
  padding: 0.25rem;
}

button.small {
  padding: 0.25rem 0.5rem;
  font-size: 0.8rem;
}
```

- [ ] **Step 4: Verify build passes**

Run: `cd event-admin-frontend && npm run build`

- [ ] **Step 5: Commit**

```bash
git add event-admin-frontend/
git commit -m "feat(frontend): integrate EmailChangeModal into ParticipantsPage and BookingDetailsPage"
```

---

## Task 13: Documentation Updates

**Files:**
- Modify: `docs/architecture/MESSAGE_CONTRACTS.md`
- Modify: `event-receiver/QUEUES_DIGEST.md`
- Modify: `event-users/docs/DATA_MODEL.md`
- Modify: `event-users/docs/API_CONTRACTS.md`
- Modify: `event-admin/docs/API_CONTRACTS.md`

- [ ] **Step 1: Update MESSAGE_CONTRACTS.md**

Add a new section for the `user.email.change_requested` event contract:
- Source: `admin`
- Type: `user.email.change_requested`
- Queue: `events.user.email`
- Payload schema
- Producer: event-admin (via event-receiver)
- Consumer: event-users

- [ ] **Step 2: Update event-receiver QUEUES_DIGEST.md**

Add the new routing rule:
- Queue: `events.user.email`
- Source pattern: `admin`
- Type pattern: `user.email.*`

- [ ] **Step 3: Update event-users docs**

In `DATA_MODEL.md`: Add `email_source` column, `user_email_changelog` table, `webhook_outbox` table descriptions.

In `API_CONTRACTS.md`: Add `GET /api/users/{user_id}/email-changelog` endpoint.

- [ ] **Step 4: Update event-admin docs**

In `API_CONTRACTS.md`: Add `POST /api/users/{user_id}/change-email` and `GET /api/users/{user_id}/email-changelog` endpoints.

- [ ] **Step 5: Commit**

```bash
git add docs/ event-receiver/ event-users/docs/ event-admin/docs/
git commit -m "docs: update contracts, data model, and API docs for email editing feature"
```
