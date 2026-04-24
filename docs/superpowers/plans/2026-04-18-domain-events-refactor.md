# event-notifier: Domain Events Refactor

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Перевести event-notifier с командного паттерна (`notification.send_requested`) на доменные события (`booking.*`) с data-driven routing rules, transactional outbox, идемпотентностью и retry с exponential backoff.

**Architecture:** event-notifier подписывается на новую очередь `events.notifications`, куда event-receiver роутит `booking.*` события напрямую. Сервис применяет `routing_rules` из PostgreSQL, резолвит контакты через event-users по UUID (`GET /users/{user_id}`), атомарно пишет записи в `notification_outbox` вместе с `processed_events`. Фоновый `OutboxSender` читает outbox и доставляет через channel adapters (email/telegram/push) с exponential backoff до 5 попыток.

**Tech Stack:** Python 3.14, asyncpg (PostgreSQL, без ORM), FastStream[rabbit], httpx, structlog, pydantic-settings, pytest + pytest-asyncio + respx

**Важно:** В этом проекте **нет git-репозитория**. Все шаги `git commit` пропускать.

---

## Контекст и ключевые решения

### Что меняется
| Компонент | Было | Стало |
|---|---|---|
| Потребляемая очередь | `events.notification.commands` | `events.notifications` |
| Тип событий | `notification.send_requested` (команда с явным списком получателей) | `booking.created`, `booking.cancelled` и др. |
| Кто решает кого уведомить | Booking service (явно передаёт список) | event-notifier (через routing_rules в БД) |
| Идентификатор получателя | email адрес | **UUID пользователя** (`volunteer_id`, `client_id`) |
| Резолв контактов | `GET /api/users?email=&role=` | **`GET /users/{user_id}`** |
| Гарантии доставки | Нет (sync, fire-and-forget) | Transactional outbox + retry |
| Идемпотентность | Нет | processed_events таблица |

### Что НЕ меняется
- Channel adapters: `EmailChannel`, `TelegramChannel`, `PushChannel` — без изменений
- `ResultEventPublisher` — удаляется (delivery статус теперь в собственной БД)
- Интерфейс `INotificationChannel` — без изменений

### Ключевое: UUID вместо email
Booking events передают **UUID пользователей** (`volunteer_id`, `client_id`), не email-адреса.
`routing_rules.recipient_field` указывает на UUID-поле в `data` события.
`UsersClient.get_contacts_by_id(user_id, role)` вызывает `GET /users/{user_id}` на event-users
и возвращает список `ChannelContact` с email, telegram_chat_id и т.д.

### Mapping: доменные события → trigger_event для channel adapters
Channel adapters используют строки вида `"BOOKING_CREATED"`. Для mapping будет добавлен словарь в `event_types.py`:
```python
DOMAIN_EVENT_TO_TRIGGER = {
    "booking.created":      "BOOKING_CREATED",
    "booking.cancelled":    "BOOKING_CANCELLED",
    "booking.rescheduled":  "BOOKING_RESCHEDULED",
    "booking.reassigned":   "BOOKING_REASSIGNED",
    "booking.reminder_sent": "BOOKING_REMINDER",
}
```

### Routing rules: как извлекаем получателей
`routing_rules` хранит путь до UUID в dot-notation от корня `data`:
- `"booking.created"` + `recipient_field="volunteer_id"` → `data["volunteer_id"]` → UUID строка
- `"booking.created"` + `recipient_field="client_id"` → `data["client_id"]` → UUID строка

### Retry formula
`scheduled_at = NOW() + retry_count^2 * 10 секунд`:
- retry 1 → +10s, retry 2 → +40s, retry 3 → +90s, retry 4 → +160s, retry 5 → +250s → failed

---

## File Map

```
event-schemas/event_schemas/
  booking.py                          — MODIFY: добавить volunteer_id, client_id в все payload-ы

event-receiver/event_receiver/
  config.py                           — MODIFY: добавить routing rules booking.* → events.notifications

event-notifier/
  pyproject.toml                      — MODIFY: добавить asyncpg>=0.30.0
  .env.example                        — MODIFY: добавить DATABASE_URL
  event_notifier/
    config.py                         — MODIFY: добавить database_url, notifications_queue
    event_types.py                    — MODIFY: добавить DOMAIN_EVENT_TO_TRIGGER dict
    db/
      __init__.py
      schema.py                       — CREATE: SQL schema + create_tables()
      repository.py                   — CREATE: asyncpg queries (routing_rules, processed_events, outbox)
    domain/
      models/
        notification.py               — MODIFY: добавить DomainEvent, RoutingRule, OutboxRecord dataclasses;
                                        ChannelContact.user_email → user_id
      services/
        routing.py                    — CREATE: extract_recipients() — pure function
    application/
      use_cases/
        dispatch_notification.py      — DELETE (заменяется)
        process_domain_event.py       — CREATE: ProcessDomainEventUseCase
    adapters/
      consumer.py                     — REWRITE: обработка domain events
      outbox_sender.py                — CREATE: OutboxSender background task
    interfaces/
      repository.py                   — CREATE: INotificationRepository Protocol
      users_client.py                 — MODIFY: добавить get_contacts_by_id
      publisher.py                    — DELETE (ResultEventPublisher удалён)
    infrastructure/
      publisher.py                    — DELETE
      users_client.py                 — MODIFY: добавить get_contacts_by_id
    ioc.py                            — MODIFY: wire DB pool, новые провайдеры
    main.py                           — MODIFY: DB init, OutboxSender task
  tests/
    domain/
      test_routing_service.py         — CREATE (замена старого test_notification_models)
    application/
      test_process_domain_event.py    — CREATE (замена test_dispatch_notification.py)
    infrastructure/
      test_outbox_sender.py           — CREATE
      test_users_client.py            — MODIFY: добавить тест get_contacts_by_id
      # остальные инфраструктурные тесты — без изменений
```

---

## Task 1: Обогатить booking event schemas (UUID поля)

**Files:**
- Modify: `event-schemas/event_schemas/booking.py`

Добавляем `volunteer_id` и `client_id` как обязательные UUID-строки в payload-ы, которые их не имеют.
Существующие поля (`user`, `client`, `email`) сохраняем для обратной совместимости с event-receiver/event-saver.

- [ ] **Step 1: Прочитать текущий файл**

```bash
cat event-schemas/event_schemas/booking.py
```

- [ ] **Step 2: Обновить `event-schemas/event_schemas/booking.py`**

Заменить полностью на:

```python
"""Booking event payload schemas."""

from datetime import datetime

from pydantic import BaseModel, EmailStr, Field

from event_schemas.types import ClientInfo, UserInfo


class BookingCreatedPayload(BaseModel):
    """Payload for booking.created event."""

    volunteer_id: str = Field(..., description="Organizer (volunteer) UUID")
    client_id: str = Field(..., description="Client UUID")
    user: UserInfo = Field(..., description="Organizer information")
    client: ClientInfo = Field(..., description="Client information")
    start_time: datetime = Field(..., description="Booking start time (ISO 8601)")
    end_time: datetime = Field(..., description="Booking end time (ISO 8601)")

    model_config = {"json_schema_extra": {"example": {
        "volunteer_id": "550e8400-e29b-41d4-a716-446655440001",
        "client_id": "550e8400-e29b-41d4-a716-446655440002",
        "user": {"email": "organizer@example.com"},
        "client": {"email": "client@example.com"},
        "start_time": "2024-03-01T10:00:00Z",
        "end_time": "2024-03-01T11:00:00Z",
    }}}


class BookingRescheduledPayload(BaseModel):
    """Payload for booking.rescheduled event."""

    volunteer_id: str = Field(..., description="Organizer (volunteer) UUID")
    client_id: str = Field(..., description="Client UUID")
    start_time: datetime = Field(..., description="New booking start time")
    end_time: datetime = Field(..., description="New booking end time")
    previous_booking: dict[str, datetime | None] = Field(
        default_factory=dict,
        description="Previous booking details",
    )

    model_config = {"json_schema_extra": {"example": {
        "volunteer_id": "550e8400-e29b-41d4-a716-446655440001",
        "client_id": "550e8400-e29b-41d4-a716-446655440002",
        "start_time": "2024-03-02T10:00:00Z",
        "end_time": "2024-03-02T11:00:00Z",
        "previous_booking": {"start_time": "2024-03-01T10:00:00Z"},
    }}}


class BookingReassignedPayload(BaseModel):
    """Payload for booking.reassigned event."""

    volunteer_id: str = Field(..., description="New organizer (volunteer) UUID")
    client_id: str = Field(..., description="Client UUID")
    previous_organizer: dict[str, str | None] = Field(
        default_factory=dict,
        description="Previous organizer information",
    )
    user: UserInfo = Field(..., description="New organizer information")

    model_config = {"json_schema_extra": {"example": {
        "volunteer_id": "550e8400-e29b-41d4-a716-446655440003",
        "client_id": "550e8400-e29b-41d4-a716-446655440002",
        "previous_organizer": {"email": "old.organizer@example.com"},
        "user": {"email": "new.organizer@example.com"},
    }}}


class BookingCancelledPayload(BaseModel):
    """Payload for booking.cancelled event."""

    volunteer_id: str = Field(..., description="Organizer (volunteer) UUID")
    client_id: str = Field(..., description="Client UUID")
    cancellation_reason: str | None = Field(None, description="Reason for cancellation")

    model_config = {"json_schema_extra": {"example": {
        "volunteer_id": "550e8400-e29b-41d4-a716-446655440001",
        "client_id": "550e8400-e29b-41d4-a716-446655440002",
        "cancellation_reason": "Client request",
    }}}


class BookingReminderSentPayload(BaseModel):
    """Payload for booking.reminder_sent event."""

    client_id: str = Field(..., description="Client UUID")
    email: EmailStr = Field(..., description="Email address where reminder was sent")

    model_config = {"json_schema_extra": {"example": {
        "client_id": "550e8400-e29b-41d4-a716-446655440002",
        "email": "client@example.com",
    }}}
```

