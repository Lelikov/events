# Manageable Notification Templates Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make per-event channel enablement and template selection admin-managed — stored in event-notifier's DB, edited through the event-admin UI (Telegram Jinja bodies inline, email templates picked from a cached UniSender list).

**Architecture:** event-notifier owns a `notification_bindings` table `(trigger_event, channel) → enabled + template`, read at runtime through a TTL-cached `BindingsProvider`; channel selection and the Email/Telegram channels read from it. event-notifier exposes an admin API (service-token auth). event-admin proxies `/api/notifications/*` (require_admin) like it proxies event-users. The frontend gets a matrix module.

**Tech Stack:** Python 3.14, FastAPI, FastStream, Dishka, SQLAlchemy (raw `text()`), alembic, Jinja2 (sandboxed), httpx, React/Vite/TS, vitest.

**Verification model:** Python services use `uv run pytest -q` + `uv run ruff check .`; frontend `npm run test` + `npm run build`. pre-commit is not installed → commit with `git commit --no-verify`. Each repo is its own git repo on `main` (user consented); push per repo at the end of its phase.

---

## Reference: anchors

- event-notifier `event_notifier/config.py` — `Settings(BaseSettings)`; has `default_locale="ru"`,
  `unisender_*`, `telegram_*`, `unisender_template_ids` + `unisender_template_ids_by_locale()`.
- `event_notifier/domain/models/notification.py` — `ChannelType(StrEnum)` = email/telegram/push;
  `ChannelContact`, `DeliveryResult`.
- `event_notifier/interfaces/channels.py` — `INotificationChannel.send(*, contact, trigger_event, template_data)`.
- `event_notifier/infrastructure/channels/email.py` — `EmailChannel(http_client, ..., template_ids_by_locale, default_locale)`, `_template_id(trigger, template_data)`.
- `event_notifier/infrastructure/channels/telegram.py` — `TelegramChannel(http_client, ..., template_env, default_locale)`, `_render(trigger, template_data)`.
- `event_notifier/application/use_cases/process_notification_command.py` — `_resolve_contacts` (email always; telegram if `telegram_chat_id`).
- `event_notifier/adapters/sql.py` — `SqlExecutor` (`fetch_one`/`fetch_all`/`execute`/`transaction`), `ISqlExecutor` interface in `interfaces/`.
- `event_notifier/db/repository.py` — raw `text()` SQL via `self._sql`.
- `event_notifier/ioc.py` — Dishka `AppProvider`; `provide_email_channel`/`provide_telegram_channel`/`provide_outbox_sender`. `event_notifier/main.py` — FastAPI app with only `@app.get` health/metrics/ready (NO Dishka-FastAPI integration yet, NO routers).
- `alembic/versions/` — `001_initial_schema.py`, `002_command_path_redesign.py`.
- event-admin `event_admin/adapters/users_client.py` (`UsersClient(http_client, api_token, cache)`), `event_admin/config.py` (`users_service_url`, `users_service_api_token`, `blacklist_service_token` patterns), `event_admin/routes.py` (`DishkaRoute` routers gated by `require_admin`, `_users_proxy_error`), `event_admin/ioc.py`.
- event-admin-frontend `src/modules/shared/routing.ts` (`AppRoute` union + `parseRoute` + `navigateTo`), `src/modules/app/AdminLayout.tsx` (nav items `{label, path}`), `src/modules/shared/api.ts` (`apiRequest`), `App.tsx` (route → component).
- `TriggerEvent` (event-schemas) values: BOOKING_CREATED, BOOKING_RESCHEDULED, BOOKING_REASSIGNED, BOOKING_CANCELLED, BOOKING_REMINDER, BOOKING_REJECTED, BOOKING_REJECTED_BLACKLISTED.

---

# Phase 1 — Notifier data model + runtime

### Task 1.1: `notification_bindings` migration + seed

**Files:**
- Create: `event-notifier/alembic/versions/003_notification_bindings.py`

- [ ] **Step 1: Confirm the alembic down-revision**

Run: `grep -RnE "revision =|down_revision =" event-notifier/alembic/versions/002_command_path_redesign.py`
Expected: note `002`'s `revision` value to use as this migration's `down_revision`.

- [ ] **Step 2: Write the migration (table + seed from env + .j2 files)**

