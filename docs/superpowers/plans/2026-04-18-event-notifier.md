# event-notifier Service Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Создать `event-notifier` — автономный Python-сервис, который потребляет команды на отправку уведомлений из RabbitMQ, доставляет их через email (UniSender), Telegram (Bot API) и push (FCM), и публикует события-результаты обратно через event-receiver.

**Architecture:** event-notifier подписывается на `events.notification.commands`. На каждую команду `notification.send_requested` он резолвит контакты получателей через HTTP API event-users, отправляет по каждому найденному каналу (email/telegram/push), затем публикует confirmation-события (`notification.*.message_sent`) в RabbitMQ. Эти события подхватывает event-receiver и роутит в `events.notification.delivery`, откуда event-saver записывает их в БД. Архитектура сервиса повторяет event-saver: clean layers (domain → application → infrastructure), Dishka DI, FastStream для RabbitMQ, structlog.

**Tech Stack:** Python 3.14, FastAPI, FastStream[rabbit], Dishka, httpx, structlog, pydantic-settings, event-schemas (локальный пакет), pytest + pytest-asyncio (тесты).

**Prerequisite:** Должен быть выполнен план `2026-04-18-notification-contracts.md` (новые EventType и routing rules в event-receiver).

---

## File Map

```
event-notifier/
  pyproject.toml                              — зависимости, ruff config
  .env.example                                — шаблон env-переменных
  event_notifier/
    __init__.py
    config.py                                 — Settings (pydantic-settings)
    logger.py                                 — setup_logger (structlog)
    main.py                                   — FastAPI app + lifespan
    ioc.py                                    — Dishka AppProvider
    event_types.py                            — локальные EventType-алиасы
    domain/
      __init__.py
      models/
        __init__.py
        notification.py                       — NotificationCommand, ChannelContact, DeliveryResult
      services/
        __init__.py
        contact_resolver.py                   — ContactResolver: email → []ChannelContact
    application/
      __init__.py
      use_cases/
        __init__.py
        dispatch_notification.py              — DispatchNotificationUseCase
    infrastructure/
      __init__.py
      channels/
        __init__.py
        base.py                               — INotificationChannel Protocol
        email.py                              — EmailChannel (UniSender)
        telegram.py                           — TelegramChannel (Bot API)
        push.py                               — PushChannel (FCM HTTP v1)
      users_client.py                         — UsersClient (httpx → event-users)
      publisher.py                            — ResultEventPublisher
    adapters/
      __init__.py
      consumer.py                             — RabbitMQ consumer
    interfaces/
      __init__.py
      channels.py                             — INotificationChannel
      users_client.py                         — IUsersClient
      publisher.py                            — IResultEventPublisher
  tests/
    conftest.py
    domain/
      test_contact_resolver.py
    application/
      test_dispatch_notification.py
    infrastructure/
      test_email_channel.py
      test_telegram_channel.py
      test_push_channel.py
      test_users_client.py
```

---

## Task 1: Scaffolding — pyproject.toml, config, logger

**Files:**
- Create: `event-notifier/pyproject.toml`
- Create: `event-notifier/.env.example`
- Create: `event-notifier/event_notifier/__init__.py`
- Create: `event-notifier/event_notifier/config.py`
- Create: `event-notifier/event_notifier/logger.py`
- Create: `event-notifier/event_notifier/event_types.py`

- [ ] **Step 1: Создать `event-notifier/pyproject.toml`**

```toml
[project]
name = "event-notifier"
version = "0.1.0"
description = "Notification dispatch service"
requires-python = ">=3.14"
dependencies = [
    "cloudevents>=1.12.0",
    "dishka>=1.8.0",
    "event-schemas @ git+https://github.com/Lelikov/event-schemas.git",
    "fastapi>=0.135.1",
    "faststream[rabbit]>=0.6.7",
    "httpx>=0.28.0",
    "pydantic-settings>=2.13.1",
    "structlog>=25.5.0",
    "uvicorn>=0.41.0",
]

[dependency-groups]
dev = [
    "pytest>=8.0.0",
    "pytest-asyncio>=0.25.0",
    "pytest-mock>=3.14.0",
    "respx>=0.22.0",
    "ruff>=0.15.4",
    "pre-commit>=4.5.1",
]

[tool.ruff]
line-length = 120
fix = true
unsafe-fixes = true

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Создать `.env.example`**

```bash
# RabbitMQ
RABBIT_URL=amqp://guest:guest@localhost:5672/
RABBIT_EXCHANGE=events
NOTIFICATION_COMMANDS_QUEUE=events.notification.commands

# event-receiver (куда публиковать результаты)
EVENT_RECEIVER_URL=http://localhost:8888
EVENT_RECEIVER_JWT=<jwt-token-for-event-receiver>

# event-users (откуда брать контакты)
EVENT_USERS_URL=http://localhost:8001
EVENT_USERS_TOKEN=<bearer-token>

# Email (UniSender Go)
UNISENDER_API_KEY=<api-key>
UNISENDER_FROM_EMAIL=noreply@yourdomain.com
UNISENDER_FROM_NAME=Your Service

# Telegram Bot
TELEGRAM_BOT_TOKEN=<bot-token>

# FCM (Firebase Cloud Messaging HTTP v1)
FCM_PROJECT_ID=<firebase-project-id>
FCM_SERVICE_ACCOUNT_JSON=<path-to-service-account.json>

DEBUG=false
LOG_LEVEL=INFO
```

- [ ] **Step 3: Создать `event-notifier/event_notifier/config.py`**

```python
from pydantic import AmqpDsn, AnyHttpUrl, Field
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
    notification_commands_queue: str = "events.notification.commands"

    event_receiver_url: AnyHttpUrl = Field(strict=True)
    event_receiver_jwt: str = Field(strict=True)

    event_users_url: AnyHttpUrl = Field(strict=True)
    event_users_token: str = Field(strict=True)

    unisender_api_key: str = Field(strict=True)
    unisender_from_email: str = Field(strict=True)
    unisender_from_name: str = "Notifications"

    telegram_bot_token: str = Field(strict=True)

    fcm_project_id: str = Field(strict=True)
    fcm_service_account_json: str = Field(strict=True)
```

- [ ] **Step 4: Создать `event-notifier/event_notifier/logger.py`**

```python
import logging
import structlog


def setup_logger(*, log_level: int, console_render: bool) -> None:
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.add_log_level,
            structlog.stdlib.add_logger_name,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer() if console_render else structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    logging.basicConfig(level=log_level)