- [ ] **Step 3: Проверить импорт**

```bash
cd event-schemas && python -c "
from event_schemas.booking import BookingCancelledPayload
p = BookingCancelledPayload(
    volunteer_id='550e8400-e29b-41d4-a716-446655440001',
    client_id='550e8400-e29b-41d4-a716-446655440002',
    cancellation_reason='test',
)
print(p)
"
```

Ожидаемый вывод: объект модели без ошибок.

- [ ] **Step 4: Lint**

```bash
cd event-schemas && ruff check .
```

Ожидаемый вывод: No errors.

---

## Task 2: Новые routing rules в event-receiver

**Files:**
- Modify: `event-receiver/event_receiver/config.py`

Добавить новую очередь `events.notifications` и роутить в неё все `booking.*` события.

- [ ] **Step 1: Добавить routing rules в `_default_route_rules()` в `event-receiver/event_receiver/config.py`**

Добавить **в начало** функции `_default_route_rules()` (перед существующими правилами) следующие правила:

```python
RouteRule(
    destination="events.notifications",
    source_pattern="booking",
    type_pattern="booking.created",
),
RouteRule(
    destination="events.notifications",
    source_pattern="booking",
    type_pattern="booking.cancelled",
),
RouteRule(
    destination="events.notifications",
    source_pattern="booking",
    type_pattern="booking.rescheduled",
),
RouteRule(
    destination="events.notifications",
    source_pattern="booking",
    type_pattern="booking.reassigned",
),
RouteRule(
    destination="events.notifications",
    source_pattern="booking",
    type_pattern="booking.reminder_sent",
),
```

- [ ] **Step 2: Проверить, что `events.notifications` попадает в routing_destinations**

```bash
cd event-receiver && python -c "
import os
os.environ.update({
    'AUTHORIZATION_JWT_VERIFY_KEY': 'k', 'AUTHORIZATION_JWT_ISSUER': 'i',
    'AUTHORIZATION_JWT_AUDIENCE': 'a', 'EMAIL_API_KEY': 'e',
    'GETSTREAM_API_KEY': 'g', 'GETSTREAM_API_SECRET': 's',
    'GETSTREAM_USER_ID_ENCRYPTION_KEY': 'x', 'BOOKING_API_KEY': 'b',
    'EVENT_USERS_API_URL': 'http://localhost', 'EVENT_USERS_API_TOKEN': 't',
})
from event_receiver.config import Settings
s = Settings()
dests = sorted(s.routing_destinations)
assert 'events.notifications' in dests, dests
print('events.notifications присутствует:', dests)
"
```

Ожидаемый вывод: `events.notifications` в списке.

---

## Task 3: Добавить asyncpg, обновить config

**Files:**
- Modify: `event-notifier/pyproject.toml`
- Modify: `event-notifier/event_notifier/config.py`
- Modify: `event-notifier/.env.example`
- Modify: `event-notifier/event_notifier/event_types.py`

- [ ] **Step 1: Добавить asyncpg в `event-notifier/pyproject.toml`**

В секцию `dependencies` добавить:
```toml
"asyncpg>=0.30.0",
```

Установить:
```bash
cd event-notifier && uv sync
```

Ожидаемый вывод: успешная установка без ошибок.

- [ ] **Step 2: Обновить `event-notifier/event_notifier/config.py`**

```python
from pydantic import AmqpDsn, AnyHttpUrl, Field, PostgresDsn
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

    rabbit_url: AmqpDsn = "amqp://guest:guest@localhost:5672/"
    rabbit_exchange: str = "events"
    notifications_queue: str = "events.notifications"

    database_url: PostgresDsn = Field(strict=True)

    event_users_url: AnyHttpUrl = Field(strict=True)
    event_users_token: str = Field(strict=True)

    unisender_api_key: str = Field(strict=True)
    unisender_from_email: str = Field(strict=True)
    unisender_from_name: str = "Notifications"

    telegram_bot_token: str = Field(strict=True)

    fcm_project_id: str = Field(strict=True)
    fcm_service_account_json: str = Field(strict=True)
```

Примечание: `event_receiver_url` и `event_receiver_jwt` удалены (ResultEventPublisher убирается). `notification_commands_queue` заменён на `notifications_queue`.

- [ ] **Step 3: Обновить `event-notifier/.env.example`**

```bash
# RabbitMQ
RABBIT_URL=amqp://guest:guest@localhost:5672/
RABBIT_EXCHANGE=events
NOTIFICATIONS_QUEUE=events.notifications

# PostgreSQL
DATABASE_URL=postgresql://postgres:password@localhost:5432/event_notifier

# event-users
EVENT_USERS_URL=http://localhost:8001
EVENT_USERS_TOKEN=<bearer-token>

# Email (UniSender Go)
UNISENDER_API_KEY=<api-key>
UNISENDER_FROM_EMAIL=noreply@yourdomain.com
UNISENDER_FROM_NAME=Your Service

# Telegram Bot
TELEGRAM_BOT_TOKEN=<bot-token>

# FCM
FCM_PROJECT_ID=<firebase-project-id>
FCM_SERVICE_ACCOUNT_JSON=<path-to-service-account.json>

DEBUG=false
LOG_LEVEL=INFO
```

- [ ] **Step 4: Обновить `event-notifier/event_notifier/event_types.py`**

```python
"""Local event type aliases used by event-notifier."""

NOTIFICATION_EMAIL_SENT = "notification.email.message_sent"
NOTIFICATION_TELEGRAM_SENT = "notification.telegram.message_sent"
NOTIFICATION_PUSH_SENT = "notification.push.message_sent"
NOTIFIER_SOURCE = "event-notifier"

# Mapping from CloudEvent type to trigger_event string used by channel adapters
DOMAIN_EVENT_TO_TRIGGER: dict[str, str] = {
    "booking.created": "BOOKING_CREATED",
    "booking.cancelled": "BOOKING_CANCELLED",
    "booking.rescheduled": "BOOKING_RESCHEDULED",
    "booking.reassigned": "BOOKING_REASSIGNED",
    "booking.reminder_sent": "BOOKING_REMINDER",
}
```

- [ ] **Step 5: Проверить импорт**

```bash
cd event-notifier && python -c "from event_notifier.config import Settings; print('OK')"
```

Ожидаемый вывод: ошибка `ValidationError` (нет DATABASE_URL) — это норма, значит Pydantic парсит корректно.

---

## Task 4: DB schema

**Files:**
- Create: `event-notifier/event_notifier/db/__init__.py`
- Create: `event-notifier/event_notifier/db/schema.py`

- [ ] **Step 1: Создать `event-notifier/event_notifier/db/__init__.py`**

Пустой файл.