Create `event-notifier/alembic/versions/003_notification_bindings.py`:
```python
"""notification_bindings: admin-managed per-event channel + template config."""

import json
import os
from pathlib import Path

import sqlalchemy as sa
from alembic import op

revision = "003_notification_bindings"
down_revision = "002_command_path_redesign"  # set to 002's actual revision id from Step 1
branch_labels = None
depends_on = None

_TRIGGERS = [
    "BOOKING_CREATED", "BOOKING_RESCHEDULED", "BOOKING_REASSIGNED", "BOOKING_CANCELLED",
    "BOOKING_REMINDER", "BOOKING_REJECTED", "BOOKING_REJECTED_BLACKLISTED",
]


def _seed_rows() -> list[dict]:
    default_locale = os.getenv("DEFAULT_LOCALE", "ru")
    raw = os.getenv("UNISENDER_TEMPLATE_IDS", "{}")
    try:
        parsed = json.loads(raw)
    except ValueError:
        parsed = {}
    # Flatten to {TRIGGER: uuid} for the default locale (mirrors unisender_template_ids_by_locale).
    email_ids: dict[str, str] = {}
    for key, value in parsed.items():
        if isinstance(value, dict):
            if key == default_locale:
                email_ids.update(value)
        elif isinstance(value, str):
            email_ids[key] = value

    tg_dir = Path(__file__).resolve().parents[2] / "event_notifier" / "templates" / default_locale / "telegram"
    rows = []
    for trigger in _TRIGGERS:
        rows.append({
            "trigger_event": trigger, "channel": "email", "enabled": True,
            "unisender_template_id": email_ids.get(trigger), "telegram_body": None,
        })
        tg_file = tg_dir / f"{trigger}.j2"
        body = tg_file.read_text(encoding="utf-8") if tg_file.exists() else None
        rows.append({
            "trigger_event": trigger, "channel": "telegram", "enabled": body is not None,
            "unisender_template_id": None, "telegram_body": body,
        })
    return rows


def upgrade() -> None:
    table = op.create_table(
        "notification_bindings",
        sa.Column("trigger_event", sa.Text(), nullable=False),
        sa.Column("channel", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("unisender_template_id", sa.Text(), nullable=True),
        sa.Column("telegram_body", sa.Text(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.PrimaryKeyConstraint("trigger_event", "channel", name="pk_notification_bindings"),
    )
    op.bulk_insert(table, _seed_rows())


def downgrade() -> None:
    op.drop_table("notification_bindings")
```

- [ ] **Step 3: Apply against pg-notifier and verify**

Run: `docker compose exec event-notifier uv run alembic upgrade head` (or run alembic locally against `DATABASE_URL`).
Then: `docker compose exec -T pg-notifier psql -U postgres -d event_notifier -c "SELECT trigger_event, channel, enabled, unisender_template_id IS NOT NULL AS has_email, telegram_body IS NOT NULL AS has_tg FROM notification_bindings ORDER BY 1,2;"`
Expected: 14 rows (7 triggers × 2 channels); email rows `enabled=t`; telegram rows `enabled=t` where a `.j2` existed.

- [ ] **Step 4: Commit**

```bash
git -C event-notifier add alembic/versions/003_notification_bindings.py
git -C event-notifier commit --no-verify -m "feat(notifier): notification_bindings table + seed from env/.j2"
```

### Task 1.2: Binding model + BindingsProvider (TTL cache)

**Files:**
- Create: `event-notifier/event_notifier/domain/models/binding.py`
- Create: `event-notifier/event_notifier/adapters/bindings_provider.py`
- Test: `event-notifier/tests/adapters/test_bindings_provider.py`

- [ ] **Step 1: Define the binding DTO**

Create `event_notifier/domain/models/binding.py`:
```python
from dataclasses import dataclass

from event_notifier.domain.models.notification import ChannelType


@dataclass(frozen=True)
class NotificationBinding:
    trigger_event: str
    channel: ChannelType
    enabled: bool
    unisender_template_id: str | None
    telegram_body: str | None
```

- [ ] **Step 2: Write the failing test**

