# Notification Service Contracts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Зафиксировать контракты для нового notification-сервиса: добавить event-типы, payload-схемы, обновить routing rules в event-receiver.

**Architecture:** Booking-сервис публикует команду `notification.send_requested` → event-receiver роутит в новую очередь `events.notification.commands` → event-notifier (ещё не существует) будет её консьюмить. Результаты отправки роутятся в `events.notification.delivery` (очередь уже существует, нужно обновить source_pattern).

**Tech Stack:** Python 3.14, Pydantic v2, `event-schemas` (локальный pip-пакет), `event-receiver` (FastAPI + RouteRule).

---

## Scope

Этот план покрывает **только изменения в существующих сервисах**. Сам `event-notifier` сервис — в отдельном плане `2026-04-18-event-notifier.md`.

---

## File Map

| Файл | Изменение |
|------|-----------|
| `event-schemas/event_schemas/types.py` | Добавить `NOTIFICATION_SEND_REQUESTED`, `NOTIFICATION_PUSH_SENT` в `EventType`; `PUSH` в `EventPriority` |
| `event-schemas/event_schemas/notification.py` | Добавить `NotificationRecipient`, `NotificationCommandPayload`, `PushNotificationPayload` |
| `event-schemas/event_schemas/__init__.py` | Re-export новых типов |
| `event-receiver/event_receiver/config.py` | Добавить routing rule для `events.notification.commands`; сменить `source_pattern` на `"*"` для delivery-правил |
| `event-receiver/QUEUES_DIGEST.md` | Добавить новую очередь |

---

## Task 1: Новые EventType в event-schemas

**Files:**
- Modify: `event-schemas/event_schemas/types.py`

- [ ] **Step 1: Добавить новые значения в `EventType` и `EventPriority`**

В `event-schemas/event_schemas/types.py` добавить после существующих `NOTIFICATION_*` строк:

```python
# В class EventType после NOTIFICATION_TELEGRAM_SENT:
NOTIFICATION_SEND_REQUESTED = "notification.send_requested"  # команда для event-notifier
NOTIFICATION_PUSH_SENT = "notification.push.message_sent"    # результат отправки push
```

В `EVENT_PRIORITIES` добавить:
```python
EventType.NOTIFICATION_SEND_REQUESTED: EventPriority.HIGH,
EventType.NOTIFICATION_PUSH_SENT: EventPriority.HIGH,
```

В `EVENT_SCHEMA_VERSIONS` добавить:
```python
EventType.NOTIFICATION_SEND_REQUESTED: "v1",
EventType.NOTIFICATION_PUSH_SENT: "v1",
```

- [ ] **Step 2: Проверить, что модуль импортируется без ошибок**

```bash
cd event-schemas
python -c "from event_schemas.types import EventType; print(EventType.NOTIFICATION_SEND_REQUESTED)"
```

Ожидаемый вывод: `notification.send_requested`

- [ ] **Step 3: Commit**

```bash
cd event-schemas
git add event_schemas/types.py
git commit -m "feat(schemas): add NOTIFICATION_SEND_REQUESTED and NOTIFICATION_PUSH_SENT event types"
```

---

## Task 2: Payload-схемы для notification команды и push

**Files:**
- Modify: `event-schemas/event_schemas/notification.py`

- [ ] **Step 1: Добавить `NotificationRecipient` и `NotificationCommandPayload`**

В начало `event-schemas/event_schemas/notification.py` добавить импорт:
```python
from typing import Any
```

В конец файла добавить:

```python
class NotificationRecipient(BaseModel):
    """Single recipient for a notification command."""

    email: EmailStr = Field(..., description="Recipient email address")
    role: RecipientRole = Field(..., description="Recipient role in booking context")

    model_config = {"json_schema_extra": {"example": {
        "email": "organizer@example.com",
        "role": "organizer",
    }}}


class NotificationCommandPayload(BaseModel):
    """Payload for notification.send_requested event.

    Published by the booking service to request notifications.
    event-notifier consumes this and dispatches via appropriate channels.
    """

    booking_id: str = Field(..., description="Booking UID to associate notifications with")
    trigger_event: TriggerEvent = Field(..., description="Booking lifecycle event that triggered this")
    recipients: list[NotificationRecipient] = Field(..., description="List of recipients to notify")
    template_data: dict[str, Any] = Field(
        default_factory=dict,
        description="Extra key-value data for template rendering (e.g. booking_start, organizer_name)",
    )

    model_config = {"json_schema_extra": {"example": {
        "booking_id": "booking-uuid-123",
        "trigger_event": "BOOKING_CREATED",
        "recipients": [
            {"email": "organizer@example.com", "role": "organizer"},
            {"email": "client@example.com", "role": "client"},
        ],
        "template_data": {
            "booking_start": "2026-05-01T10:00:00Z",
            "organizer_name": "Jane Smith",
        },
    }}}


class PushNotificationPayload(BaseModel):
    """Payload for notification.push.message_sent event (delivery confirmation)."""

    email: EmailStr = Field(..., description="Recipient email address")
    recipient_role: RecipientRole = Field(..., description="Recipient role")
    trigger_event: TriggerEvent = Field(..., description="Event that triggered this notification")
    device_token: str = Field(..., description="FCM/APNS device token used")
    message_id: str | None = Field(None, description="Provider message ID for tracking")

    model_config = {"json_schema_extra": {"example": {
        "email": "client@example.com",
        "recipient_role": "client",
        "trigger_event": "BOOKING_CREATED",
        "device_token": "fcm-token-abc123",
        "message_id": "projects/my-project/messages/abc123",
    }}}
```

- [ ] **Step 2: Проверить импорт новых классов**

```bash
cd event-schemas
python -c "
from event_schemas.notification import NotificationCommandPayload, NotificationRecipient, PushNotificationPayload
cmd = NotificationCommandPayload(
    booking_id='b-123',
    trigger_event='BOOKING_CREATED',
    recipients=[{'email': 'a@b.com', 'role': 'organizer'}],
)
print(cmd.model_dump())
"
```

Ожидаемый вывод: словарь с `booking_id`, `trigger_event`, `recipients`, `template_data`.

- [ ] **Step 3: Commit**

```bash
cd event-schemas
git add event_schemas/notification.py
git commit -m "feat(schemas): add NotificationCommandPayload and PushNotificationPayload schemas"
```

---

## Task 3: Re-export новых типов из `__init__.py`

**Files:**
- Modify: `event-schemas/event_schemas/__init__.py`

- [ ] **Step 1: Добавить новые классы в публичный API**

Открыть `event-schemas/event_schemas/__init__.py`. Найти блок с re-export'ами из `notification` и добавить:

```python
from event_schemas.notification import (
    EmailNotificationPayload,
    EmailRejectionNotificationPayload,
    TelegramNotificationPayload,
    NotificationRecipient,          # новое
    NotificationCommandPayload,     # новое
    PushNotificationPayload,        # новое
)
```

- [ ] **Step 2: Проверить публичный API**

```bash
cd event-schemas
python -c "
from event_schemas import NotificationCommandPayload, PushNotificationPayload, NotificationRecipient
print('OK')
"
```

Ожидаемый вывод: `OK`

- [ ] **Step 3: Lint**

```bash
cd event-schemas
ruff check .
```

Ожидаемый вывод: No errors.

- [ ] **Step 4: Commit**

```bash
cd event-schemas
git add event_schemas/__init__.py
git commit -m "feat(schemas): export new notification types from public API"
```

---

## Task 4: Routing rules в event-receiver

**Files:**
- Modify: `event-receiver/event_receiver/config.py`

- [ ] **Step 1: Добавить routing rule для команд и обновить source_pattern у delivery**

В `event-receiver/event_receiver/config.py`, в функции `_default_route_rules()`:

**Добавить** новое правило (перед правилом `events.notification.delivery`):
```python
RouteRule(
    destination="events.notification.commands",
    source_pattern="*",
    type_pattern="notification.send_requested",
),
```

