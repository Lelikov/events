# Coding Standards

Patterns documented here are derived from reading the actual codebase, not aspirational guidelines. Where services diverge from the common pattern, divergences are noted explicitly.

---

## Python Service Patterns

### Dependency Injection: Dishka

All Python services use [Dishka](https://dishka.readthedocs.io/) for DI with a single `AppProvider` class in `ioc.py`.

**Standard scopes (event-admin, event-users, event-saver):**

| Scope | What lives here | Lifecycle |
|-------|----------------|-----------|
| `Scope.APP` | Settings, AsyncEngine, async_sessionmaker, domain services, projections, broker, exchange | Singleton for app lifetime |
| `Scope.REQUEST` | AsyncSession, SqlExecutor, DB adapters, controllers | Created per HTTP request or per consumed message |

**Reference:**
- `event-admin/event_admin/ioc.py:29-104`
- `event-users/event_users/ioc.py:25-80`
- `event-saver/event_saver/ioc.py:54-170`

**Route injection pattern:**

```python
from dishka.integrations.fastapi import DishkaRoute

router = APIRouter(route_class=DishkaRoute)

@router.get("/bookings")
async def list_bookings(controller: FromDishka[IBookingsController], ...):
    ...
```

**Source:** `event-admin/event_admin/routes.py`, `event-users/event_users/routes.py`

---

### Protocol-Based Interfaces

Every service defines Python `Protocol` classes in an `interfaces/` directory. Concrete implementations live in `adapters/` or `infrastructure/`.

**Pattern:**

```python
# interfaces/sql.py
from typing import Protocol
from sqlalchemy.engine import RowMapping

class ISqlExecutor(Protocol):
    async def fetch_one(self, query: str, values: dict) -> RowMapping | None: ...
    async def fetch_all(self, query: str, values: dict) -> list[RowMapping]: ...
    async def execute(self, query: str, values: dict) -> None: ...
```

**Common interfaces across services:**
- `ISqlExecutor` / `ISqlExecutorFactory` -- DB access abstraction
- `IBookingsDBAdapter`, `IUsersDBAdapter` -- domain-specific DB adapters
- `IBookingsController`, `IUsersController` -- orchestration layer
- `IEventRouter`, `ICloudEventPublisher`, `ITopologyManager` -- messaging (event-receiver, event-saver)
- `INotificationChannel`, `IUsersClient` -- external integrations (event-notifier)

**Source:** `event-saver/event_saver/interfaces/sql.py:10-24`, `event-admin/event_admin/interfaces/`, `event-users/event_users/interfaces/`

---

### Frozen Dataclass DTOs

Inter-layer communication uses frozen dataclasses. Pydantic models are reserved for HTTP request/response schemas.

**Pattern:**

```python
from dataclasses import dataclass

@dataclass(frozen=True, slots=True)
class ParsedEvent:
    raw: RawEventData
    payload_hash: str

    @property
    def event_id(self) -> str:
        return self.raw.event_id
```

**Conventions observed:**
- `frozen=True` for immutability (though mutable containers like `list` inside are NOT prevented -- audit M-18)
- `slots=True` for memory efficiency (used in event-saver domain models)
- Properties delegate to nested fields rather than duplicating data
- DTOs live in `dto/` directories (event-admin, event-users) or `domain/models/` (event-saver)

**Source:** `event-saver/event_saver/domain/models/event.py:8-73`, `event-admin/event_admin/dto/bookings.py`

---

### SqlExecutor Pattern

All services using SQLAlchemy share a `SqlExecutor` class that wraps `AsyncSession` with raw `text()` SQL. ORM models exist only for Alembic migration autogeneration.

**Standard API (event-saver, event-users):**

```python
class SqlExecutor:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def fetch_one(self, query: str, values: dict) -> RowMapping | None: ...
    async def fetch_all(self, query: str, values: dict) -> list[RowMapping]: ...
    async def execute(self, query: str, values: dict) -> None: ...
    async def execute_in_transaction(self, statements: list[tuple[str, dict]]) -> None: ...
```

**Read-only variant (event-admin):**

```python
class SqlExecutor:
    async def fetch_one(self, query: str, values: dict) -> RowMapping | None: ...
    async def fetch_all(self, query: str, values: dict) -> list[RowMapping]: ...
    # No execute() or execute_in_transaction() exposed
```

**Session lifecycle (event-users pattern -- commit/rollback in DI):**

```python
@provide(scope=Scope.REQUEST)
async def provide_session(self, sessionmaker: ...) -> AsyncGenerator[AsyncSession]:
    async with sessionmaker() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
```

**Source:** `event-saver/event_saver/adapters/sql.py:1-30`, `event-users/event_users/adapters/sql.py:1-35`, `event-admin/event_admin/adapters/sql.py:1-22`

---

### Pydantic Settings Configuration

All services use `pydantic-settings` with `BaseSettings` for configuration.

**Pattern:**

```python
from pydantic import Field, PostgresDsn
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
    postgres_dsn: PostgresDsn = Field(strict=True)
    rabbit_url: AmqpDsn = "amqp://guest:guest@localhost:5672/"
```

**Conventions:**
- No env prefix (fields map directly to env var names, case-insensitive)
- `Field(strict=True)` for required fields with no default
- Pydantic URL types (`PostgresDsn`, `AmqpDsn`, `AnyHttpUrl`) for connection strings
- `.env` file loaded automatically

**Source:** `event-notifier/event_notifier/config.py:1-33`, `event-saver/event_saver/config.py:1-6`

---

### Structlog Logging

All Python services use `structlog` configured in a `logger.py` module, called during app lifespan.

**Pattern:**

```python
import structlog

def setup_logger(log_level: int, console_render: bool) -> None:
    shared_processors = [
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.contextvars.merge_contextvars,
        structlog.processors.CallsiteParameterAdder({...}),
        structlog.stdlib.ExtraAdder(),
    ]
    # Console renderer in debug mode, JSON in production
```

**Usage in modules:**

```python
import structlog
logger = structlog.get_logger(__name__)

logger.info("event_ingested", event_type=event.type, booking_id=event.booking_id)
```

**Source:** `event-saver/event_saver/logger.py:1-40`

---

### Error Handling

**event-receiver pattern (domain errors mapped to HTTP):**

```python
# errors.py
class IngestError(Exception): ...
class BadRequestError(IngestError): ...
class UnauthorizedError(IngestError): ...

# routes.py -- centralized mapping
try:
    result = await controller.ingest(...)
except BadRequestError as e:
    raise HTTPException(status_code=400, detail=str(e))
except UnauthorizedError as e:
    raise HTTPException(status_code=401, detail=str(e))
```

**event-saver pattern (log and continue for projections):**

```python
# projection_executor.py
try:
    await projection.handle(event)
except Exception:
    logger.exception("projection_failed", projection=projection.__class__.__name__)
    # Does NOT re-raise -- other projections still execute
```

**Source:** `event-receiver/event_receiver/errors.py`, `event-saver/event_saver/application/services/projection_executor.py:60-66`

---

### Response Schema Pattern

HTTP response models use Pydantic with `from_dto()` classmethods to convert from frozen dataclass DTOs.

**Pattern:**

```python
class BookingResponse(BaseModel):
    booking_uid: str
    status: str | None
    ...

    @classmethod
    def from_dto(cls, dto: BookingDto) -> "BookingResponse":
        return cls(
            booking_uid=dto.booking_uid,
            status=dto.status,
            ...
        )
```

**Source:** `event-admin/event_admin/schemas/bookings.py`, `event-users/event_users/schemas/users.py`

---

### Test Patterns

**Current state:** No service has meaningful test coverage (audit L-1).

**event-notifier (only service with test infrastructure):**
- `pytest-asyncio` with `asyncio_mode = "auto"`
- `pytest-mock` for dependency mocking
- `respx` for HTTP client mocking
- Infrastructure tests mock HTTP via `respx`; use case tests use `unittest.mock.AsyncMock`
- No real external connections in tests

**Source:** `event-notifier/CLAUDE.md` (test commands section)

---

## TypeScript / React Patterns (event-admin-frontend)

### Module Structure

All application code lives under `src/modules/`:

| Module | Responsibility |
|--------|---------------|
| `auth/` | Login page, AuthContext, JWT localStorage persistence |
| `bookings/` | Dashboard, list, detail pages + API calls |
| `participants/` | User list page + event-users API calls |
| `settings/` | TimeZone context (localStorage persistence) |
| `app/` | AdminLayout (sidebar + page shell) |
| `shared/` | apiRequest wrapper, routing, formatDateTime, reusable components |

**Source:** `event-admin-frontend/src/modules/`

### apiRequest Wrapper

Central HTTP function handles auth, JSON parsing, and error throwing:

```typescript
export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
    const { method = 'GET', body, auth = true, baseUrl = API_BASE_URL } = options
    // Attaches Bearer token from localStorage
    // Throws ApiError with .status and .details for non-2xx
}
```

Two variants:
- Default: uses `VITE_API_BASE_URL` for event-admin
- `participantsApi.ts`: uses `VITE_USERS_API_BASE_URL` for event-users (same `apiRequest` with `baseUrl` override)

**Source:** `event-admin-frontend/src/modules/shared/api.ts:24-64`

### Manual Routing (No Router Library)

```typescript
export type AppRoute =
  | { name: 'login' }
  | { name: 'dashboard' }
  | { name: 'bookings' }
  | { name: 'booking-details'; bookingUid: string }
  | { name: 'participants' }
  | { name: 'not-found' }

export function parseRoute(pathname: string): AppRoute { ... }
export function navigateTo(path: string, options?: { replace?: boolean }): void { ... }
```

`App.tsx` listens to `popstate` + custom `app:navigate` events to re-render based on `parseRoute(location.pathname)`.

**Source:** `event-admin-frontend/src/modules/shared/routing.ts:1-38`

### JWT Auth Flow

1. Login: `POST /auth/login` with `{email, password, totp_code}` -> receives `{access_token, role}`
2. Storage: JWT in `localStorage["event_admin_jwt"]`, role in `localStorage["event_admin_role"]`
3. Request: `apiRequest` attaches `Authorization: Bearer <token>` automatically
4. Logout: Clears localStorage, calls `POST /auth/logout` (no-op server-side)

**Source:** `event-admin-frontend/src/modules/auth/AuthContext.tsx`, `event-admin-frontend/src/modules/auth/storage.ts:1-26`

---

## Known Divergences

### event-notifier: All DI at Scope.APP

Unlike other services that split between APP and REQUEST scopes, event-notifier wires **everything** at `Scope.APP` including the DB pool, repository, use case, channels, and outbox sender. This is because it has no HTTP request lifecycle -- it is a background consumer with a polling outbox.

It also uses **asyncpg directly** (connection pool) instead of SQLAlchemy, since it does not need ORM models or Alembic migrations (schema is bootstrapped via raw SQL in `db/schema.py`).

**Source:** `event-notifier/event_notifier/ioc.py:26-100` (every `@provide` uses `scope=Scope.APP`)

### event-saver: Own EventType Enum Divergent from event-schemas

event-saver defines `EventType` at `event_saver/event_types.py:20-37` with completely different string values than `event-schemas/event_schemas/types.py:8-43`:

| Enum member | event-schemas value | event-saver value |
|-------------|--------------------|--------------------|
| `BOOKING_CREATED` | `"booking.created"` | `"booking.events.v1.booking.created.create"` |
| `GETSTREAM_MESSAGE_NEW` | `"getstream.message.new"` | `"getstream.events.v1.message.new.create"` |
| `BOOKING_RESCHEDULED` | `"booking.rescheduled"` | `"booking.rescheduled"` (only matching value!) |

event-saver does NOT import event-schemas at runtime. The shared library is shared only with event-receiver.

**Source:** `event-saver/event_saver/event_types.py:20-37`, `event-schemas/docs/SERVICE_OVERVIEW.md:20-23`

### event-admin-frontend: JWT Forwarding (Not Static Token)

The service CLAUDE.md and `participants/` module docs reference a "static bearer token" (`VITE_USERS_API_TOKEN`) for event-users API calls. However, the actual implementation in `participantsApi.ts` uses the same `apiRequest` wrapper with `baseUrl` override, which forwards the admin user's JWT (from `getJwtToken()` in localStorage) to event-users.

This means event-users and event-admin must share the same JWT secret for token verification to succeed.

**Source:** `event-admin-frontend/src/modules/shared/api.ts:34-38` (attaches token from `getJwtToken()`), `event-admin-frontend/src/modules/participants/participantsApi.ts` (uses `apiRequest` with `baseUrl` override)

### event-admin: SqlExecutor Read-Only Enforcement is Interface-Level Only

event-admin's `SqlExecutor` exposes only `fetch_one`/`fetch_all` (no `execute` method). However, the DB connection uses the same superuser credentials (`postgres`/`postgres`) as event-saver. No database-level read-only role exists.

**Source:** `event-admin/event_admin/adapters/sql.py:1-22` (no write methods), `docs/audit/DEPENDENCY_GRAPH.md:150-155` (same credentials)

### event-receiver: IngestController at Scope.REQUEST Despite Statelessness

All fields injected into `IngestController` are APP-scoped singletons. The controller itself has no per-request state. Allocating a new instance per request adds unnecessary overhead.

**Source:** `event-receiver/event_receiver/ioc.py:133-145` (audit L-6)

### Session Commit Strategy Varies

| Service | Commit strategy | Source |
|---------|----------------|--------|
| event-saver | `execute_in_transaction()` for batched writes; individual `execute()` does NOT commit | `event-saver/event_saver/adapters/sql.py:18-29` |
| event-users | Session commits in DI provider (yield session, then commit) | `event-users/event_users/ioc.py:57-68` |
| event-admin | No commits (read-only) | `event-admin/event_admin/adapters/sql.py:1-22` |
| event-notifier | asyncpg explicit transactions (`async with conn.transaction()`) | `event-notifier/event_notifier/db/repository.py:48-50` |

### Clean Architecture Violations in event-saver

The application layer imports concrete infrastructure classes:
- `IngestEventUseCase` imports `BookingRepository` and `EventRepository` directly (`event_saver/application/use_cases/ingest_event.py:11`)
- `ProjectionExecutor` imports `BaseProjection` from infrastructure (`event_saver/application/services/projection_executor.py:8`)

These should be wired through protocols in `interfaces/` but are not.

**Source:** `docs/audit/AUDIT_REPORT.md:317-327` (audit H-9)