- [ ] **Step 2: Создать `event-notifier/event_notifier/db/schema.py`**

```python
"""PostgreSQL schema bootstrap for event-notifier."""

import asyncpg

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS routing_rules (
    id          SERIAL PRIMARY KEY,
    event_type  TEXT NOT NULL,
    recipient_field TEXT NOT NULL,
    recipient_role  TEXT NOT NULL,
    priority        TEXT NOT NULL DEFAULT 'normal',
    ignore_quiet_hours BOOLEAN NOT NULL DEFAULT FALSE,
    active          BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_routing_rules_unique
    ON routing_rules (event_type, recipient_field, recipient_role);

CREATE TABLE IF NOT EXISTS processed_events (
    cloud_event_id TEXT PRIMARY KEY,
    processed_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS notification_outbox (
    id                UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    idempotency_key   TEXT NOT NULL UNIQUE,
    cloud_event_id    TEXT NOT NULL,
    booking_id        TEXT NOT NULL,
    user_id           TEXT NOT NULL,
    recipient_address TEXT NOT NULL,
    recipient_role    TEXT NOT NULL,
    channel           TEXT NOT NULL,
    event_type        TEXT NOT NULL,
    template_context  JSONB NOT NULL DEFAULT '{}',
    status            TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'delivered', 'failed')),
    retry_count       INT NOT NULL DEFAULT 0,
    max_retries       INT NOT NULL DEFAULT 5,
    scheduled_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    created_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at        TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_outbox_pending
    ON notification_outbox (scheduled_at)
    WHERE status = 'pending';
"""

_SEED_SQL = """
INSERT INTO routing_rules (event_type, recipient_field, recipient_role)
VALUES
    ('booking.created',      'volunteer_id', 'volunteer'),
    ('booking.created',      'client_id',    'client'),
    ('booking.cancelled',    'volunteer_id', 'volunteer'),
    ('booking.cancelled',    'client_id',    'client'),
    ('booking.rescheduled',  'volunteer_id', 'volunteer'),
    ('booking.rescheduled',  'client_id',    'client'),
    ('booking.reassigned',   'volunteer_id', 'volunteer'),
    ('booking.reassigned',   'client_id',    'client'),
    ('booking.reminder_sent','client_id',    'client')
ON CONFLICT DO NOTHING;
"""


async def create_tables(pool: asyncpg.Pool) -> None:
    """Create all tables and seed routing rules. Idempotent."""
    async with pool.acquire() as conn:
        await conn.execute(_SCHEMA_SQL)
        await conn.execute(_SEED_SQL)
```

- [ ] **Step 3: Проверить импорт**

```bash
cd event-notifier && python -c "from event_notifier.db.schema import create_tables; print('OK')"
```

Ожидаемый вывод: `OK`

---

## Task 5: Domain models + routing service

**Files:**
- Modify: `event-notifier/event_notifier/domain/models/notification.py`
- Create: `event-notifier/event_notifier/domain/services/__init__.py`
- Create: `event-notifier/event_notifier/domain/services/routing.py`
- Test: `event-notifier/tests/domain/test_routing_service.py`

- [ ] **Step 1: Обновить `event-notifier/event_notifier/domain/models/notification.py`**

```python
"""Domain models for notification dispatch — pure dataclasses, no infrastructure deps."""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class ChannelType(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"
    PUSH = "push"


@dataclass(frozen=True, slots=True)
class DomainEvent:
    """Parsed incoming CloudEvent (domain event from booking service)."""

    event_id: str         # CloudEvent id (used for idempotency)
    event_type: str       # "booking.created" etc.
    source: str           # ce-source
    booking_id: str       # ce-booking_id attribute
    data: dict[str, Any]  # parsed JSON payload


@dataclass(frozen=True, slots=True)
class RoutingRule:
    """A single routing rule from the DB."""

    event_type: str
    recipient_field: str   # dot-notation path into DomainEvent.data → extracts UUID string
    recipient_role: str    # "volunteer" | "client"


@dataclass(frozen=True, slots=True)
class OutboxRecord:
    """A record from the notification_outbox table."""

    id: str                 # UUID as string
    cloud_event_id: str
    booking_id: str
    user_id: str            # UUID of the recipient user
    recipient_address: str  # email / telegram chat_id / FCM token
    recipient_role: str
    channel: str            # "email" | "telegram" | "push"
    event_type: str
    template_context: dict[str, Any]
    retry_count: int
    max_retries: int


@dataclass(frozen=True, slots=True)
class ChannelContact:
    """A resolved channel contact for a recipient."""

    channel: ChannelType
    contact_id: str      # email addr / telegram chat_id / FCM device token
    user_id: str         # UUID of the user (from booking event data)
    role: str            # "volunteer" | "client"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Result of a single channel delivery attempt."""

    channel: ChannelType
    success: bool
    message_id: str | None = None
    error: str | None = None
```

- [ ] **Step 2: Создать `event-notifier/event_notifier/domain/services/__init__.py`**

Пустой файл.

- [ ] **Step 3: Написать тест `tests/domain/test_routing_service.py`**

```python
from event_notifier.domain.models.notification import RoutingRule
from event_notifier.domain.services.routing import extract_field_value, apply_routing_rules


def test_extract_top_level_field():
    data = {"volunteer_id": "uuid-vol-001"}
    assert extract_field_value(data, "volunteer_id") == "uuid-vol-001"


def test_extract_nested_field():
    data = {"user": {"id": "uuid-org-001"}}
    assert extract_field_value(data, "user.id") == "uuid-org-001"


def test_extract_missing_field_returns_none():
    data = {"user": {"name": "Bob"}}
    assert extract_field_value(data, "user.id") is None


def test_extract_non_string_returns_none():
    data = {"count": 42}
    assert extract_field_value(data, "count") is None


def test_apply_routing_rules_booking_created():
    rules = [
        RoutingRule(event_type="booking.created", recipient_field="volunteer_id", recipient_role="volunteer"),
        RoutingRule(event_type="booking.created", recipient_field="client_id", recipient_role="client"),
    ]
    data = {"volunteer_id": "uuid-vol-001", "client_id": "uuid-cli-001"}
    recipients = apply_routing_rules(event_type="booking.created", event_data=data, routing_rules=rules)
    assert len(recipients) == 2
    assert ("uuid-vol-001", "volunteer") in recipients
    assert ("uuid-cli-001", "client") in recipients


def test_apply_routing_rules_skips_missing_fields():
    rules = [
        RoutingRule(event_type="booking.cancelled", recipient_field="volunteer_id", recipient_role="volunteer"),
        RoutingRule(event_type="booking.cancelled", recipient_field="client_id", recipient_role="client"),
    ]
    # client_id отсутствует — должен быть пропущен
    data = {"volunteer_id": "uuid-vol-001", "cancellation_reason": "test"}
    recipients = apply_routing_rules(event_type="booking.cancelled", event_data=data, routing_rules=rules)
    assert recipients == [("uuid-vol-001", "volunteer")]
```

- [ ] **Step 4: Запустить — убедиться что падают**

```bash
cd event-notifier && python -m pytest tests/domain/test_routing_service.py -v
```

Ожидаемый результат: `ModuleNotFoundError`

- [ ] **Step 5: Создать `event-notifier/event_notifier/domain/services/routing.py`**

```python
"""Pure routing functions: extract recipients from domain event using routing rules."""

from event_notifier.domain.models.notification import RoutingRule


def extract_field_value(data: dict, field_path: str) -> str | None:
    """Extract a string value from a nested dict using dot-notation path.

    Example: extract_field_value({"volunteer_id": "uuid-001"}, "volunteer_id") == "uuid-001"
    Example: extract_field_value({"user": {"id": "uuid-001"}}, "user.id") == "uuid-001"
    Returns None if the path doesn't exist or the value is not a string.
    """
    parts = field_path.split(".")
    current: object = data
    for part in parts:
        if not isinstance(current, dict):
            return None
        current = current.get(part)
    return current if isinstance(current, str) else None


def apply_routing_rules(
    *,
    event_type: str,
    event_data: dict,
    routing_rules: list[RoutingRule],
) -> list[tuple[str, str]]:
    """Return list of (user_id, role) pairs for the given event type.

    Extracts UUID values from event_data using routing_rules.recipient_field.
    Only includes rules matching event_type where the field resolves to a non-empty string.
    """
    recipients: list[tuple[str, str]] = []
    for rule in routing_rules:
        if rule.event_type != event_type:
            continue
        user_id = extract_field_value(event_data, rule.recipient_field)
        if user_id:
            recipients.append((user_id, rule.recipient_role))
    return recipients
```