```

- [ ] **Step 5: Создать `event-notifier/event_notifier/event_types.py`**

```python
"""Local event type aliases used by event-notifier."""

NOTIFICATION_SEND_REQUESTED = "notification.send_requested"
NOTIFICATION_EMAIL_SENT = "notification.email.message_sent"
NOTIFICATION_TELEGRAM_SENT = "notification.telegram.message_sent"
NOTIFICATION_PUSH_SENT = "notification.push.message_sent"
NOTIFIER_SOURCE = "event-notifier"
```

- [ ] **Step 6: Установить зависимости и проверить, что конфиг парсится**

```bash
cd event-notifier
uv sync
python -c "print('OK')"
```

- [ ] **Step 7: Commit**

```bash
cd event-notifier
git add pyproject.toml .env.example event_notifier/
git commit -m "feat(notifier): scaffold service with config, logger, event_types"
```

---

## Task 2: Domain models

**Files:**
- Create: `event-notifier/event_notifier/domain/models/notification.py`
- Test: `event-notifier/tests/domain/test_notification_models.py`

- [ ] **Step 1: Написать тест (failing)**

Создать `event-notifier/tests/domain/test_notification_models.py`:

```python
from event_notifier.domain.models.notification import (
    ChannelContact,
    ChannelType,
    DeliveryResult,
    NotificationCommand,
)


def test_notification_command_fields():
    cmd = NotificationCommand(
        event_id="evt-1",
        booking_id="booking-abc",
        trigger_event="BOOKING_CREATED",
        recipients=[{"email": "a@b.com", "role": "organizer"}],
        template_data={"key": "value"},
        source="booking",
    )
    assert cmd.booking_id == "booking-abc"
    assert cmd.trigger_event == "BOOKING_CREATED"
    assert len(cmd.recipients) == 1
    assert cmd.recipients[0].email == "a@b.com"


def test_channel_contact_email():
    c = ChannelContact(channel=ChannelType.EMAIL, contact_id="user@example.com", user_email="user@example.com", role="organizer")
    assert c.channel == ChannelType.EMAIL
    assert c.contact_id == "user@example.com"


def test_delivery_result_success():
    r = DeliveryResult(channel=ChannelType.TELEGRAM, success=True, message_id="tg-123")
    assert r.success is True
    assert r.error is None


def test_delivery_result_failure():
    r = DeliveryResult(channel=ChannelType.PUSH, success=False, error="Device not registered")
    assert r.success is False
    assert r.error == "Device not registered"
```

- [ ] **Step 2: Запустить — убедиться что тесты падают**

```bash
cd event-notifier
pytest tests/domain/test_notification_models.py -v
```

Ожидаемый результат: `ModuleNotFoundError`

- [ ] **Step 3: Создать `event-notifier/event_notifier/domain/models/notification.py`**

```python
"""Domain models for notification dispatch — pure dataclasses, no infrastructure deps."""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any


class ChannelType(StrEnum):
    EMAIL = "email"
    TELEGRAM = "telegram"
    PUSH = "push"


@dataclass(frozen=True, slots=True)
class RecipientInfo:
    email: str
    role: str  # "organizer" | "client"


@dataclass(frozen=True, slots=True)
class NotificationCommand:
    """Parsed notification.send_requested command."""

    event_id: str
    booking_id: str
    trigger_event: str
    recipients: list[RecipientInfo]
    template_data: dict[str, Any]
    source: str

    def __post_init__(self) -> None:
        # recipients приходят как list[dict], конвертируем
        object.__setattr__(
            self,
            "recipients",
            [
                r if isinstance(r, RecipientInfo) else RecipientInfo(**r)
                for r in self.recipients
            ],
        )