Create `event_notifier/tests/adapters/test_bindings_provider.py`:
```python
import time

import pytest

from event_notifier.adapters.bindings_provider import BindingsProvider
from event_notifier.domain.models.notification import ChannelType


class _FakeSql:
    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    async def fetch_all(self, *_a, **_k):
        self.calls += 1
        return self.rows


@pytest.mark.anyio
async def test_get_returns_binding_and_caches():
    rows = [
        {"trigger_event": "BOOKING_CREATED", "channel": "email", "enabled": True,
         "unisender_template_id": "uuid-1", "telegram_body": None},
        {"trigger_event": "BOOKING_CREATED", "channel": "telegram", "enabled": False,
         "unisender_template_id": None, "telegram_body": "hi {{ name }}"},
    ]
    sql = _FakeSql(rows)
    provider = BindingsProvider(sql=sql, ttl_seconds=60)

    b = await provider.get("BOOKING_CREATED", ChannelType.EMAIL)
    assert b is not None and b.enabled and b.unisender_template_id == "uuid-1"
    tg = await provider.get("BOOKING_CREATED", ChannelType.TELEGRAM)
    assert tg is not None and tg.enabled is False
    assert sql.calls == 1  # second get served from cache


@pytest.mark.anyio
async def test_missing_binding_returns_none():
    provider = BindingsProvider(sql=_FakeSql([]), ttl_seconds=60)
    assert await provider.get("BOOKING_CREATED", ChannelType.EMAIL) is None
```

- [ ] **Step 3: Run it (fails)**

Run: `cd event-notifier && uv run pytest tests/adapters/test_bindings_provider.py -q`
Expected: FAIL — module not found.

- [ ] **Step 4: Implement the provider**

Create `event_notifier/adapters/bindings_provider.py`:
```python
import time
from typing import Any

from sqlalchemy import text

from event_notifier.domain.models.binding import NotificationBinding
from event_notifier.domain.models.notification import ChannelType
from event_notifier.interfaces.sql import ISqlExecutor


class BindingsProvider:
    """Reads notification_bindings with a short in-memory TTL cache so admin edits
    apply within the TTL without a restart."""

    def __init__(self, *, sql: ISqlExecutor, ttl_seconds: int = 30) -> None:
        self._sql = sql
        self._ttl = ttl_seconds
        self._cache: dict[tuple[str, str], NotificationBinding] = {}
        self._expires_at = 0.0

    async def _refresh(self) -> None:
        rows: list[dict[str, Any]] = await self._sql.fetch_all(
            text(
                "SELECT trigger_event, channel, enabled, unisender_template_id, telegram_body "
                "FROM notification_bindings"
            ),
        )
        self._cache = {
            (r["trigger_event"], r["channel"]): NotificationBinding(
                trigger_event=r["trigger_event"],
                channel=ChannelType(r["channel"]),
                enabled=bool(r["enabled"]),
                unisender_template_id=r["unisender_template_id"],
                telegram_body=r["telegram_body"],
            )
            for r in rows
        }
        self._expires_at = time.monotonic() + self._ttl

    async def get(self, trigger_event: str, channel: ChannelType) -> NotificationBinding | None:
        if time.monotonic() >= self._expires_at:
            await self._refresh()
        return self._cache.get((trigger_event, channel.value))

    def invalidate(self) -> None:
        self._expires_at = 0.0
```
(Confirm `ISqlExecutor.fetch_all` returns a list of mappings — match the existing `repository.py` usage. If it returns `RowMapping`s, `r["col"]` works as written.)

- [ ] **Step 5: Run it (passes)**

Run: `cd event-notifier && uv run pytest tests/adapters/test_bindings_provider.py -q`
Expected: PASS (2).

- [ ] **Step 6: Commit**

```bash
git -C event-notifier add event_notifier/domain/models/binding.py event_notifier/adapters/bindings_provider.py tests/adapters/test_bindings_provider.py
git -C event-notifier commit --no-verify -m "feat(notifier): BindingsProvider (TTL-cached notification_bindings reader)"
```

### Task 1.3: Channels + channel-selection read from bindings

**Files:**
- Modify: `event_notifier/infrastructure/channels/email.py`
- Modify: `event_notifier/infrastructure/channels/telegram.py`
- Modify: `event_notifier/application/use_cases/process_notification_command.py`
- Modify: `event_notifier/ioc.py`
- Test: `event_notifier/tests/infrastructure/test_channels_bindings.py`

- [ ] **Step 1: EmailChannel — take the UUID from the binding**

In `email.py`, change the constructor to accept `bindings: BindingsProvider` (drop `template_ids_by_locale`/`default_locale` use for selection) and rewrite `_template_id`:
```python
    async def _template_id(self, trigger_event: TriggerEvent) -> str | None:
        binding = await self._bindings.get(trigger_event.value, ChannelType.EMAIL)
        if binding is None or not binding.enabled:
            return None
        return binding.unisender_template_id
```
Make `send` `await self._template_id(trigger_event)`. Import `BindingsProvider` and `ChannelType`.

- [ ] **Step 2: TelegramChannel — render the binding's body (sandboxed)**