- [ ] **Step 6: Запустить тесты — убедиться что проходят**

```bash
cd event-notifier && python -m pytest tests/domain/test_routing_service.py -v
```

Ожидаемый результат: 6 tests PASSED.

---

## Task 6: DB Repository + Interface + UsersClient

**Files:**
- Create: `event-notifier/event_notifier/interfaces/repository.py`
- Create: `event-notifier/event_notifier/db/repository.py`
- Modify: `event-notifier/event_notifier/interfaces/users_client.py`
- Modify: `event-notifier/event_notifier/infrastructure/users_client.py`
- Modify: `event-notifier/tests/infrastructure/test_users_client.py`

Репозиторий инкапсулирует все SQL-запросы. `UsersClient` получает новый метод `get_contacts_by_id` для резолва контактов по UUID.

- [ ] **Step 1: Создать `event-notifier/event_notifier/interfaces/repository.py`**

```python
"""Protocol interface for notification repository."""

from typing import Any, Protocol

from event_notifier.domain.models.notification import OutboxRecord, RoutingRule


class INotificationRepository(Protocol):
    async def get_routing_rules(self, event_type: str) -> list[RoutingRule]: ...

    async def is_processed(self, cloud_event_id: str) -> bool: ...

    async def write_outbox_atomically(
        self,
        cloud_event_id: str,
        records: list[dict[str, Any]],
    ) -> None:
        """Write outbox records + mark event as processed in one transaction."""
        ...

    async def fetch_pending_outbox(self, batch_size: int = 10) -> list[OutboxRecord]: ...

    async def mark_delivered(self, record_id: str) -> None: ...

    async def mark_retry(self, record_id: str, retry_count: int, delay_seconds: int) -> None: ...

    async def mark_failed(self, record_id: str) -> None: ...
```

- [ ] **Step 2: Создать `event-notifier/event_notifier/db/repository.py`**

```python
"""asyncpg-based implementation of INotificationRepository."""

import json
from typing import Any

import asyncpg
import structlog

from event_notifier.domain.models.notification import OutboxRecord, RoutingRule

logger = structlog.get_logger(__name__)


class NotificationRepository:
    def __init__(self, pool: asyncpg.Pool) -> None:
        self._pool = pool

    async def get_routing_rules(self, event_type: str) -> list[RoutingRule]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                "SELECT event_type, recipient_field, recipient_role "
                "FROM routing_rules WHERE event_type = $1 AND active = TRUE",
                event_type,
            )
        return [
            RoutingRule(
                event_type=row["event_type"],
                recipient_field=row["recipient_field"],
                recipient_role=row["recipient_role"],
            )
            for row in rows
        ]

    async def is_processed(self, cloud_event_id: str) -> bool:
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT 1 FROM processed_events WHERE cloud_event_id = $1",
                cloud_event_id,
            )
        return row is not None

    async def write_outbox_atomically(
        self,
        cloud_event_id: str,
        records: list[dict[str, Any]],
    ) -> None:
        """Insert outbox records and mark event as processed in a single transaction."""
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "INSERT INTO processed_events (cloud_event_id) VALUES ($1) ON CONFLICT DO NOTHING",
                    cloud_event_id,
                )
                for rec in records:
                    await conn.execute(
                        """
                        INSERT INTO notification_outbox
                            (idempotency_key, cloud_event_id, booking_id, user_id,
                             recipient_address, recipient_role, channel, event_type, template_context)
                        VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9)
                        ON CONFLICT (idempotency_key) DO NOTHING
                        """,
                        rec["idempotency_key"],
                        rec["cloud_event_id"],
                        rec["booking_id"],
                        rec["user_id"],
                        rec["recipient_address"],
                        rec["recipient_role"],
                        rec["channel"],
                        rec["event_type"],
                        json.dumps(rec["template_context"]),
                    )
        logger.debug("Outbox written atomically", cloud_event_id=cloud_event_id, count=len(records))

    async def fetch_pending_outbox(self, batch_size: int = 10) -> list[OutboxRecord]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                """
                SELECT id::text, cloud_event_id, booking_id, user_id,
                       recipient_address, recipient_role, channel, event_type,
                       template_context, retry_count, max_retries
                FROM notification_outbox
                WHERE status = 'pending' AND scheduled_at <= NOW()
                ORDER BY scheduled_at
                LIMIT $1
                FOR UPDATE SKIP LOCKED
                """,
                batch_size,
            )
        return [
            OutboxRecord(
                id=row["id"],
                cloud_event_id=row["cloud_event_id"],
                booking_id=row["booking_id"],
                user_id=row["user_id"],
                recipient_address=row["recipient_address"],
                recipient_role=row["recipient_role"],
                channel=row["channel"],
                event_type=row["event_type"],
                template_context=dict(row["template_context"]) if row["template_context"] else {},
                retry_count=row["retry_count"],
                max_retries=row["max_retries"],
            )
            for row in rows
        ]

    async def mark_delivered(self, record_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE notification_outbox SET status='delivered', updated_at=NOW() WHERE id=$1::uuid",
                record_id,
            )

    async def mark_retry(self, record_id: str, retry_count: int, delay_seconds: int) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                """
                UPDATE notification_outbox
                SET retry_count=$2,
                    scheduled_at = NOW() + ($3 || ' seconds')::interval,
                    updated_at = NOW()
                WHERE id=$1::uuid
                """,
                record_id,
                retry_count,
                str(delay_seconds),
            )

    async def mark_failed(self, record_id: str) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(
                "UPDATE notification_outbox SET status='failed', updated_at=NOW() WHERE id=$1::uuid",
                record_id,
            )
```

- [ ] **Step 3: Проверить импорт репозитория**

```bash
cd event-notifier && python -c "from event_notifier.db.repository import NotificationRepository; print('OK')"
```

Ожидаемый вывод: `OK`

- [ ] **Step 4: Обновить `event-notifier/event_notifier/interfaces/users_client.py`**

```python
from typing import Protocol

from event_notifier.domain.models.notification import ChannelContact


class IUsersClient(Protocol):
    async def get_contacts_by_email(self, *, email: str, role: str) -> list[ChannelContact]: ...

    async def get_contacts_by_id(self, *, user_id: str, role: str) -> list[ChannelContact]: ...
```

- [ ] **Step 5: Обновить `event-notifier/event_notifier/infrastructure/users_client.py`**

```python
"""HTTP client for event-users service."""

import structlog
from httpx import AsyncClient

from event_notifier.domain.models.notification import ChannelContact, ChannelType

logger = structlog.get_logger(__name__)

_CHANNEL_MAP = {
    "telegram": ChannelType.TELEGRAM,
    "push": ChannelType.PUSH,
}


class UsersClient:
    def __init__(self, *, http_client: AsyncClient, api_token: str) -> None:
        self._client = http_client
        self._headers = {"Authorization": f"Bearer {api_token}"}

    async def get_contacts_by_email(self, *, email: str, role: str) -> list[ChannelContact]:
        """Resolve all notification contacts for a recipient by email.

        Always includes the email channel. Adds telegram/push if found in user_contacts.
        Falls back to email-only on any error.
        """
        contacts: list[ChannelContact] = [
            ChannelContact(
                channel=ChannelType.EMAIL,
                contact_id=email,
                user_id=email,  # legacy: use email as user_id when no UUID available
                role=role,
            )
        ]

        try:
            response = await self._client.get(
                "/api/users",
                params={"email": email, "role": role, "limit": 1, "offset": 0},
                headers=self._headers,
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            logger.warning("Failed to fetch user contacts, email-only fallback", email=email)
            return contacts

        items = data.get("items", [])
        if not items:
            logger.debug("User not found in event-users, email-only", email=email)
            return contacts

        for raw_contact in items[0].get("contacts", []):
            channel_str = raw_contact.get("channel", "")
            channel = _CHANNEL_MAP.get(channel_str)
            if channel is None:
                continue
            contacts.append(
                ChannelContact(
                    channel=channel,
                    contact_id=raw_contact["contact_id"],
                    user_id=email,  # legacy: use email as user_id when no UUID available
                    role=role,
                )
            )

        logger.debug("Resolved contacts by email", email=email, channel_count=len(contacts))
        return contacts

    async def get_contacts_by_id(self, *, user_id: str, role: str) -> list[ChannelContact]:
        """Resolve all notification contacts for a recipient by UUID.

        Calls GET /users/{user_id} on event-users service.
        Returns email + telegram/push contacts if available.
        Returns empty list if user not found (caller decides how to handle).
        """
        try:
            response = await self._client.get(
                f"/users/{user_id}",
                headers=self._headers,
            )
            response.raise_for_status()
            data = response.json()
        except Exception:
            logger.warning("Failed to fetch user profile by id", user_id=user_id)
            return []

        contacts: list[ChannelContact] = []

        email = data.get("email")
        if email and isinstance(email, str):
            contacts.append(
                ChannelContact(
                    channel=ChannelType.EMAIL,
                    contact_id=email,
                    user_id=user_id,
                    role=role,
                )
            )

        telegram_chat_id = data.get("telegram_chat_id")
        if telegram_chat_id and isinstance(telegram_chat_id, str):
            contacts.append(
                ChannelContact(
                    channel=ChannelType.TELEGRAM,
                    contact_id=telegram_chat_id,
                    user_id=user_id,
                    role=role,
                )
            )

        logger.debug("Resolved contacts by id", user_id=user_id, channel_count=len(contacts))
        return contacts
```