**Изменить** существующие два правила для `events.notification.delivery` — сменить `source_pattern="booking"` на `source_pattern="*"`:
```python
RouteRule(
    destination="events.notification.delivery",
    source_pattern="*",           # было: "booking"
    type_pattern="notification.email.message_sent",
),
RouteRule(
    destination="events.notification.delivery",
    source_pattern="*",           # было: "booking"
    type_pattern="notification.telegram.message_sent",
),
```

**Добавить** routing rule для push-результатов (после telegram):
```python
RouteRule(
    destination="events.notification.delivery",
    source_pattern="*",
    type_pattern="notification.push.message_sent",
),
```

- [ ] **Step 2: Проверить, что все destination-ы попадают в `routing_destinations`**

```bash
cd event-receiver
python -c "
import os
os.environ.update({
    'AUTHORIZATION_JWT_VERIFY_KEY': 'k',
    'AUTHORIZATION_JWT_ISSUER': 'i',
    'AUTHORIZATION_JWT_AUDIENCE': 'a',
    'EMAIL_API_KEY': 'e',
    'GETSTREAM_API_KEY': 'g',
    'GETSTREAM_API_SECRET': 's',
    'GETSTREAM_USER_ID_ENCRYPTION_KEY': 'x',
    'BOOKING_API_KEY': 'b',
    'EVENT_USERS_API_URL': 'http://localhost',
    'EVENT_USERS_API_TOKEN': 't',
})
from event_receiver.config import Settings
s = Settings()
print(sorted(s.routing_destinations))
"
```

Ожидаемый вывод — список включает `events.notification.commands` и `events.notification.delivery`.

- [ ] **Step 3: Lint**

```bash
cd event-receiver
ruff check --fix .
```

- [ ] **Step 4: Commit**

```bash
cd event-receiver
git add event_receiver/config.py
git commit -m "feat(receiver): add events.notification.commands routing, update delivery source_pattern to wildcard"
```

---

## Task 5: Обновить QUEUES_DIGEST.md

**Files:**
- Modify: `event-receiver/QUEUES_DIGEST.md`

- [ ] **Step 1: Добавить новую очередь в сводную таблицу**

В `event-receiver/QUEUES_DIGEST.md` добавить строку в таблицу:

```markdown
| `events.notification.commands` | `*` | `notification.send_requested` | команды для event-notifier |
```

Обновить существующие строки для `events.notification.delivery` — сменить `booking` на `*` в колонке Source Pattern.

В конце файла добавить секцию:

```markdown
## events.notification.commands

Очередь команд для event-notifier сервиса:
- `notification.send_requested` — запрос на отправку уведомлений по всем каналам

**Консьюмер:** `event-notifier` (ещё не задеплоен)
**Source pattern:** `*` (любой сервис может отправить команду)
```

- [ ] **Step 2: Commit**

```bash
cd event-receiver
git add QUEUES_DIGEST.md
git commit -m "docs(receiver): document events.notification.commands queue"
```

---

## Итоговая проверка

- [ ] **Все сервисы запускаются локально без ошибок**

```bash
cd event-receiver && python -c "from event_receiver.config import Settings" && echo "receiver OK"
cd event-schemas && python -c "from event_schemas import NotificationCommandPayload" && echo "schemas OK"
```

- [ ] **Routing: команда попадает в правильную очередь**

Проверить вручную или через unit-тест EventRouter, что:
- `type=notification.send_requested, source=booking` → `events.notification.commands`
- `type=notification.email.message_sent, source=event-notifier` → `events.notification.delivery`
- `type=notification.push.message_sent, source=event-notifier` → `events.notification.delivery`

```bash
cd event-receiver
python -c "
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
    ('booking', 'notification.send_requested', 'events.notification.commands'),
    ('event-notifier', 'notification.email.message_sent', 'events.notification.delivery'),
    ('event-notifier', 'notification.push.message_sent', 'events.notification.delivery'),
]
for source, etype, expected in cases:
    result = router.resolve(source=source, event_type=etype)
    status = 'OK' if result == expected else f'FAIL (got {result})'
    print(f'{etype}: {status}')
"
```

Ожидаемый вывод: три строки `OK`.