In `telegram.py`, accept `bindings: BindingsProvider`; replace `_render`:
```python
from jinja2.sandbox import SandboxedEnvironment

# in __init__: self._jinja = SandboxedEnvironment(autoescape=False)
    async def _render(self, trigger_event: TriggerEvent, template_data: dict[str, Any]) -> str | None:
        binding = await self._bindings.get(trigger_event.value, ChannelType.TELEGRAM)
        if binding is None or not binding.enabled or not binding.telegram_body:
            return None
        return self._jinja.from_string(binding.telegram_body).render(**template_data).strip()
```
Make `send` `await self._render(...)`. (Telegram bodies are plain text, so `autoescape=False`; the sandbox blocks unsafe attribute access.)

- [ ] **Step 3: Gate channels in the use case**

In `process_notification_command.py`, inject `bindings: BindingsProvider` into the use case ctor and, in `_resolve_contacts`, after building each candidate contact, skip channels whose binding is disabled:
```python
        # email candidate (always built today) — keep only if enabled
        if not await self._channel_enabled(command_trigger, ChannelType.EMAIL):
            contacts = []
        ...
        # telegram candidate — append only if enabled
        if user_contacts.telegram_chat_id and await self._channel_enabled(command_trigger, ChannelType.TELEGRAM):
            contacts.append(...)
```
Add a helper:
```python
    async def _channel_enabled(self, trigger_event: TriggerEvent, channel: ChannelType) -> bool:
        binding = await self._bindings.get(trigger_event.value, channel)
        return binding is not None and binding.enabled
```
`_resolve_contacts` must receive the command's `trigger_event` (thread it through from the caller, which already has `command.trigger_event`).

- [ ] **Step 4: Wire BindingsProvider in DI**

In `ioc.py`: add a provider `provide_bindings_provider(self, sql: ISqlExecutor, settings: Settings) -> BindingsProvider` returning `BindingsProvider(sql=sql, ttl_seconds=settings.bindings_cache_ttl_seconds)`; inject it into `provide_email_channel`, `provide_telegram_channel`, and the use-case provider. Add `bindings_cache_ttl_seconds: int = 30` to `config.py` Settings.

- [ ] **Step 5: Write a test for channel binding behavior**

Create `tests/infrastructure/test_channels_bindings.py`:
```python
import pytest

from event_notifier.adapters.bindings_provider import BindingsProvider
from event_notifier.domain.models.notification import ChannelType
from event_notifier.infrastructure.channels.telegram import TelegramChannel
from event_schemas.types import TriggerEvent


class _Sql:
    def __init__(self, rows): self.rows = rows
    async def fetch_all(self, *_a, **_k): return self.rows


@pytest.mark.anyio
async def test_telegram_renders_binding_body():
    rows = [{"trigger_event": "BOOKING_CREATED", "channel": "telegram", "enabled": True,
             "unisender_template_id": None, "telegram_body": "Привет, {{ client_name }}!"}]
    bindings = BindingsProvider(sql=_Sql(rows), ttl_seconds=60)
    chan = TelegramChannel(http_client=None, base_url="http://x", bot_token="t", bindings=bindings)
    text = await chan._render(TriggerEvent.BOOKING_CREATED, {"client_name": "Анна"})
    assert text == "Привет, Анна!"


@pytest.mark.anyio
async def test_telegram_skips_when_disabled():
    rows = [{"trigger_event": "BOOKING_CREATED", "channel": "telegram", "enabled": False,
             "unisender_template_id": None, "telegram_body": "x"}]
    bindings = BindingsProvider(sql=_Sql(rows), ttl_seconds=60)
    chan = TelegramChannel(http_client=None, base_url="http://x", bot_token="t", bindings=bindings)
    assert await chan._render(TriggerEvent.BOOKING_CREATED, {}) is None
```
(Adjust the `TelegramChannel(...)` ctor args to the real signature after Step 2.)

- [ ] **Step 6: Update existing channel/use-case tests + run suite**

Existing tests that constructed `EmailChannel`/`TelegramChannel` with `template_ids_by_locale`/`template_env`, or the use case without `bindings`, now need the `bindings` arg (pass a `BindingsProvider` over a fake `_Sql`). Update them. Run `cd event-notifier && uv run pytest -q` until green; `uv run ruff check .` clean.

- [ ] **Step 7: Commit**

```bash
git -C event-notifier add -A
git -C event-notifier commit --no-verify -m "feat(notifier): channels + selection read templates/enablement from bindings"
```