- [ ] **Step 6: Добавить тест в `tests/infrastructure/test_users_client.py`**

Добавить **в конец** файла `event-notifier/tests/infrastructure/test_users_client.py`:

```python

@pytest.mark.asyncio
async def test_get_contacts_by_id_returns_email_and_telegram(http_client):
    user_id = "550e8400-e29b-41d4-a716-446655440001"
    with respx.mock:
        respx.get(f"http://users-service/users/{user_id}").mock(
            return_value=Response(
                200,
                json={
                    "id": user_id,
                    "role": "volunteer",
                    "first_name": "Ivan",
                    "last_name": "Petrov",
                    "email": "ivan@example.com",
                    "telegram_chat_id": "987654321",
                },
            )
        )

        client = UsersClient(http_client=http_client, api_token="token")
        contacts = await client.get_contacts_by_id(user_id=user_id, role="volunteer")

    email_contacts = [c for c in contacts if c.channel == ChannelType.EMAIL]
    assert len(email_contacts) == 1
    assert email_contacts[0].contact_id == "ivan@example.com"
    assert email_contacts[0].user_id == user_id

    tg_contacts = [c for c in contacts if c.channel == ChannelType.TELEGRAM]
    assert len(tg_contacts) == 1
    assert tg_contacts[0].contact_id == "987654321"


@pytest.mark.asyncio
async def test_get_contacts_by_id_user_not_found_returns_empty(http_client):
    user_id = "unknown-uuid"
    with respx.mock:
        respx.get(f"http://users-service/users/{user_id}").mock(
            return_value=Response(404, json={"detail": "Not found"})
        )

        client = UsersClient(http_client=http_client, api_token="token")
        contacts = await client.get_contacts_by_id(user_id=user_id, role="client")

    assert contacts == []
```

- [ ] **Step 7: Запустить тесты users_client**

```bash
cd event-notifier && python -m pytest tests/infrastructure/test_users_client.py -v
```

Ожидаемый результат: 4 tests PASSED (2 старых + 2 новых).

---

## Task 7: ProcessDomainEventUseCase

**Files:**
- Create: `event-notifier/event_notifier/application/use_cases/process_domain_event.py`
- Delete: `event-notifier/event_notifier/application/use_cases/dispatch_notification.py`
- Test: `event-notifier/tests/application/test_process_domain_event.py`
- Delete: `event-notifier/tests/application/test_dispatch_notification.py`

Use case: получает `DomainEvent`, применяет routing_rules, резолвит контакты через UsersClient по UUID, пишет записи в outbox.

- [ ] **Step 1: Написать тест `tests/application/test_process_domain_event.py`**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from event_notifier.application.use_cases.process_domain_event import ProcessDomainEventUseCase
from event_notifier.domain.models.notification import (
    ChannelContact,
    ChannelType,
    DomainEvent,
    RoutingRule,
)


@pytest.fixture
def mock_repository():
    repo = MagicMock()
    repo.get_routing_rules = AsyncMock(return_value=[
        RoutingRule(event_type="booking.created", recipient_field="volunteer_id", recipient_role="volunteer"),
        RoutingRule(event_type="booking.created", recipient_field="client_id", recipient_role="client"),
    ])
    repo.is_processed = AsyncMock(return_value=False)
    repo.write_outbox_atomically = AsyncMock()
    return repo


@pytest.fixture
def mock_users_client():
    client = MagicMock()
    client.get_contacts_by_id = AsyncMock(side_effect=lambda *, user_id, role: [
        ChannelContact(channel=ChannelType.EMAIL, contact_id=f"{user_id}@example.com", user_id=user_id, role=role),
        ChannelContact(channel=ChannelType.TELEGRAM, contact_id="chat-123", user_id=user_id, role=role),
    ])
    return client


@pytest.fixture
def event():
    return DomainEvent(
        event_id="evt-001",
        event_type="booking.created",
        source="booking",
        booking_id="booking-abc",
        data={"volunteer_id": "uuid-vol-001", "client_id": "uuid-cli-001"},
    )


@pytest.mark.asyncio
async def test_writes_outbox_records_for_all_contacts(mock_repository, mock_users_client, event):
    use_case = ProcessDomainEventUseCase(repository=mock_repository, users_client=mock_users_client)
    await use_case.execute(event)

    mock_repository.write_outbox_atomically.assert_awaited_once()
    _, call_kwargs = mock_repository.write_outbox_atomically.call_args
    records = call_kwargs["records"]
    # 2 recipients * 2 channels each = 4 outbox records
    assert len(records) == 4
    channels = {r["channel"] for r in records}
    assert "email" in channels
    assert "telegram" in channels


@pytest.mark.asyncio
async def test_skips_already_processed_events(mock_repository, mock_users_client, event):
    mock_repository.is_processed = AsyncMock(return_value=True)
    use_case = ProcessDomainEventUseCase(repository=mock_repository, users_client=mock_users_client)
    await use_case.execute(event)

    mock_repository.write_outbox_atomically.assert_not_awaited()
    mock_users_client.get_contacts_by_id.assert_not_awaited()


@pytest.mark.asyncio
async def test_skips_event_with_no_routing_rules(mock_repository, mock_users_client, event):
    mock_repository.get_routing_rules = AsyncMock(return_value=[])
    use_case = ProcessDomainEventUseCase(repository=mock_repository, users_client=mock_users_client)
    await use_case.execute(event)

    mock_repository.write_outbox_atomically.assert_not_awaited()


@pytest.mark.asyncio
async def test_idempotency_key_format(mock_repository, mock_users_client, event):
    use_case = ProcessDomainEventUseCase(repository=mock_repository, users_client=mock_users_client)
    await use_case.execute(event)

    _, call_kwargs = mock_repository.write_outbox_atomically.call_args
    records = call_kwargs["records"]
    keys = [r["idempotency_key"] for r in records]
    # format: "{event_id}:{user_id}:{channel}"
    assert any("evt-001:uuid-vol-001:email" == k for k in keys)
    assert any("evt-001:uuid-vol-001:telegram" == k for k in keys)
```

- [ ] **Step 2: Запустить — убедиться что падают**

```bash
cd event-notifier && python -m pytest tests/application/test_process_domain_event.py -v
```

Ожидаемый результат: `ModuleNotFoundError`

- [ ] **Step 3: Создать `event-notifier/event_notifier/application/use_cases/process_domain_event.py`**

```python
"""Use case: process a domain event and write notification records to outbox."""

from typing import Any

import structlog

from event_notifier.domain.models.notification import DomainEvent
from event_notifier.domain.services.routing import apply_routing_rules
from event_notifier.interfaces.repository import INotificationRepository
from event_notifier.interfaces.users_client import IUsersClient

logger = structlog.get_logger(__name__)