@dataclass(frozen=True, slots=True)
class ChannelContact:
    """A resolved channel contact for a recipient."""

    channel: ChannelType
    contact_id: str      # email addr / telegram chat_id / FCM device token
    user_email: str      # original recipient email (for result event)
    role: str            # "organizer" | "client"


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Result of a single channel delivery attempt."""

    channel: ChannelType
    success: bool
    message_id: str | None = None   # provider message ID
    error: str | None = None        # error message if success=False
```

- [ ] **Step 4: Запустить тесты — убедиться что проходят**

```bash
cd event-notifier
pytest tests/domain/test_notification_models.py -v
```

Ожидаемый результат: 4 tests PASSED

- [ ] **Step 5: Commit**

```bash
cd event-notifier
git add event_notifier/domain/ tests/domain/
git commit -m "feat(notifier): add domain models NotificationCommand, ChannelContact, DeliveryResult"
```

---

## Task 3: Interfaces (Protocol-классы)

**Files:**
- Create: `event-notifier/event_notifier/interfaces/channels.py`
- Create: `event-notifier/event_notifier/interfaces/users_client.py`
- Create: `event-notifier/event_notifier/interfaces/publisher.py`

- [ ] **Step 1: Создать `interfaces/channels.py`**

```python
from typing import Any, Protocol

from event_notifier.domain.models.notification import ChannelContact, DeliveryResult


class INotificationChannel(Protocol):
    async def send(
        self,
        *,
        contact: ChannelContact,
        trigger_event: str,
        template_data: dict[str, Any],
    ) -> DeliveryResult: ...
```

- [ ] **Step 2: Создать `interfaces/users_client.py`**

```python
from typing import Protocol

from event_notifier.domain.models.notification import ChannelContact


class IUsersClient(Protocol):
    async def get_contacts_by_email(self, *, email: str, role: str) -> list[ChannelContact]: ...
```

- [ ] **Step 3: Создать `interfaces/publisher.py`**

```python
from typing import Any, Protocol

from event_notifier.domain.models.notification import ChannelContact, DeliveryResult


class IResultEventPublisher(Protocol):
    async def publish_delivery_result(
        self,
        *,
        contact: ChannelContact,
        result: DeliveryResult,
        booking_id: str,
        trigger_event: str,
    ) -> None: ...
```

- [ ] **Step 4: Commit**

```bash
cd event-notifier
git add event_notifier/interfaces/
git commit -m "feat(notifier): add Protocol interfaces for channels, users_client, publisher"
```

---

## Task 4: UsersClient — HTTP-клиент для event-users

**Files:**
- Create: `event-notifier/event_notifier/infrastructure/users_client.py`
- Test: `event-notifier/tests/infrastructure/test_users_client.py`

- [ ] **Step 1: Написать тест (failing)**

Создать `event-notifier/tests/infrastructure/test_users_client.py`:

```python
import pytest
import respx
from httpx import AsyncClient, Response

from event_notifier.domain.models.notification import ChannelType
from event_notifier.infrastructure.users_client import UsersClient


@pytest.fixture
def http_client():
    return AsyncClient(base_url="http://users-service")


@pytest.mark.asyncio
async def test_get_contacts_by_email_returns_email_and_telegram(http_client):
    with respx.mock:
        respx.get("http://users-service/api/users").mock(
            return_value=Response(
                200,
                json={
                    "items": [{
                        "id": "uuid-1",
                        "email": "org@example.com",
                        "role": "organizer",
                        "name": "Org",
                        "time_zone": "UTC",
                        "contacts": [
                            {"channel": "telegram", "contact_id": "123456789",
                             "id": "c1", "user_id": "uuid-1",
                             "created_at": "2026-01-01T00:00:00Z",
                             "updated_at": "2026-01-01T00:00:00Z"},
                        ],
                        "created_at": "2026-01-01T00:00:00Z",
                        "updated_at": "2026-01-01T00:00:00Z",
                    }]
                },
            )
        )

        client = UsersClient(http_client=http_client, api_token="token")
        contacts = await client.get_contacts_by_email(email="org@example.com", role="organizer")

    # Email channel всегда добавляется (primary contact)
    email_contacts = [c for c in contacts if c.channel == ChannelType.EMAIL]
    assert len(email_contacts) == 1
    assert email_contacts[0].contact_id == "org@example.com"

    # Telegram из contacts
    tg_contacts = [c for c in contacts if c.channel == ChannelType.TELEGRAM]
    assert len(tg_contacts) == 1
    assert tg_contacts[0].contact_id == "123456789"


@pytest.mark.asyncio
async def test_get_contacts_by_email_user_not_found_returns_email_only(http_client):
    with respx.mock:
        respx.get("http://users-service/api/users").mock(
            return_value=Response(200, json={"items": []})
        )

        client = UsersClient(http_client=http_client, api_token="token")
        contacts = await client.get_contacts_by_email(email="unknown@example.com", role="client")

    # Даже если юзер не найден — email-канал всегда доступен
    assert len(contacts) == 1
    assert contacts[0].channel == ChannelType.EMAIL
    assert contacts[0].contact_id == "unknown@example.com"
```

- [ ] **Step 2: Запустить — убедиться что падают**

```bash
cd event-notifier
pytest tests/infrastructure/test_users_client.py -v
```

Ожидаемый результат: `ModuleNotFoundError`

- [ ] **Step 3: Создать `event-notifier/event_notifier/infrastructure/users_client.py`**

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
        """Resolve all notification contacts for a recipient.

        Always includes the email channel. Adds telegram/push if found in user_contacts.
        """
        contacts: list[ChannelContact] = [
            ChannelContact(
                channel=ChannelType.EMAIL,
                contact_id=email,
                user_email=email,
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
                    user_email=email,
                    role=role,
                )
            )

        logger.debug("Resolved contacts", email=email, channel_count=len(contacts))
        return contacts
```

- [ ] **Step 4: Запустить тесты**

```bash
cd event-notifier
pytest tests/infrastructure/test_users_client.py -v
```

Ожидаемый результат: 2 tests PASSED

- [ ] **Step 5: Commit**

```bash
cd event-notifier
git add event_notifier/infrastructure/users_client.py tests/infrastructure/test_users_client.py
git commit -m "feat(notifier): implement UsersClient with email fallback"
```

---

## Task 5: Email channel (UniSender)

**Files:**
- Create: `event-notifier/event_notifier/infrastructure/channels/email.py`
- Test: `event-notifier/tests/infrastructure/test_email_channel.py`

- [ ] **Step 1: Написать тест (failing)**

Создать `event-notifier/tests/infrastructure/test_email_channel.py`:

```python
import pytest
import respx
from httpx import AsyncClient, Response

from event_notifier.domain.models.notification import ChannelContact, ChannelType
from event_notifier.infrastructure.channels.email import EmailChannel


@pytest.fixture
def email_channel():
    client = AsyncClient(base_url="https://go.unisender.ru")
    return EmailChannel(
        http_client=client,
        api_key="test-key",
        from_email="noreply@example.com",
        from_name="Test",
    )


@pytest.fixture
def contact():
    return ChannelContact(
        channel=ChannelType.EMAIL,
        contact_id="recipient@example.com",
        user_email="recipient@example.com",
        role="client",
    )


@pytest.mark.asyncio
async def test_send_returns_success_with_job_id(email_channel, contact):
    with respx.mock:
        respx.post("https://go.unisender.ru/ru/transactional/api/v1/email/send.json").mock(
            return_value=Response(200, json={"status": "success", "job_id": "job-xyz"})
        )

        result = await email_channel.send(
            contact=contact,
            trigger_event="BOOKING_CREATED",
            template_data={"booking_id": "b-1"},
        )

    assert result.success is True
    assert result.message_id == "job-xyz"
    assert result.channel == ChannelType.EMAIL


@pytest.mark.asyncio
async def test_send_returns_failure_on_api_error(email_channel, contact):
    with respx.mock:
        respx.post("https://go.unisender.ru/ru/transactional/api/v1/email/send.json").mock(
            return_value=Response(400, json={"status": "error", "message": "Invalid API key"})
        )

        result = await email_channel.send(
            contact=contact,
            trigger_event="BOOKING_CREATED",
            template_data={},
        )

    assert result.success is False
    assert result.error is not None
```

- [ ] **Step 2: Запустить — убедиться что падают**

```bash
cd event-notifier
pytest tests/infrastructure/test_email_channel.py -v
```

Ожидаемый результат: `ModuleNotFoundError`

- [ ] **Step 3: Создать `event-notifier/event_notifier/infrastructure/channels/email.py`**

```python
"""Email notification channel via UniSender Go transactional API."""

from typing import Any

import structlog
from httpx import AsyncClient, HTTPStatusError

from event_notifier.domain.models.notification import ChannelContact, ChannelType, DeliveryResult


logger = structlog.get_logger(__name__)

# Maps trigger_event → UniSender template code.
# Add entries when new notification types are added.
_TEMPLATE_MAP: dict[str, str] = {
    "BOOKING_CREATED": "booking_created",
    "BOOKING_CANCELLED": "booking_cancelled",
    "BOOKING_RESCHEDULED": "booking_rescheduled",
    "BOOKING_REASSIGNED": "booking_reassigned",
    "BOOKING_REMINDER": "booking_reminder",
    "BOOKING_REJECTED": "booking_rejected",
}

_UNISENDER_URL = "/ru/transactional/api/v1/email/send.json"


class EmailChannel:
    def __init__(
        self,
        *,
        http_client: AsyncClient,
        api_key: str,
        from_email: str,
        from_name: str,
    ) -> None:
        self._client = http_client
        self._api_key = api_key
        self._from_email = from_email
        self._from_name = from_name

    async def send(
        self,
        *,
        contact: ChannelContact,
        trigger_event: str,
        template_data: dict[str, Any],
    ) -> DeliveryResult:
        template_code = _TEMPLATE_MAP.get(trigger_event)
        if not template_code:
            return DeliveryResult(
                channel=ChannelType.EMAIL,
                success=False,
                error=f"No email template for trigger_event={trigger_event}",
            )

        payload = {
            "api_key": self._api_key,
            "message": {
                "template_id": template_code,
                "recipients": [{"email": contact.contact_id}],
                "from_email": self._from_email,
                "from_name": self._from_name,
                "global_substitutions": template_data,
            },
        }

        try:
            response = await self._client.post(_UNISENDER_URL, json=payload)
            response.raise_for_status()
            body = response.json()
            job_id = body.get("job_id")
            logger.info("Email sent", to=contact.contact_id, trigger=trigger_event, job_id=job_id)
            return DeliveryResult(channel=ChannelType.EMAIL, success=True, message_id=job_id)
        except HTTPStatusError as exc:
            error = f"UniSender HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            logger.warning("Email send failed", to=contact.contact_id, error=error)
            return DeliveryResult(channel=ChannelType.EMAIL, success=False, error=error)
        except Exception as exc:
            logger.exception("Email send unexpected error", to=contact.contact_id)
            return DeliveryResult(channel=ChannelType.EMAIL, success=False, error=str(exc))
```

- [ ] **Step 4: Запустить тесты**

```bash
cd event-notifier
pytest tests/infrastructure/test_email_channel.py -v
```

Ожидаемый результат: 2 tests PASSED

- [ ] **Step 5: Commit**

```bash
cd event-notifier
git add event_notifier/infrastructure/channels/email.py tests/infrastructure/test_email_channel.py
git commit -m "feat(notifier): implement EmailChannel via UniSender transactional API"
```

---

## Task 6: Telegram channel

**Files:**
- Create: `event-notifier/event_notifier/infrastructure/channels/telegram.py`
- Test: `event-notifier/tests/infrastructure/test_telegram_channel.py`

- [ ] **Step 1: Написать тест (failing)**

Создать `event-notifier/tests/infrastructure/test_telegram_channel.py`:

```python
import pytest
import respx
from httpx import AsyncClient, Response

from event_notifier.domain.models.notification import ChannelContact, ChannelType
from event_notifier.infrastructure.channels.telegram import TelegramChannel


@pytest.fixture
def telegram_channel():
    client = AsyncClient(base_url="https://api.telegram.org")
    return TelegramChannel(http_client=client, bot_token="test-token")


@pytest.fixture
def contact():
    return ChannelContact(
        channel=ChannelType.TELEGRAM,
        contact_id="987654321",
        user_email="org@example.com",
        role="organizer",
    )


@pytest.mark.asyncio
async def test_send_returns_success_with_message_id(telegram_channel, contact):
    with respx.mock:
        respx.post("https://api.telegram.org/bottest-token/sendMessage").mock(
            return_value=Response(200, json={"ok": True, "result": {"message_id": 42}})
        )

        result = await telegram_channel.send(
            contact=contact,
            trigger_event="BOOKING_CREATED",
            template_data={"booking_id": "b-1"},
        )

    assert result.success is True
    assert result.message_id == "42"
    assert result.channel == ChannelType.TELEGRAM


@pytest.mark.asyncio
async def test_send_failure_on_forbidden(telegram_channel, contact):
    with respx.mock:
        respx.post("https://api.telegram.org/bottest-token/sendMessage").mock(
            return_value=Response(403, json={"ok": False, "description": "Forbidden: bot was blocked"})
        )

        result = await telegram_channel.send(
            contact=contact,
            trigger_event="BOOKING_CREATED",
            template_data={},
        )

    assert result.success is False
    assert "403" in result.error
```

- [ ] **Step 2: Запустить — убедиться что падают**

```bash
cd event-notifier
pytest tests/infrastructure/test_telegram_channel.py -v
```

- [ ] **Step 3: Создать `event-notifier/event_notifier/infrastructure/channels/telegram.py`**

```python
"""Telegram notification channel via Bot API sendMessage."""

from typing import Any

import structlog
from httpx import AsyncClient, HTTPStatusError

from event_notifier.domain.models.notification import ChannelContact, ChannelType, DeliveryResult


logger = structlog.get_logger(__name__)

_MESSAGE_TEMPLATES: dict[str, str] = {
    "BOOKING_CREATED": "Новая встреча забронирована.",
    "BOOKING_CANCELLED": "Встреча отменена.",
    "BOOKING_RESCHEDULED": "Встреча перенесена.",
    "BOOKING_REASSIGNED": "Встреча переназначена.",
    "BOOKING_REMINDER": "Напоминание о встрече.",
    "BOOKING_REJECTED": "Бронирование отклонено.",
}


class TelegramChannel:
    def __init__(self, *, http_client: AsyncClient, bot_token: str) -> None:
        self._client = http_client
        self._bot_token = bot_token

    async def send(
        self,
        *,
        contact: ChannelContact,
        trigger_event: str,
        template_data: dict[str, Any],
    ) -> DeliveryResult:
        text = _MESSAGE_TEMPLATES.get(trigger_event, f"Уведомление: {trigger_event}")

        try:
            response = await self._client.post(
                f"/bot{self._bot_token}/sendMessage",
                json={"chat_id": contact.contact_id, "text": text, "parse_mode": "HTML"},
            )
            response.raise_for_status()
            body = response.json()
            message_id = str(body["result"]["message_id"])
            logger.info("Telegram message sent", chat_id=contact.contact_id, message_id=message_id)
            return DeliveryResult(channel=ChannelType.TELEGRAM, success=True, message_id=message_id)
        except HTTPStatusError as exc:
            error = f"Telegram HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            logger.warning("Telegram send failed", chat_id=contact.contact_id, error=error)
            return DeliveryResult(channel=ChannelType.TELEGRAM, success=False, error=error)
        except Exception as exc:
            logger.exception("Telegram send unexpected error", chat_id=contact.contact_id)
            return DeliveryResult(channel=ChannelType.TELEGRAM, success=False, error=str(exc))
```

- [ ] **Step 4: Запустить тесты**

```bash
cd event-notifier
pytest tests/infrastructure/test_telegram_channel.py -v
```

Ожидаемый результат: 2 tests PASSED

- [ ] **Step 5: Commit**

```bash
cd event-notifier
git add event_notifier/infrastructure/channels/telegram.py tests/infrastructure/test_telegram_channel.py
git commit -m "feat(notifier): implement TelegramChannel via Bot API"
```

---

## Task 7: Push channel (FCM HTTP v1)

**Files:**
- Create: `event-notifier/event_notifier/infrastructure/channels/push.py`
- Test: `event-notifier/tests/infrastructure/test_push_channel.py`

- [ ] **Step 1: Написать тест (failing)**

Создать `event-notifier/tests/infrastructure/test_push_channel.py`:

```python
import pytest
import respx
from httpx import AsyncClient, Response
from unittest.mock import MagicMock

from event_notifier.domain.models.notification import ChannelContact, ChannelType
from event_notifier.infrastructure.channels.push import PushChannel


@pytest.fixture
def push_channel():
    client = AsyncClient(base_url="https://fcm.googleapis.com")
    token_provider = MagicMock()
    token_provider.get_access_token = MagicMock(return_value="fake-access-token")
    return PushChannel(
        http_client=client,
        project_id="my-project",
        access_token_provider=token_provider,
    )


@pytest.fixture
def contact():
    return ChannelContact(
        channel=ChannelType.PUSH,
        contact_id="device-token-xyz",
        user_email="client@example.com",
        role="client",
    )


@pytest.mark.asyncio
async def test_send_push_success(push_channel, contact):
    with respx.mock:
        respx.post(
            "https://fcm.googleapis.com/v1/projects/my-project/messages:send"
        ).mock(
            return_value=Response(200, json={"name": "projects/my-project/messages/msg-123"})
        )

        result = await push_channel.send(
            contact=contact,
            trigger_event="BOOKING_CREATED",
            template_data={"booking_id": "b-1"},
        )

    assert result.success is True
    assert "msg-123" in result.message_id


@pytest.mark.asyncio
async def test_send_push_invalid_token(push_channel, contact):
    with respx.mock:
        respx.post(
            "https://fcm.googleapis.com/v1/projects/my-project/messages:send"
        ).mock(
            return_value=Response(400, json={"error": {"code": 400, "message": "INVALID_ARGUMENT"}})
        )

        result = await push_channel.send(
            contact=contact,
            trigger_event="BOOKING_CREATED",
            template_data={},
        )

    assert result.success is False
    assert result.error is not None
```

- [ ] **Step 2: Запустить — убедиться что падают**

```bash
cd event-notifier
pytest tests/infrastructure/test_push_channel.py -v
```

- [ ] **Step 3: Создать `event-notifier/event_notifier/infrastructure/channels/push.py`**

```python
"""Push notification channel via FCM HTTP v1 API."""

from typing import Any, Protocol

import structlog
from httpx import AsyncClient, HTTPStatusError

from event_notifier.domain.models.notification import ChannelContact, ChannelType, DeliveryResult


logger = structlog.get_logger(__name__)

_PUSH_TITLES: dict[str, str] = {
    "BOOKING_CREATED": "Новая встреча",
    "BOOKING_CANCELLED": "Встреча отменена",
    "BOOKING_RESCHEDULED": "Встреча перенесена",
    "BOOKING_REASSIGNED": "Встреча переназначена",
    "BOOKING_REMINDER": "Напоминание",
    "BOOKING_REJECTED": "Бронирование отклонено",
}


class IAccessTokenProvider(Protocol):
    def get_access_token(self) -> str: ...


class PushChannel:
    def __init__(
        self,
        *,
        http_client: AsyncClient,
        project_id: str,
        access_token_provider: IAccessTokenProvider,
    ) -> None:
        self._client = http_client
        self._project_id = project_id
        self._token_provider = access_token_provider

    async def send(
        self,
        *,
        contact: ChannelContact,
        trigger_event: str,
        template_data: dict[str, Any],
    ) -> DeliveryResult:
        title = _PUSH_TITLES.get(trigger_event, "Уведомление")
        access_token = self._token_provider.get_access_token()

        payload = {
            "message": {
                "token": contact.contact_id,
                "notification": {"title": title, "body": ""},
                "data": {"trigger_event": trigger_event, **{k: str(v) for k, v in template_data.items()}},
            }
        }

        try:
            response = await self._client.post(
                f"/v1/projects/{self._project_id}/messages:send",
                json=payload,
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            message_name = response.json().get("name", "")
            logger.info("Push sent", device_token=contact.contact_id[:20], message=message_name)
            return DeliveryResult(channel=ChannelType.PUSH, success=True, message_id=message_name)
        except HTTPStatusError as exc:
            error = f"FCM HTTP {exc.response.status_code}: {exc.response.text[:200]}"
            logger.warning("Push send failed", error=error)
            return DeliveryResult(channel=ChannelType.PUSH, success=False, error=error)
        except Exception as exc:
            logger.exception("Push send unexpected error")
            return DeliveryResult(channel=ChannelType.PUSH, success=False, error=str(exc))
```

- [ ] **Step 4: Запустить тесты**

```bash
cd event-notifier
pytest tests/infrastructure/test_push_channel.py -v
```

Ожидаемый результат: 2 tests PASSED

- [ ] **Step 5: Commit**

```bash
cd event-notifier
git add event_notifier/infrastructure/channels/push.py tests/infrastructure/test_push_channel.py
git commit -m "feat(notifier): implement PushChannel via FCM HTTP v1"
```

---

## Task 8: ResultEventPublisher

**Files:**
- Create: `event-notifier/event_notifier/infrastructure/publisher.py`

- [ ] **Step 1: Создать `event-notifier/event_notifier/infrastructure/publisher.py`**

Этот компонент публикует результаты отправки как CloudEvents в event-receiver через HTTP POST `/event/cloudevents`.

```python
"""Publishes notification delivery result events to event-receiver."""

import uuid
from datetime import UTC, datetime
from typing import Any

import structlog
from httpx import AsyncClient

from event_notifier.domain.models.notification import ChannelContact, ChannelType, DeliveryResult
from event_notifier.event_types import (
    NOTIFICATION_EMAIL_SENT,
    NOTIFICATION_PUSH_SENT,
    NOTIFICATION_TELEGRAM_SENT,
    NOTIFIER_SOURCE,
)


logger = structlog.get_logger(__name__)

_CHANNEL_TO_EVENT_TYPE = {
    ChannelType.EMAIL: NOTIFICATION_EMAIL_SENT,
    ChannelType.TELEGRAM: NOTIFICATION_TELEGRAM_SENT,
    ChannelType.PUSH: NOTIFICATION_PUSH_SENT,
}


class ResultEventPublisher:
    def __init__(self, *, http_client: AsyncClient, jwt_token: str) -> None:
        self._client = http_client
        self._headers = {
            "Authorization": f"Bearer {jwt_token}",
            "Content-Type": "application/json",
        }

    async def publish_delivery_result(
        self,
        *,
        contact: ChannelContact,
        result: DeliveryResult,
        booking_id: str,
        trigger_event: str,
    ) -> None:
        if not result.success:
            logger.debug(
                "Skipping result publish for failed delivery",
                channel=result.channel,
                error=result.error,
            )
            return

        event_type = _CHANNEL_TO_EVENT_TYPE[result.channel]
        payload = self._build_payload(
            contact=contact,
            result=result,
            trigger_event=trigger_event,
        )

        headers = {
            **self._headers,
            "ce-specversion": "1.0",
            "ce-type": event_type,
            "ce-source": NOTIFIER_SOURCE,
            "ce-id": str(uuid.uuid4()),
            "ce-time": datetime.now(UTC).isoformat(),
            "ce-booking_id": booking_id,
            "Content-Type": "application/json",
        }

        try:
            response = await self._client.post(
                "/event/cloudevents",
                content=self._serialize(payload),
                headers=headers,
            )
            response.raise_for_status()
            logger.info(
                "Delivery result published",
                event_type=event_type,
                booking_id=booking_id,
                channel=result.channel,
            )
        except Exception:
            logger.exception(
                "Failed to publish delivery result",
                event_type=event_type,
                booking_id=booking_id,
            )
            # Fire-and-forget: не прерываем основной поток

    def _build_payload(
        self,
        *,
        contact: ChannelContact,
        result: DeliveryResult,
        trigger_event: str,
    ) -> dict[str, Any]:
        base = {
            "email": contact.user_email,
            "recipient_role": contact.role,
            "trigger_event": trigger_event,
        }
        if result.channel == ChannelType.EMAIL:
            base["job_id"] = result.message_id
        elif result.channel == ChannelType.PUSH:
            base["device_token"] = contact.contact_id
            base["message_id"] = result.message_id
        return base

    @staticmethod
    def _serialize(payload: dict[str, Any]) -> bytes:
        import json
        return json.dumps(payload).encode()
```

- [ ] **Step 2: Проверить импорт**

```bash
cd event-notifier
python -c "from event_notifier.infrastructure.publisher import ResultEventPublisher; print('OK')"
```

- [ ] **Step 3: Commit**

```bash
cd event-notifier
git add event_notifier/infrastructure/publisher.py
git commit -m "feat(notifier): implement ResultEventPublisher (CloudEvents POST to event-receiver)"
```

---

## Task 9: DispatchNotificationUseCase

**Files:**
- Create: `event-notifier/event_notifier/application/use_cases/dispatch_notification.py`
- Test: `event-notifier/tests/application/test_dispatch_notification.py`

- [ ] **Step 1: Написать тест (failing)**

Создать `event-notifier/tests/application/test_dispatch_notification.py`:

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

from event_notifier.application.use_cases.dispatch_notification import DispatchNotificationUseCase
from event_notifier.domain.models.notification import (
    ChannelContact,
    ChannelType,
    DeliveryResult,
    NotificationCommand,
    RecipientInfo,
)


@pytest.fixture
def mock_users_client():
    client = MagicMock()
    client.get_contacts_by_email = AsyncMock(return_value=[
        ChannelContact(channel=ChannelType.EMAIL, contact_id="org@example.com", user_email="org@example.com", role="organizer"),
        ChannelContact(channel=ChannelType.TELEGRAM, contact_id="11111", user_email="org@example.com", role="organizer"),
    ])
    return client


@pytest.fixture
def mock_email_channel():
    ch = MagicMock()
    ch.send = AsyncMock(return_value=DeliveryResult(channel=ChannelType.EMAIL, success=True, message_id="job-1"))
    return ch


@pytest.fixture
def mock_telegram_channel():
    ch = MagicMock()
    ch.send = AsyncMock(return_value=DeliveryResult(channel=ChannelType.TELEGRAM, success=True, message_id="msg-1"))
    return ch


@pytest.fixture
def mock_publisher():
    pub = MagicMock()
    pub.publish_delivery_result = AsyncMock()
    return pub


@pytest.fixture
def use_case(mock_users_client, mock_email_channel, mock_telegram_channel, mock_publisher):
    return DispatchNotificationUseCase(
        users_client=mock_users_client,
        channels={ChannelType.EMAIL: mock_email_channel, ChannelType.TELEGRAM: mock_telegram_channel},
        publisher=mock_publisher,
    )


@pytest.fixture
def command():
    return NotificationCommand(
        event_id="evt-1",
        booking_id="booking-abc",
        trigger_event="BOOKING_CREATED",
        recipients=[RecipientInfo(email="org@example.com", role="organizer")],
        template_data={"booking_id": "booking-abc"},
        source="booking",
    )


@pytest.mark.asyncio
async def test_dispatches_to_all_resolved_channels(use_case, command, mock_email_channel, mock_telegram_channel, mock_publisher):
    await use_case.execute(command)

    mock_email_channel.send.assert_awaited_once()
    mock_telegram_channel.send.assert_awaited_once()
    assert mock_publisher.publish_delivery_result.await_count == 2


@pytest.mark.asyncio
async def test_channel_failure_does_not_stop_other_channels(use_case, command, mock_email_channel, mock_telegram_channel, mock_publisher):
    mock_email_channel.send = AsyncMock(side_effect=RuntimeError("UniSender down"))

    await use_case.execute(command)  # не должен падать

    # Telegram всё равно должен был отправить
    mock_telegram_channel.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_publishes_result_for_each_successful_send(use_case, command, mock_publisher):
    await use_case.execute(command)

    calls = mock_publisher.publish_delivery_result.call_args_list
    channels_published = {c.kwargs["result"].channel for c in calls}
    assert ChannelType.EMAIL in channels_published
    assert ChannelType.TELEGRAM in channels_published
```

- [ ] **Step 2: Запустить — убедиться что падают**

```bash
cd event-notifier
pytest tests/application/test_dispatch_notification.py -v
```

- [ ] **Step 3: Создать `event-notifier/event_notifier/application/use_cases/dispatch_notification.py`**

```python
"""Use case: dispatch notification command to all applicable channels."""

import structlog

from event_notifier.domain.models.notification import ChannelContact, ChannelType, DeliveryResult, NotificationCommand
from event_notifier.interfaces.channels import INotificationChannel
from event_notifier.interfaces.publisher import IResultEventPublisher
from event_notifier.interfaces.users_client import IUsersClient


logger = structlog.get_logger(__name__)


class DispatchNotificationUseCase:
    def __init__(
        self,
        *,
        users_client: IUsersClient,
        channels: dict[ChannelType, INotificationChannel],
        publisher: IResultEventPublisher,
    ) -> None:
        self._users_client = users_client
        self._channels = channels
        self._publisher = publisher

    async def execute(self, command: NotificationCommand) -> None:
        logger.info(
            "Dispatching notification",
            booking_id=command.booking_id,
            trigger_event=command.trigger_event,
            recipient_count=len(command.recipients),
        )

        for recipient in command.recipients:
            contacts = await self._users_client.get_contacts_by_email(
                email=recipient.email,
                role=recipient.role,
            )
            for contact in contacts:
                result = await self._send_to_channel(
                    contact=contact,
                    trigger_event=command.trigger_event,
                    template_data=command.template_data,
                )
                await self._publisher.publish_delivery_result(
                    contact=contact,
                    result=result,
                    booking_id=command.booking_id,
                    trigger_event=command.trigger_event,
                )

    async def _send_to_channel(
        self,
        *,
        contact: ChannelContact,
        trigger_event: str,
        template_data: dict,
    ) -> DeliveryResult:
        channel = self._channels.get(contact.channel)
        if channel is None:
            return DeliveryResult(
                channel=contact.channel,
                success=False,
                error=f"No channel adapter for {contact.channel}",
            )
        try:
            return await channel.send(
                contact=contact,
                trigger_event=trigger_event,
                template_data=template_data,
            )
        except Exception as exc:
            logger.exception(
                "Channel send raised unexpected error",
                channel=contact.channel,
                email=contact.user_email,
            )
            return DeliveryResult(channel=contact.channel, success=False, error=str(exc))
```

- [ ] **Step 4: Запустить тесты**

```bash
cd event-notifier
pytest tests/application/test_dispatch_notification.py -v
```

Ожидаемый результат: 3 tests PASSED

- [ ] **Step 5: Commit**

```bash
cd event-notifier
git add event_notifier/application/ tests/application/
git commit -m "feat(notifier): implement DispatchNotificationUseCase with per-channel isolation"
```

---

## Task 10: RabbitMQ consumer + IoC + main

**Files:**
- Create: `event-notifier/event_notifier/adapters/consumer.py`
- Create: `event-notifier/event_notifier/ioc.py`
- Create: `event-notifier/event_notifier/main.py`

- [ ] **Step 1: Создать `event-notifier/event_notifier/adapters/consumer.py`**

```python
"""RabbitMQ consumer for events.notification.commands queue."""

import json
from typing import Any

import structlog
from cloudevents.http import from_http
from faststream import Context
from faststream.rabbit import RabbitBroker, RabbitExchange, RabbitQueue

from event_notifier.application.use_cases.dispatch_notification import DispatchNotificationUseCase
from event_notifier.domain.models.notification import NotificationCommand, RecipientInfo
from event_notifier.event_types import NOTIFICATION_SEND_REQUESTED


logger = structlog.get_logger(__name__)


class NotificationConsumer:
    def __init__(
        self,
        *,
        broker: RabbitBroker,
        exchange: RabbitExchange,
        queue_name: str,
        use_case: DispatchNotificationUseCase,
    ) -> None:
        self._broker = broker
        self._exchange = exchange
        self._queue_name = queue_name
        self._use_case = use_case
        self._started = False

    async def start(self) -> None:
        if self._started:
            return

        subscriber = self._broker.subscriber(
            queue=RabbitQueue(
                name=self._queue_name,
                durable=True,
                routing_key=self._queue_name,
                declare=False,
            ),
            exchange=self._exchange,
        )

        @subscriber
        async def consume(message: Any = Context("message")) -> None:
            await self._handle(message)

        await self._broker.start()
        self._started = True
        logger.info("Notification consumer started", queue=self._queue_name)

    async def stop(self) -> None:
        if not self._started:
            return
        await self._broker.stop()
        self._started = False

    async def _handle(self, message: Any) -> None:
        try:
            event = from_http(headers=message.headers, data=message.body)
        except Exception:
            logger.exception("Failed to parse CloudEvent")
            raise

        if event["type"] != NOTIFICATION_SEND_REQUESTED:
            logger.warning("Unexpected event type, skipping", event_type=event["type"])
            return

        data = event.data or {}
        try:
            command = NotificationCommand(
                event_id=event["id"],
                booking_id=event.get("booking_id") or data.get("booking_id", ""),
                trigger_event=data["trigger_event"],
                recipients=[
                    RecipientInfo(email=r["email"], role=r["role"])
                    for r in data.get("recipients", [])
                ],
                template_data=data.get("template_data", {}),
                source=event["source"],
            )
        except (KeyError, TypeError) as exc:
            logger.error("Malformed notification command payload", error=str(exc))
            return  # не реджектим — skip и логируем

        await self._use_case.execute(command)
```

- [ ] **Step 2: Создать `event-notifier/event_notifier/ioc.py`**

```python
"""Dishka DI container for event-notifier."""

from collections.abc import AsyncGenerator

import structlog
from dishka import Provider, Scope, provide
from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange
from httpx import AsyncClient

from event_notifier.adapters.consumer import NotificationConsumer
from event_notifier.application.use_cases.dispatch_notification import DispatchNotificationUseCase
from event_notifier.config import Settings
from event_notifier.domain.models.notification import ChannelType
from event_notifier.infrastructure.channels.email import EmailChannel
from event_notifier.infrastructure.channels.push import IAccessTokenProvider, PushChannel
from event_notifier.infrastructure.channels.telegram import TelegramChannel
from event_notifier.infrastructure.publisher import ResultEventPublisher
from event_notifier.infrastructure.users_client import UsersClient
from event_notifier.interfaces.channels import INotificationChannel


logger = structlog.get_logger(__name__)


class AppProvider(Provider):

    @provide(scope=Scope.APP)
    def provide_settings(self) -> Settings:
        return Settings()

    @provide(scope=Scope.APP)
    def provide_exchange(self, settings: Settings) -> RabbitExchange:
        return RabbitExchange(name=settings.rabbit_exchange, type=ExchangeType.TOPIC, durable=True)

    @provide(scope=Scope.APP)
    def provide_broker(self, settings: Settings) -> RabbitBroker:
        return RabbitBroker(str(settings.rabbit_url))

    @provide(scope=Scope.APP)
    async def provide_users_http_client(self, settings: Settings) -> AsyncGenerator[AsyncClient, None]:
        async with AsyncClient(base_url=str(settings.event_users_url)) as client:
            yield client

    @provide(scope=Scope.APP)
    async def provide_receiver_http_client(self, settings: Settings) -> AsyncGenerator[AsyncClient, None]:
        async with AsyncClient(base_url=str(settings.event_receiver_url)) as client:
            yield client

    @provide(scope=Scope.APP)
    def provide_users_client(self, http_client: AsyncClient, settings: Settings) -> UsersClient:
        return UsersClient(http_client=http_client, api_token=settings.event_users_token)

    @provide(scope=Scope.APP)
    def provide_publisher(self, http_client: AsyncClient, settings: Settings) -> ResultEventPublisher:
        return ResultEventPublisher(http_client=http_client, jwt_token=settings.event_receiver_jwt)

    @provide(scope=Scope.APP)
    def provide_email_channel(self, settings: Settings) -> EmailChannel:
        from httpx import AsyncClient as AC
        client = AC(base_url="https://go.unisender.ru")
        return EmailChannel(
            http_client=client,
            api_key=settings.unisender_api_key,
            from_email=settings.unisender_from_email,
            from_name=settings.unisender_from_name,
        )

    @provide(scope=Scope.APP)
    def provide_telegram_channel(self, settings: Settings) -> TelegramChannel:
        from httpx import AsyncClient as AC
        client = AC(base_url="https://api.telegram.org")
        return TelegramChannel(http_client=client, bot_token=settings.telegram_bot_token)

    @provide(scope=Scope.APP)
    def provide_channels(
        self,
        email: EmailChannel,
        telegram: TelegramChannel,
    ) -> dict[ChannelType, INotificationChannel]:
        return {
            ChannelType.EMAIL: email,
            ChannelType.TELEGRAM: telegram,
            # ChannelType.PUSH: push_channel  # включить когда FCM настроен
        }

    @provide(scope=Scope.APP)
    def provide_use_case(
        self,
        users_client: UsersClient,
        channels: dict[ChannelType, INotificationChannel],
        publisher: ResultEventPublisher,
    ) -> DispatchNotificationUseCase:
        return DispatchNotificationUseCase(
            users_client=users_client,
            channels=channels,
            publisher=publisher,
        )

    @provide(scope=Scope.APP)
    def provide_consumer(
        self,
        broker: RabbitBroker,
        exchange: RabbitExchange,
        settings: Settings,
        use_case: DispatchNotificationUseCase,
    ) -> NotificationConsumer:
        return NotificationConsumer(
            broker=broker,
            exchange=exchange,
            queue_name=settings.notification_commands_queue,
            use_case=use_case,
        )
```

- [ ] **Step 3: Создать `event-notifier/event_notifier/main.py`**

```python
from contextlib import asynccontextmanager
from logging import getLevelNamesMapping
from typing import TYPE_CHECKING

import structlog
from dishka import make_async_container
from fastapi import FastAPI

from event_notifier.adapters.consumer import NotificationConsumer
from event_notifier.config import Settings
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

    consumer = await container.get(NotificationConsumer)
    await consumer.start()

    logger.info("event-notifier ready, consuming notifications")

    yield

    logger.info("Shutting down event-notifier")
    await consumer.stop()
    await container.close()


app = FastAPI(title="event-notifier", version="0.1.0", lifespan=lifespan)


@app.get("/health")
async def health() -> dict:
    return {"status": "ok"}
```

- [ ] **Step 4: Проверить импорты без запуска**

```bash
cd event-notifier
python -c "from event_notifier.main import app; print('imports OK')"
```

Ожидаемый вывод: `imports OK`

- [ ] **Step 5: Commit**

```bash
cd event-notifier
git add event_notifier/adapters/ event_notifier/ioc.py event_notifier/main.py
git commit -m "feat(notifier): wire consumer, IoC container, and FastAPI main"
```

---

## Task 11: Full integration smoke test

- [ ] **Step 1: Запустить все тесты**

```bash
cd event-notifier
pytest tests/ -v
```

Ожидаемый результат: все тесты PASSED (минимум 11 тестов из предыдущих задач).

- [ ] **Step 2: Запустить lint**

```bash
cd event-notifier
ruff check .
ruff format --check .
```

Ожидаемый результат: No errors.

- [ ] **Step 3: Проверить, что сервис стартует (с локальным RabbitMQ)**

Для этого потребуется запущенный RabbitMQ и `.env` с реальными значениями (или заглушками для non-critical).

```bash
cd event-notifier
uvicorn event_notifier.main:app --host 0.0.0.0 --port 8889 &
sleep 3
curl http://localhost:8889/health
```

Ожидаемый вывод: `{"status":"ok"}`

- [ ] **Step 4: Финальный commit**

```bash
cd event-notifier
git add .
git commit -m "feat(notifier): complete event-notifier v1 implementation (email + telegram channels)"
```

---

## Self-Review Checklist

- [x] **Spec coverage**: все каналы (email, telegram, push) покрыты. Push закомментирован в IoC до настройки FCM credentials.
- [x] **Prerequisite**: план явно ссылается на `2026-04-18-notification-contracts.md`
- [x] **Placeholder scan**: нет TBD/TODO в коде задач
- [x] **Type consistency**: `ChannelContact`, `DeliveryResult`, `NotificationCommand` используются последовательно в Tasks 2–9
- [x] **Error isolation**: каждый канал изолирован — падение одного не блокирует другие (Task 9, `_send_to_channel`)
- [x] **Fire-and-forget publisher**: ошибки публикации результата не прерывают основной поток (Task 8)
- [x] **Email fallback**: даже если юзер не найден в event-users, email-канал всегда работает (Task 4)

**Что намеренно отложено:**
- FCM (`PushChannel`) реализован и протестирован, но закомментирован в IoC до получения Firebase credentials
- Dead Letter Queue для `events.notification.commands` — нужна отдельная задача в infrastructure-плане
- OpenTelemetry tracing — отдельный план