---

# Phase 2 — Notifier admin API

### Task 2.1: Dishka-FastAPI integration + admin auth dependency

**Files:**
- Modify: `event_notifier/main.py`
- Create: `event_notifier/admin_auth.py`
- Modify: `event_notifier/config.py` (`notifier_admin_token`)

- [ ] **Step 1: Add the admin token setting**

In `config.py` add `notifier_admin_token: str = Field(strict=True)` and `unisender_template_list_ttl_seconds: int = 3600`.

- [ ] **Step 2: Integrate Dishka with the FastAPI app**

In `main.py`, after building the app and container, call `from dishka.integrations.fastapi import setup_dishka; setup_dishka(container, app)` (the container is already created for the consumer in the lifespan — expose it so HTTP routes resolve providers). Use `dishka.integrations.fastapi.DishkaRoute` for the admin router (as event-admin does).

- [ ] **Step 3: Admin token dependency**

Create `event_notifier/admin_auth.py`:
```python
import hmac

from fastapi import Depends, Header, HTTPException, status
from dishka.integrations.fastapi import FromDishka

from event_notifier.config import Settings


async def require_admin_token(
    settings: FromDishka[Settings],
    authorization: str = Header(default=""),
) -> None:
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(token, settings.notifier_admin_token):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid notifier admin token")
```

- [ ] **Step 4: Commit**

```bash
git -C event-notifier add -A
git -C event-notifier commit --no-verify -m "feat(notifier): Dishka-FastAPI wiring + admin-token auth dependency"
```

### Task 2.2: Config GET/PUT + telegram preview + UniSender list endpoints

**Files:**
- Create: `event_notifier/routes_admin.py`
- Create: `event_notifier/adapters/unisender_templates.py` (cached list client)
- Modify: `event_notifier/db/repository.py` (binding read/upsert)
- Modify: `event_notifier/main.py` (include the admin router)
- Test: `event_notifier/tests/test_routes_admin.py`

- [ ] **Step 1: Repository binding read/upsert**

Add to `repository.py`:
```python
    async def list_bindings(self) -> list[dict]:
        return await self._sql.fetch_all(
            text("SELECT trigger_event, channel, enabled, unisender_template_id, telegram_body, updated_at "
                 "FROM notification_bindings ORDER BY trigger_event, channel"),
        )

    async def upsert_binding(self, *, trigger_event, channel, enabled, unisender_template_id, telegram_body) -> None:
        await self._sql.execute(
            text(
                "INSERT INTO notification_bindings "
                "(trigger_event, channel, enabled, unisender_template_id, telegram_body, updated_at) "
                "VALUES (:t, :c, :en, :uid, :tb, now()) "
                "ON CONFLICT (trigger_event, channel) DO UPDATE SET "
                "enabled = excluded.enabled, unisender_template_id = excluded.unisender_template_id, "
                "telegram_body = excluded.telegram_body, updated_at = now()"
            ),
            {"t": trigger_event, "c": channel, "en": enabled, "uid": unisender_template_id, "tb": telegram_body},
        )
```

- [ ] **Step 2: Cached UniSender list client**

Create `adapters/unisender_templates.py`:
```python
import time

import httpx


class UnisenderTemplateList:
    """In-memory TTL cache of UniSender Go templates (id + name)."""

    def __init__(self, *, http_client: httpx.AsyncClient, base_url: str, api_key: str, ttl_seconds: int = 3600) -> None:
        self._client = http_client
        self._url = f"{base_url.rstrip('/')}/ru/transactional/api/v1/template/list.json"
        self._api_key = api_key
        self._ttl = ttl_seconds
        self._cache: list[dict] | None = None
        self._expires_at = 0.0

    async def get(self, *, refresh: bool = False) -> list[dict]:
        if refresh or self._cache is None or time.monotonic() >= self._expires_at:
            resp = await self._client.post(self._url, headers={"X-API-KEY": self._api_key}, json={"limit": 100, "offset": 0})
            resp.raise_for_status()
            body = resp.json()
            templates = body.get("templates", body.get("data", []))
            self._cache = [{"id": str(t.get("id")), "name": t.get("title") or t.get("name") or str(t.get("id"))} for t in templates]
            self._expires_at = time.monotonic() + self._ttl
        return self._cache
```
(Confirm the response shape against the UniSender docs — `templates` array with `id`/`title`. Adjust the key names if the live shape differs; the endpoint is `POST .../template/list.json` with `X-API-KEY`.)