class ProcessDomainEventUseCase:
    def __init__(
        self,
        *,
        repository: INotificationRepository,
        users_client: IUsersClient,
    ) -> None:
        self._repository = repository
        self._users_client = users_client

    async def execute(self, event: DomainEvent) -> None:
        # Idempotency: skip if already processed
        if await self._repository.is_processed(event.event_id):
            logger.info("Event already processed, skipping", event_id=event.event_id)
            return

        # Get routing rules from DB
        routing_rules = await self._repository.get_routing_rules(event.event_type)
        if not routing_rules:
            logger.warning(
                "No routing rules for event type, skipping",
                event_type=event.event_type,
                event_id=event.event_id,
            )
            return

        # Extract (user_id, role) pairs from event data using routing rules
        recipients = apply_routing_rules(
            event_type=event.event_type,
            event_data=event.data,
            routing_rules=routing_rules,
        )
        if not recipients:
            logger.warning(
                "No recipients resolved from event data",
                event_type=event.event_type,
                event_id=event.event_id,
            )
            return

        logger.info(
            "Processing domain event",
            event_type=event.event_type,
            event_id=event.event_id,
            booking_id=event.booking_id,
            recipient_count=len(recipients),
        )

        # Resolve channel contacts for each recipient UUID
        outbox_records: list[dict[str, Any]] = []
        for user_id, role in recipients:
            contacts = await self._users_client.get_contacts_by_id(user_id=user_id, role=role)
            if not contacts:
                logger.warning(
                    "No contacts resolved for user, skipping",
                    user_id=user_id,
                    event_id=event.event_id,
                )
                continue
            for contact in contacts:
                outbox_records.append({
                    "idempotency_key": f"{event.event_id}:{contact.user_id}:{contact.channel.value}",
                    "cloud_event_id": event.event_id,
                    "booking_id": event.booking_id,
                    "user_id": contact.user_id,
                    "recipient_address": contact.contact_id,
                    "recipient_role": contact.role,
                    "channel": contact.channel.value,
                    "event_type": event.event_type,
                    "template_context": event.data,
                })

        if not outbox_records:
            logger.warning("No outbox records to write", event_id=event.event_id)
            return

        # Write all outbox records + mark event as processed in one transaction
        await self._repository.write_outbox_atomically(
            cloud_event_id=event.event_id,
            records=outbox_records,
        )
        logger.info(
            "Outbox written",
            event_id=event.event_id,
            records_count=len(outbox_records),
        )
```

- [ ] **Step 4: Запустить тесты**

```bash
cd event-notifier && python -m pytest tests/application/test_process_domain_event.py -v
```

Ожидаемый результат: 4 tests PASSED.

- [ ] **Step 5: Удалить старые файлы**

```bash
rm event-notifier/event_notifier/application/use_cases/dispatch_notification.py
rm event-notifier/tests/application/test_dispatch_notification.py
```

---

## Task 8: Новый consumer (domain events)

**Files:**
- Rewrite: `event-notifier/event_notifier/adapters/consumer.py`

Потребляет `booking.*` события из очереди `events.notifications`.

- [ ] **Step 1: Перезаписать `event-notifier/event_notifier/adapters/consumer.py`**

```python
"""RabbitMQ consumer for events.notifications queue (domain events)."""

import structlog
from cloudevents.v1.http import from_http
from faststream.rabbit import RabbitBroker, RabbitExchange, RabbitQueue

from event_notifier.application.use_cases.process_domain_event import ProcessDomainEventUseCase
from event_notifier.domain.models.notification import DomainEvent
from event_notifier.event_types import DOMAIN_EVENT_TO_TRIGGER

logger = structlog.get_logger(__name__)


class NotificationConsumer:
    def __init__(
        self,
        *,
        broker: RabbitBroker,
        exchange: RabbitExchange,
        queue_name: str,
        use_case: ProcessDomainEventUseCase,
    ) -> None:
        self._broker = broker
        self._exchange = exchange
        self._queue_name = queue_name
        self._use_case = use_case
        self._started = False

    async def start(self) -> None:
        if self._started:
            return

        queue = RabbitQueue(
            name=self._queue_name,
            durable=True,
            routing_key=self._queue_name,
            declare=False,
        )

        @self._broker.subscriber(queue=queue, exchange=self._exchange)
        async def handle(body: bytes, headers: dict) -> None:
            await self._handle(body=body, headers=headers)

        await self._broker.start()
        self._started = True
        logger.info("Notification consumer started", queue=self._queue_name)

    async def stop(self) -> None:
        if not self._started:
            return
        await self._broker.close()
        self._started = False
        logger.info("Notification consumer stopped", queue=self._queue_name)

    async def _handle(self, *, body: bytes, headers: dict) -> None:
        try:
            ce = from_http(headers=headers, data=body)
        except Exception:
            logger.exception("Failed to parse CloudEvent")
            raise

        event_type = ce["type"]
        if event_type not in DOMAIN_EVENT_TO_TRIGGER:
            logger.warning("Unknown event type, skipping", event_type=event_type)
            return

        booking_id = ce.get("booking_id") or (ce.data or {}).get("booking_id", "")
        data = ce.data or {}

        event = DomainEvent(
            event_id=ce["id"],
            event_type=event_type,
            source=ce["source"],
            booking_id=booking_id,
            data=data,
        )

        logger.info(
            "Received domain event",
            event_type=event_type,
            event_id=ce["id"],
            booking_id=booking_id,
        )

        await self._use_case.execute(event)
```

- [ ] **Step 2: Проверить импорт**

```bash
cd event-notifier && python -c "from event_notifier.adapters.consumer import NotificationConsumer; print('OK')"
```

Ожидаемый вывод: `OK`

---

## Task 9: OutboxSender

**Files:**
- Create: `event-notifier/event_notifier/adapters/outbox_sender.py`
- Test: `event-notifier/tests/infrastructure/test_outbox_sender.py`

Фоновый цикл, который читает `pending` записи из outbox и доставляет через channel adapters.

- [ ] **Step 1: Написать тест `tests/infrastructure/test_outbox_sender.py`**

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from event_notifier.adapters.outbox_sender import OutboxSender
from event_notifier.domain.models.notification import ChannelContact, ChannelType, DeliveryResult, OutboxRecord


def make_record(**kwargs) -> OutboxRecord:
    defaults = {
        "id": "record-uuid-1",
        "cloud_event_id": "evt-001",
        "booking_id": "booking-abc",
        "user_id": "uuid-user-001",
        "recipient_address": "user@example.com",
        "recipient_role": "volunteer",
        "channel": "email",
        "event_type": "booking.created",
        "template_context": {"volunteer_id": "uuid-user-001"},
        "retry_count": 0,
        "max_retries": 5,
    }
    defaults.update(kwargs)
    return OutboxRecord(**defaults)


@pytest.fixture
def mock_repository():
    repo = MagicMock()
    repo.fetch_pending_outbox = AsyncMock(return_value=[])
    repo.mark_delivered = AsyncMock()
    repo.mark_retry = AsyncMock()
    repo.mark_failed = AsyncMock()
    return repo


@pytest.fixture
def mock_email_channel():
    ch = MagicMock()
    ch.send = AsyncMock(return_value=DeliveryResult(channel=ChannelType.EMAIL, success=True, message_id="job-1"))
    return ch


@pytest.mark.asyncio
async def test_successful_send_marks_delivered(mock_repository, mock_email_channel):
    record = make_record()
    mock_repository.fetch_pending_outbox = AsyncMock(return_value=[record])
    sender = OutboxSender(
        repository=mock_repository,
        channels={ChannelType.EMAIL: mock_email_channel},
    )
    await sender.run_once()

    mock_email_channel.send.assert_awaited_once()
    mock_repository.mark_delivered.assert_awaited_once_with("record-uuid-1")
    mock_repository.mark_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_failed_send_marks_retry(mock_repository, mock_email_channel):
    record = make_record(retry_count=0)
    mock_repository.fetch_pending_outbox = AsyncMock(return_value=[record])
    mock_email_channel.send = AsyncMock(
        return_value=DeliveryResult(channel=ChannelType.EMAIL, success=False, error="timeout")
    )
    sender = OutboxSender(
        repository=mock_repository,
        channels={ChannelType.EMAIL: mock_email_channel},
    )
    await sender.run_once()

    mock_repository.mark_retry.assert_awaited_once()
    call_kwargs = mock_repository.mark_retry.call_args.kwargs
    assert call_kwargs["record_id"] == "record-uuid-1"
    assert call_kwargs["retry_count"] == 1
    assert call_kwargs["delay_seconds"] == 10  # retry 1: 10 * 1^2 = 10


@pytest.mark.asyncio
async def test_max_retries_exceeded_marks_failed(mock_repository, mock_email_channel):
    record = make_record(retry_count=5, max_retries=5)
    mock_repository.fetch_pending_outbox = AsyncMock(return_value=[record])
    mock_email_channel.send = AsyncMock(
        return_value=DeliveryResult(channel=ChannelType.EMAIL, success=False, error="timeout")
    )
    sender = OutboxSender(
        repository=mock_repository,
        channels={ChannelType.EMAIL: mock_email_channel},
    )
    await sender.run_once()

    mock_repository.mark_failed.assert_awaited_once_with("record-uuid-1")
    mock_repository.mark_retry.assert_not_awaited()


@pytest.mark.asyncio
async def test_unknown_channel_marks_failed(mock_repository):
    record = make_record(channel="push")  # no push adapter registered
    mock_repository.fetch_pending_outbox = AsyncMock(return_value=[record])
    sender = OutboxSender(
        repository=mock_repository,
        channels={ChannelType.EMAIL: MagicMock()},  # only email registered
    )
    await sender.run_once()

    mock_repository.mark_failed.assert_awaited_once_with("record-uuid-1")
```

- [ ] **Step 2: Запустить — убедиться что падают**

```bash
cd event-notifier && python -m pytest tests/infrastructure/test_outbox_sender.py -v
```

Ожидаемый результат: `ModuleNotFoundError`

- [ ] **Step 3: Создать `event-notifier/event_notifier/adapters/outbox_sender.py`**

```python
"""Background outbox sender: polls notification_outbox and delivers via channel adapters."""

import asyncio

import structlog

from event_notifier.domain.models.notification import ChannelContact, ChannelType, OutboxRecord
from event_notifier.event_types import DOMAIN_EVENT_TO_TRIGGER
from event_notifier.interfaces.channels import INotificationChannel
from event_notifier.interfaces.repository import INotificationRepository

logger = structlog.get_logger(__name__)


def _retry_delay_seconds(retry_count: int) -> int:
    """Exponential backoff: 10s, 40s, 90s, 160s, 250s for retries 1–5."""
    return 10 * retry_count ** 2


class OutboxSender:
    def __init__(
        self,
        *,
        repository: INotificationRepository,
        channels: dict[ChannelType, INotificationChannel],
        batch_size: int = 10,
        poll_interval: float = 1.0,
    ) -> None:
        self._repository = repository
        self._channels = channels
        self._batch_size = batch_size
        self._poll_interval = poll_interval
        self._running = False

    async def run_once(self) -> None:
        """Process one batch of pending outbox records. Used in tests and the main loop."""
        records = await self._repository.fetch_pending_outbox(self._batch_size)
        for record in records:
            await self._process_record(record)

    async def start(self) -> None:
        self._running = True
        logger.info("OutboxSender started", poll_interval=self._poll_interval)
        while self._running:
            try:
                await self.run_once()
            except Exception:
                logger.exception("OutboxSender loop error")
            await asyncio.sleep(self._poll_interval)

    def stop(self) -> None:
        self._running = False
        logger.info("OutboxSender stopped")

    async def _process_record(self, record: OutboxRecord) -> None:
        channel_type = _parse_channel(record.channel)
        if channel_type is None:
            logger.error("Unknown channel in outbox record, marking failed", channel=record.channel, id=record.id)
            await self._repository.mark_failed(record.id)
            return

        channel = self._channels.get(channel_type)
        if channel is None:
            logger.error("No adapter for channel, marking failed", channel=record.channel, id=record.id)
            await self._repository.mark_failed(record.id)
            return

        trigger_event = DOMAIN_EVENT_TO_TRIGGER.get(record.event_type, record.event_type)
        contact = ChannelContact(
            channel=channel_type,
            contact_id=record.recipient_address,
            user_id=record.user_id,
            role=record.recipient_role,
        )

        try:
            result = await channel.send(
                contact=contact,
                trigger_event=trigger_event,
                template_data=record.template_context,
            )
        except Exception as exc:
            logger.exception("Channel send raised unexpectedly", id=record.id, channel=record.channel)
            result_success = False
            result_error = str(exc)
        else:
            result_success = result.success
            result_error = result.error

        if result_success:
            await self._repository.mark_delivered(record.id)
            logger.info("Outbox record delivered", id=record.id, channel=record.channel)
        else:
            next_retry = record.retry_count + 1
            if next_retry > record.max_retries:
                await self._repository.mark_failed(record.id)
                logger.warning(
                    "Outbox record failed after max retries",
                    id=record.id,
                    channel=record.channel,
                    error=result_error,
                )
            else:
                delay = _retry_delay_seconds(next_retry)
                await self._repository.mark_retry(record.id, next_retry, delay)
                logger.warning(
                    "Outbox record send failed, scheduling retry",
                    id=record.id,
                    channel=record.channel,
                    retry=next_retry,
                    delay_seconds=delay,
                    error=result_error,
                )


def _parse_channel(channel_str: str) -> ChannelType | None:
    try:
        return ChannelType(channel_str)
    except ValueError:
        return None
```

- [ ] **Step 4: Запустить тесты**

```bash
cd event-notifier && python -m pytest tests/infrastructure/test_outbox_sender.py -v
```

Ожидаемый результат: 4 tests PASSED.

---

## Task 10: Удалить ResultEventPublisher + обновить interfaces

**Files:**
- Delete: `event-notifier/event_notifier/infrastructure/publisher.py`
- Delete: `event-notifier/event_notifier/interfaces/publisher.py`
- Modify: `event-notifier/event_notifier/interfaces/__init__.py` (если он есть — убрать publisher import)

- [ ] **Step 1: Удалить файлы**

```bash
rm event-notifier/event_notifier/infrastructure/publisher.py
rm event-notifier/event_notifier/interfaces/publisher.py
```

- [ ] **Step 2: Проверить что ничего не сломалось**

```bash
cd event-notifier && python -m pytest tests/ -v
```

Если есть ошибки импорта publisher — найти и убрать:
```bash
cd event-notifier && grep -r "publisher" event_notifier/ --include="*.py" -l
```

---

## Task 11: Обновить IoC + main

**Files:**
- Modify: `event-notifier/event_notifier/ioc.py`
- Modify: `event-notifier/event_notifier/main.py`

- [ ] **Step 1: Перезаписать `event-notifier/event_notifier/ioc.py`**