- [ ] **Step 3: Write the admin router + schemas**

Create `routes_admin.py` with a `DishkaRoute` router, `dependencies=[Depends(require_admin_token)]`, prefix `/api/notifications`:
```python
from typing import Any

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, HTTPException
from jinja2 import TemplateError
from jinja2.sandbox import SandboxedEnvironment
from pydantic import BaseModel

from event_notifier.admin_auth import require_admin_token
from event_notifier.adapters.bindings_provider import BindingsProvider
from event_notifier.adapters.unisender_templates import UnisenderTemplateList
from event_notifier.db.repository import NotificationRepository

router = APIRouter(prefix="/api/notifications", route_class=DishkaRoute, dependencies=[Depends(require_admin_token)])

_CHANNELS = {"email", "telegram"}
_SAMPLE = {"client_name": "Иван", "organizer_name": "Пётр", "start_time_local": "15 июн 13:00",
           "end_time_local": "15 июн 14:00", "time_zone": "Europe/Moscow", "meeting_url": "https://example/x"}


class BindingIn(BaseModel):
    enabled: bool
    unisender_template_id: str | None = None
    telegram_body: str | None = None


class PreviewIn(BaseModel):
    telegram_body: str
    sample_data: dict[str, Any] | None = None


@router.get("/config")
async def get_config(repo: FromDishka[NotificationRepository]) -> dict[str, Any]:
    return {"bindings": await repo.list_bindings()}


@router.put("/config/{trigger_event}/{channel}")
async def put_config(trigger_event: str, channel: str, body: BindingIn,
                     repo: FromDishka[NotificationRepository], bindings: FromDishka[BindingsProvider]) -> dict[str, str]:
    if channel not in _CHANNELS:
        raise HTTPException(status_code=400, detail="unknown channel")
    if channel == "telegram" and body.telegram_body:
        try:
            SandboxedEnvironment(autoescape=False).from_string(body.telegram_body)
        except TemplateError as exc:
            raise HTTPException(status_code=400, detail=f"invalid jinja: {exc}") from exc
    await repo.upsert_binding(trigger_event=trigger_event, channel=channel, enabled=body.enabled,
                              unisender_template_id=body.unisender_template_id, telegram_body=body.telegram_body)
    bindings.invalidate()
    return {"status": "ok"}


@router.get("/unisender-templates")
async def unisender_templates(templates: FromDishka[UnisenderTemplateList], refresh: bool = False) -> dict[str, Any]:
    return {"templates": await templates.get(refresh=refresh)}


@router.post("/telegram/preview")
async def telegram_preview(body: PreviewIn) -> dict[str, str]:
    try:
        rendered = SandboxedEnvironment(autoescape=False).from_string(body.telegram_body).render(**(body.sample_data or _SAMPLE))
    except TemplateError as exc:
        raise HTTPException(status_code=400, detail=f"render error: {exc}") from exc
    return {"rendered": rendered.strip()}
```

- [ ] **Step 4: DI providers + include router**

In `ioc.py` add `provide_unisender_template_list` (httpx client + settings). In `main.py`, `app.include_router(router)` from `routes_admin`.

- [ ] **Step 5: Write the route tests**

Create `tests/test_routes_admin.py` using FastAPI `TestClient`/httpx `ASGITransport` with a Dishka container whose providers are faked (fake repo + fake bindings + a stub UnisenderTemplateList). Cover: 401 without token; `GET /config` returns rows; `PUT` invalid jinja → 400; `PUT` valid telegram → 200 + invalidate called; `POST /telegram/preview` renders sample; `GET /unisender-templates` returns the stub list. Follow the existing `tests/test_ioc.py` container-build pattern for fakes.

- [ ] **Step 6: Run + lint + commit**

Run: `cd event-notifier && uv run pytest -q && uv run ruff check .`
```bash
git -C event-notifier add -A
git -C event-notifier commit --no-verify -m "feat(notifier): admin API — config CRUD, UniSender list cache, telegram preview"
git -C event-notifier push origin main
```

---

# Phase 3 — event-admin proxy

### Task 3.1: INotifierClient + /api/notifications proxy routes

**Files:**
- Create: `event-admin/event_admin/adapters/notifier_client.py`
- Create: `event-admin/event_admin/interfaces/notifier.py`
- Modify: `event-admin/event_admin/config.py` (`notifier_service_url`, `notifier_admin_token`)
- Modify: `event-admin/event_admin/ioc.py` (provide the client)
- Modify: `event-admin/event_admin/routes.py` (proxy router)
- Test: `event-admin/tests/test_notifications_proxy.py`

- [ ] **Step 1: Config**

In `config.py` add `notifier_service_url: AnyHttpUrl = Field(strict=True)` and `notifier_admin_token: str = Field(strict=True)`.

- [ ] **Step 2: Interface + client**

Create `interfaces/notifier.py` (Protocol with `get_config`, `put_config`, `unisender_templates`, `telegram_preview`). Create `adapters/notifier_client.py` mirroring `users_client.py` (httpx `AsyncClient` + `Authorization: Bearer <notifier_admin_token>`), each method calling the corresponding notifier endpoint and returning the parsed JSON; raise `httpx.HTTPStatusError` on non-2xx (handled by a `_notifier_proxy_error` mapper like `_users_proxy_error`).

- [ ] **Step 3: Proxy routes**

In `routes.py` add `notifications_router = APIRouter(prefix="/api/notifications", route_class=DishkaRoute, dependencies=[Depends(require_admin)])` with: `GET /config`, `PUT /config/{trigger_event}/{channel}`, `GET /unisender-templates`, `POST /telegram/preview` — each delegating to `INotifierClient` and mapping errors. Register the router in `main.py` (where the other routers are included).

- [ ] **Step 4: DI**

In `ioc.py` provide `INotifierClient` (app-scoped `AsyncClient` with `base_url=notifier_service_url` + token), mirroring the `IUsersClient` provider.

- [ ] **Step 5: Tests**

Create `tests/test_notifications_proxy.py` using the existing `create_app(settings, provider=FakeProvider(...))` test harness with a fake `INotifierClient`; assert: 401 without a valid JWT (require_admin), 200 pass-through for `GET /config`, error mapping when the fake raises `HTTPStatusError`.

- [ ] **Step 6: Run + commit + push**

Run: `cd event-admin && uv run pytest -q && uv run ruff check .`
```bash
git -C event-admin add -A
git -C event-admin commit --no-verify -m "feat(admin): proxy /api/notifications/* to event-notifier (require_admin)"
git -C event-admin push origin main
```

### Task 3.2: compose + nginx env

**Files:**
- Modify: `docker-compose.yml` (event-admin env + event-notifier env)
- Modify: `event-admin-frontend/nginx.conf` (confirm `/api/` covers `/api/notifications/`)
- Modify: `.env.example`

- [ ] **Step 1: Env**

Add to `event-admin` environment: `NOTIFIER_SERVICE_URL: http://event-notifier:8888`, `NOTIFIER_ADMIN_TOKEN: ${NOTIFIER_ADMIN_TOKEN:-dev-notifier-admin-7c2f...}`. Add to `event-notifier` environment: `NOTIFIER_ADMIN_TOKEN: ${NOTIFIER_ADMIN_TOKEN:-dev-notifier-admin-7c2f...}` (same value), `BINDINGS_CACHE_TTL_SECONDS: ${BINDINGS_CACHE_TTL_SECONDS:-30}`. Add the knobs to `.env.example`.

- [ ] **Step 2: nginx**

Confirm `event-admin-frontend/nginx.conf` `location /api/` proxies to event-admin (it does); `/api/notifications/` is covered by that prefix — no change needed unless `/api/notifications` needs distinct handling (it does not).

- [ ] **Step 3: Verify config loads**

Run: `docker compose up -d --build event-notifier event-admin && docker compose ps event-notifier event-admin`
Expected: both healthy (the new required `notifier_admin_token` is set via env).

- [ ] **Step 4: Commit (root repo)**

```bash
git add docker-compose.yml .env.example
git commit --no-verify -m "feat(deploy): wire NOTIFIER_ADMIN_TOKEN + notifier service url for the admin proxy"
git push origin main
```

---

# Phase 4 — Frontend "Уведомления" module

### Task 4.1: Notifications module (matrix + telegram editor)

**Files:**
- Create: `event-admin-frontend/src/modules/notifications/notificationsApi.ts`
- Create: `event-admin-frontend/src/modules/notifications/NotificationsPage.tsx`
- Create: `event-admin-frontend/src/modules/notifications/NotificationsPage.test.tsx`
- Modify: `event-admin-frontend/src/modules/shared/routing.ts` (add `/notifications` route)
- Modify: `event-admin-frontend/src/App.tsx` (route → page)
- Modify: `event-admin-frontend/src/modules/app/AdminLayout.tsx` (nav item)

- [ ] **Step 1: API client**