```python
"""Dishka DI container for event-notifier."""

from collections.abc import AsyncGenerator

import asyncpg
import structlog
from dishka import Provider, Scope, provide
from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange
from httpx import AsyncClient

from event_notifier.adapters.consumer import NotificationConsumer
from event_notifier.adapters.outbox_sender import OutboxSender
from event_notifier.application.use_cases.process_domain_event import ProcessDomainEventUseCase
from event_notifier.config import Settings
from event_notifier.db.repository import NotificationRepository
from event_notifier.domain.models.notification import ChannelType
from event_notifier.infrastructure.channels.email import EmailChannel
from event_notifier.infrastructure.channels.telegram import TelegramChannel
from event_notifier.infrastructure.users_client import UsersClient
from event_notifier.interfaces.channels import INotificationChannel

logger = structlog.get_logger(__name__)


class AppProvider(Provider):

    @provide(scope=Scope.APP)
    def provide_settings(self) -> Settings:
        return Settings()

    @provide(scope=Scope.APP)
    async def provide_db_pool(self, settings: Settings) -> AsyncGenerator[asyncpg.Pool, None]:
        pool = await asyncpg.create_pool(str(settings.database_url), min_size=2, max_size=10)
        yield pool
        await pool.close()

    @provide(scope=Scope.APP)
    def provide_repository(self, pool: asyncpg.Pool) -> NotificationRepository:
        return NotificationRepository(pool=pool)

    @provide(scope=Scope.APP)
    def provide_exchange(self, settings: Settings) -> RabbitExchange:
        return RabbitExchange(name=settings.rabbit_exchange, type=ExchangeType.TOPIC, durable=True)

    @provide(scope=Scope.APP)
    def provide_broker(self, settings: Settings) -> RabbitBroker:
        return RabbitBroker(str(settings.rabbit_url))

    @provide(scope=Scope.APP)
    async def provide_users_client(self, settings: Settings) -> AsyncGenerator[UsersClient, None]:
        async with AsyncClient(base_url=str(settings.event_users_url)) as client:
            yield UsersClient(http_client=client, api_token=settings.event_users_token)

    @provide(scope=Scope.APP)
    async def provide_email_channel(self, settings: Settings) -> AsyncGenerator[EmailChannel, None]:
        async with AsyncClient(base_url="https://go.unisender.ru") as client:
            yield EmailChannel(
                http_client=client,
                api_key=settings.unisender_api_key,
                from_email=settings.unisender_from_email,
                from_name=settings.unisender_from_name,
            )

    @provide(scope=Scope.APP)
    async def provide_telegram_channel(self, settings: Settings) -> AsyncGenerator[TelegramChannel, None]:
        async with AsyncClient(base_url="https://api.telegram.org") as client:
            yield TelegramChannel(http_client=client, bot_token=settings.telegram_bot_token)

    @provide(scope=Scope.APP)
    def provide_use_case(
        self,
        repository: NotificationRepository,
        users_client: UsersClient,
    ) -> ProcessDomainEventUseCase:
        return ProcessDomainEventUseCase(
            repository=repository,
            users_client=users_client,
        )

    @provide(scope=Scope.APP)
    def provide_outbox_sender(
        self,
        repository: NotificationRepository,
        email_channel: EmailChannel,
        telegram_channel: TelegramChannel,
    ) -> OutboxSender:
        channels: dict[ChannelType, INotificationChannel] = {
            ChannelType.EMAIL: email_channel,
            ChannelType.TELEGRAM: telegram_channel,
            # ChannelType.PUSH: push_channel  — включить после настройки FCM
        }
        return OutboxSender(repository=repository, channels=channels)

    @provide(scope=Scope.APP)
    def provide_consumer(
        self,
        broker: RabbitBroker,
        exchange: RabbitExchange,
        settings: Settings,
        use_case: ProcessDomainEventUseCase,
    ) -> NotificationConsumer:
        return NotificationConsumer(
            broker=broker,
            exchange=exchange,
            queue_name=settings.notifications_queue,
            use_case=use_case,
        )
```

- [ ] **Step 2: Перезаписать `event-notifier/event_notifier/main.py`**

```python
"""FastAPI application entry point for event-notifier."""

import asyncio
from contextlib import asynccontextmanager
from logging import getLevelNamesMapping
from typing import TYPE_CHECKING

import asyncpg
import structlog
from dishka import make_async_container
from fastapi import FastAPI

from event_notifier.adapters.consumer import NotificationConsumer
from event_notifier.adapters.outbox_sender import OutboxSender
from event_notifier.config import Settings
from event_notifier.db.schema import create_tables
from event_notifier.ioc import AppProvider
from event_notifier.logger import setup_logger

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> "AsyncGenerator[None]":
    container = make_async_container(AppProvider())

    settings = await container.get(Settings)
    log_level = getLevelNamesMapping().get(settings.log_level.upper(), 20)
    setup_logger(log_level=log_level, console_render=settings.debug)

    logger.info("Starting event-notifier", log_level=settings.log_level)

    # Initialize DB schema (idempotent)
    pool = await container.get(asyncpg.Pool)
    await create_tables(pool)
    logger.info("DB schema ready")

    # Start RabbitMQ consumer
    consumer = await container.get(NotificationConsumer)
    await consumer.start()

    # Start OutboxSender as background asyncio task
    outbox_sender = await container.get(OutboxSender)
    sender_task = asyncio.create_task(outbox_sender.start(), name="outbox-sender")

    logger.info("event-notifier ready")

    yield

    logger.info("Shutting down event-notifier")
    outbox_sender.stop()
    sender_task.cancel()
    try:
        await sender_task
    except asyncio.CancelledError:
        pass

    await consumer.stop()
    await container.close()


app = FastAPI(title="event-notifier", version="0.2.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
```

- [ ] **Step 3: Проверить импорт**

```bash
cd event-notifier && python -c "from event_notifier.main import app; print('imports OK')"
```

Ожидаемый вывод: `imports OK`

---

## Task 12: Финальный smoke test

- [ ] **Step 1: Запустить все тесты**

```bash
cd event-notifier && python -m pytest tests/ -v
```

Ожидаемый результат: все тесты PASSED. Новые тесты:
- `tests/domain/test_routing_service.py` — 6 тестов
- `tests/application/test_process_domain_event.py` — 4 теста
- `tests/infrastructure/test_outbox_sender.py` — 4 теста
- `tests/infrastructure/test_users_client.py` — 4 теста (2 старых + 2 новых)

Всего ожидается ~22 теста.

- [ ] **Step 2: Lint**

```bash
cd event-notifier && ruff check . && ruff format --check .
```

Ожидаемый результат: No errors.

- [ ] **Step 3: Проверить event-receiver routing**

```bash
cd event-receiver && python -c "
import os
os.environ.update({
    'AUTHORIZATION_JWT_VERIFY_KEY': 'k', 'AUTHORIZATION_JWT_ISSUER': 'i',
    'AUTHORIZATION_JWT_AUDIENCE': 'a', 'EMAIL_API_KEY': 'e',
    'GETSTREAM_API_KEY': 'g', 'GETSTREAM_API_SECRET': 's',
    'GETSTREAM_USER_ID_ENCRYPTION_KEY': 'x', 'BOOKING_API_KEY': 'b',
    'EVENT_USERS_API_URL': 'http://localhost', 'EVENT_USERS_API_TOKEN': 't',
})
from event_receiver.config import Settings
from event_receiver.routing import EventRouter
s = Settings()
router = EventRouter(config=s.routing)
cases = [
    ('booking', 'booking.created', 'events.notifications'),
    ('booking', 'booking.cancelled', 'events.notifications'),
    ('booking', 'booking.rescheduled', 'events.notifications'),
]
for source, etype, expected in cases:
    result = router.resolve(source=source, event_type=etype)
    status = 'OK' if result == expected else f'FAIL (got {result})'
    print(f'{etype}: {status}')
"
```

Ожидаемый вывод: три строки `OK`.

---

## Self-Review

**1. Spec coverage:**

| Требование из архитектурного документа | Реализовано в | Статус |
|---|---|---|
| Потребление доменных событий (booking.*) | Task 8 (consumer) | ✅ |
| UUID-based routing (`volunteer_id`, `client_id`) | Tasks 1, 4, 5, 7 | ✅ |
| Data-driven routing rules (DB) | Tasks 4, 6, 7 | ✅ |
| Идемпотентность (processed_events) | Tasks 6, 7 | ✅ |
| Transactional outbox | Tasks 4, 6, 7 | ✅ |
| Outbox sender с retry и backoff | Task 9 | ✅ |
| SELECT FOR UPDATE SKIP LOCKED | Task 6 (repository) | ✅ |
| Exponential backoff (10s, 40s, 90s...) | Task 9 | ✅ |
| Max 5 retries → failed | Task 9 | ✅ |
| UUID fields в booking event schemas | Task 1 | ✅ |
| GET /users/{user_id} для резолва контактов | Task 6 (UsersClient) | ✅ |
| New queue events.notifications | Tasks 2, 3 | ✅ |
| Notification preferences / quiet hours | — | ❌ отложено |
| WhatsApp channel | — | ❌ отложено |
| User profile cache (5 min TTL) | — | ❌ отложено |
| Template management в БД | — | ❌ отложено (следующий план) |

**2. Placeholder scan:** Нет TBD/TODO в коде. Каждый шаг содержит полный код.

**3. Type consistency:**
- `OutboxRecord.user_id` определён в Task 5, используется в Tasks 6, 7, 9 — поле совпадает.
- `ChannelContact.user_id` определён в Task 5, используется в Tasks 6, 7, 9 — поле совпадает.
- `DomainEvent` определён в Task 5, используется в Tasks 7, 8 — поля совпадают.
- `RoutingRule` определён в Task 5, используется в Tasks 6, 7 — поля совпадают.
- `ProcessDomainEventUseCase` определён в Task 7, используется в Tasks 8, 11 — сигнатура совпадает.
- `INotificationRepository` Protocol определён в Task 6, реализован в Task 6, мокируется в Tests 7 и 9 — методы совпадают.
- `IUsersClient.get_contacts_by_id` определён в Task 6, реализован в Task 6, мокируется в Test 7 — сигнатура совпадает.