Create `notificationsApi.ts` with `apiRequest`-based functions: `getConfig()` → `{bindings: Binding[]}`, `putBinding(trigger, channel, body)`, `getUnisenderTemplates(refresh?)` → `{templates: {id,name}[]}`, `previewTelegram(body, sample?)` → `{rendered}`. Define the `Binding`/`UnisenderTemplate` types.

- [ ] **Step 2: Route + nav + App wiring**

Add `'/notifications'` to the `AppRoute` union + `parseRoute` (returns `{ name: 'notifications' }`); add a nav item `{ label: 'Уведомления', path: '/notifications' }` in `AdminLayout.tsx`; render `<NotificationsPage />` for that route in `App.tsx`.

- [ ] **Step 3: NotificationsPage (matrix)**

Build the matrix: rows = the 7 `TriggerEvent`s, columns = email + telegram. Each cell: an enabled checkbox; email → a `<select>` populated from `getUnisenderTemplates()` + an "Обновить" button (calls `getUnisenderTemplates(true)`); telegram → an inline `<textarea>` for the body + a "Предпросмотр" button (calls `previewTelegram`, shows the rendered text or error). "Сохранить" per row calls `putBinding`. Use the existing `apiRequest` error handling.

- [ ] **Step 4: vitest**

Create `NotificationsPage.test.tsx` (happy-dom) mocking `notificationsApi`: renders the matrix from a fake config; toggling enabled + clicking save calls `putBinding` with the right args; "Предпросмотр" shows the rendered text. Keep it focused.

- [ ] **Step 5: Build + test + commit + push**

Run: `cd event-admin-frontend && npm run test && npm run build`
```bash
git -C event-admin-frontend add -A
git -C event-admin-frontend commit --no-verify -m "feat(admin-ui): Уведомления — manage channel/template per event"
git -C event-admin-frontend push origin main
```

---

# Phase 5 — Docs

### Task 5.1: Service + cross-service docs

**Files:**
- Modify: `event-notifier/docs/API_CONTRACTS.md`, `event-notifier/docs/SERVICE_OVERVIEW.md`, `event-notifier/CLAUDE.md`
- Modify: `event-admin/docs/API_CONTRACTS.md`, `event-admin/CLAUDE.md`
- Modify: `docs/architecture/ONBOARDING.md`

- [ ] **Step 1** Document: the `notification_bindings` model + seed; runtime read (TTL) + channel-enablement semantics (enabled AND contact); the notifier admin API (endpoints, service-token auth); the event-admin `/api/notifications/*` proxy; the frontend "Уведомления" page; the env knobs (`NOTIFIER_ADMIN_TOKEN`, `BINDINGS_CACHE_TTL_SECONDS`). Note v1 single-locale + out-of-scope items.
- [ ] **Step 2** Commit + push each repo's docs (notifier, admin in their repos; ONBOARDING in root).

---

## Self-Review notes

- **Spec coverage:** Phase 1 ↔ data model + runtime (bindings table/seed, BindingsProvider TTL, channels + selection from DB, sandboxed Jinja); Phase 2 ↔ notifier admin API (config CRUD, cached UniSender list, telegram preview, service-token auth, Dishka-FastAPI); Phase 3 ↔ event-admin proxy + env; Phase 4 ↔ frontend matrix; Phase 5 ↔ docs. The "enabled AND contact" semantics, in-memory TTL, inline telegram body, seed-from-env/.j2, and UniSender in-memory cache + refresh all map to tasks.
- **Placeholder scan:** dev token values and the UniSender response key names are flagged as "confirm against the live shape" — those are real verification steps, not unfilled blanks; all code blocks are complete. The down_revision must be set from Task 1.1 Step 1 (explicit instruction).
- **Consistency:** `NotificationBinding`, `BindingsProvider.get(trigger_event, channel)`/`.invalidate()`, `notification_bindings(trigger_event, channel, enabled, unisender_template_id, telegram_body)`, `/api/notifications/{config,unisender-templates,telegram/preview}`, `NOTIFIER_ADMIN_TOKEN`, `bindings_cache_ttl_seconds` are used identically across tasks and match the spec.
- **Confirm during execution:** notifier's `ISqlExecutor.fetch_all` return shape (RowMapping vs dict) for `r["col"]`; the exact `002` revision id; the real `EmailChannel`/`TelegramChannel` ctor signatures when swapping in `bindings`; whether notifier's lifespan exposes the Dishka container for `setup_dishka`; UniSender `template/list.json` response keys.
