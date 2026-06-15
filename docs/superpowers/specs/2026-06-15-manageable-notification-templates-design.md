# Manageable Notification Templates (Event → Channel → Template) — Design

**Date:** 2026-06-15
**Status:** Approved

## Goal

Let an admin manage, from the admin UI, **which channels fire for each notification event and
which template each channel uses** — and edit Telegram message templates in place. Email
templates are picked from the live (cached) UniSender Go template list. Today this is static:
channel selection is hard-coded (`_resolve_contacts`: email always, telegram if a contact exists),
email template UUIDs come from the `UNISENDER_TEMPLATE_IDS` env var, and Telegram bodies are Jinja
files baked into the event-notifier image. This makes all of it data-driven and admin-editable.

## Decisions (interview 2026-06-15)

| Topic | Decision |
|---|---|
| Datastore owner | **event-notifier owns it** (pg-notifier tables + a new admin-authed API); **event-admin proxies** (a new `INotifierClient`, mirroring the `IUsersClient` event-users proxy) |
| Locale dimension | **v1 single-locale** — `(trigger_event, channel) → one template` (the default locale). Per-locale management is deferred (accepted regression: `en` templates stop being selected until then) |
| UniSender template list | **In-memory TTL cache (~1h) + manual "Refresh"**; the email template is a UUID chosen from that list |
| Telegram template storage | **Inline Jinja body per binding** (no separate named-template library in v1) |
| Notifier admin API auth | Static **service token** (`NOTIFIER_ADMIN_TOKEN`), like `BLACKLIST_SERVICE_TOKEN`; event-admin authenticates the human via `require_admin` then calls notifier with the token |

## Current state

- **event-notifier** has a FastAPI app (only `/health`, `/metrics` today) and owns **pg-notifier**
  (alembic lives there). Channels are wired in `ioc.py` `provide_outbox_sender`
  (`{EMAIL: EmailChannel, TELEGRAM: TelegramChannel}`); `EmailChannel` reads
  `settings.unisender_template_ids_by_locale()`, `TelegramChannel` reads a `FileSystemLoader`
  Jinja `Environment` over `event_notifier/templates/<locale>/telegram/<TRIGGER>.j2`.
- Channel-per-recipient is decided in
  `application/use_cases/process_notification_command.py` `_resolve_contacts` (email always;
  telegram only if event-users returns a `telegram_chat_id`).
- Template selection is `(trigger_event, locale)` keyed inside each channel
  (`email._template_id`, `telegram._render`), with locale fallback to default.
- **event-admin** is the UI gateway: read-only over event-saver's DB, owns `admin_users` +
  `blacklist_entries` (direct writes), **proxies** `/api/users/*` to event-users via `IUsersClient`
  (httpx + a static service token), all routes gated by `require_admin`. Served same-origin
  behind the `event-admin-frontend` nginx (which proxies `/api`, `/auth`, `/bookings`).

## Components

### 1. Data model (pg-notifier, alembic in event-notifier)

New table **`notification_bindings`** — one row per `(trigger_event, channel)`:

| column | type | notes |
|---|---|---|
| `trigger_event` | text | one of `TriggerEvent` (event-schemas) |
| `channel` | text | `email` / `telegram` |
| `enabled` | boolean | whether this channel fires for this event |
| `unisender_template_id` | text NULL | UniSender template UUID (email channel) |
| `telegram_body` | text NULL | Jinja source (telegram channel) |
| `updated_at` | timestamptz | |
| PK | `(trigger_event, channel)` | |

**Seed (in the migration):** rows are populated from the current `UNISENDER_TEMPLATE_IDS`
(default-locale set) and the repo's `templates/<default_locale>/telegram/<TRIGGER>.j2` files, so
runtime behavior is unchanged on first deploy. The repo `.j2` files remain in git as the seed
source of truth for the migration; the DB is authoritative at runtime afterward.

### 2. event-notifier runtime

- A `BindingsProvider` reads `notification_bindings` with a short **in-memory TTL cache
  (configurable, default 30s)** so admin edits apply within ~the TTL without a restart.
- **Channel selection** (`_resolve_contacts` / the use case): a channel is used for a trigger only
  when its binding is `enabled` **AND** the recipient has a contact for it. (So the admin can turn
  telegram off for `BOOKING_REMINDER` even when a chat_id exists.)
- **EmailChannel**: takes the `unisender_template_id` from the binding (not env). Missing/disabled
  → that recipient's email is skipped (same permanent-failure semantics as a missing template today).
- **TelegramChannel**: renders `telegram_body` from the binding using a **`SandboxedEnvironment`**
  (`jinja2.sandbox`) rendered from string. Missing/disabled → skipped.
- The env `UNISENDER_TEMPLATE_IDS` and repo `.j2` files become **seed-only**; runtime reads the DB.

### 3. event-notifier admin API (new router, service-token auth)

A router gated by a static `NOTIFIER_ADMIN_TOKEN` (`Authorization: Bearer <token>`):

- `GET  /api/notifications/config` — the full matrix: every `TriggerEvent` × `{email, telegram}`
  with `enabled` + the bound template (UUID or `telegram_body`).
- `PUT  /api/notifications/config/{trigger_event}/{channel}` — upsert `enabled` +
  `unisender_template_id` (email) or `telegram_body` (telegram). Validates: trigger/channel are
  known; for telegram, the body **compiles** (Jinja parse) before save.
- `GET  /api/notifications/unisender-templates[?refresh=true]` — the cached UniSender list
  (`POST .../template/list.json` with the notifier's UniSender key; in-memory TTL ~1h;
  `refresh=true` forces a refetch). Returns `[{id, name}]`.
- `POST /api/notifications/telegram/preview` — body `{telegram_body, sample_data?}` → compiles and
  renders against representative `template_data` keys; returns rendered text or a Jinja error.

The UniSender list call reuses the notifier's existing UniSender HTTP client/config.

### 4. event-admin proxy

- `INotifierClient` (httpx app-scoped client + the `NOTIFIER_ADMIN_TOKEN`), mirroring `IUsersClient`.
- A `/api/notifications/*` router gated by `require_admin` that forwards to event-notifier and maps
  upstream errors (like `_users_proxy_error`). New settings:
  `NOTIFIER_SERVICE_URL`, `NOTIFIER_ADMIN_TOKEN`.
- nginx (`event-admin-frontend/nginx.conf`): add `location /api/notifications/` → proxied to
  event-admin (the existing `/api/` location already covers it — confirm the prefix match;
  `/api/notifications/` falls under `location /api/`).

### 5. event-admin-frontend — "Уведомления" module

A new page (sidebar entry "Уведомления"): a matrix with **events as rows** (`TriggerEvent`) and
**channels as columns** (email, telegram). Per cell:
- an **enabled** toggle;
- email → a **dropdown** populated from the cached UniSender list + an **"Обновить"** button;
- telegram → an inline/modal **Jinja editor** for the body + a **"Предпросмотр"** button (renders
  via the preview endpoint).
All calls go through event-admin (`/api/notifications/*`), same-origin. Follows the existing
module/`apiRequest` patterns; vitest for the API mapping + gating.

## Config / env

- event-notifier: `NOTIFIER_ADMIN_TOKEN` (admin API auth), `BINDINGS_CACHE_TTL_SECONDS` (default 30),
  `UNISENDER_TEMPLATE_LIST_TTL_SECONDS` (default 3600). Existing `UNISENDER_*` reused for the list call.
- event-admin: `NOTIFIER_SERVICE_URL` (`http://event-notifier:8888`), `NOTIFIER_ADMIN_TOKEN` (must
  match notifier's). All from Vault/ESO in k8s; dev defaults in docker-compose.

## Testing

- event-notifier: `BindingsProvider` (cache + DB read), channel selection honoring `enabled`,
  `EmailChannel`/`TelegramChannel` reading from bindings, telegram render-from-string (sandbox),
  UniSender list cache (TTL + refresh), the admin API endpoints (config CRUD, preview validation).
  Alembic migration + seed. `TestRabbitBroker`/fake SQL patterns as today.
- event-admin: proxy routes (auth gate, error mapping) with a fake `INotifierClient`.
- frontend: vitest for the module's API mapping + the enabled/preview interactions.

## Phased implementation

1. **Notifier data model + runtime** — `notification_bindings` migration + seed, `BindingsProvider`
   (TTL cache), channel selection + Email/Telegram channels reading from bindings (sandboxed Jinja).
2. **Notifier admin API** — config GET/PUT, UniSender list (cached) endpoint, telegram preview;
   service-token auth.
3. **event-admin proxy** — `INotifierClient`, `/api/notifications/*` routes, settings, nginx/compose env.
4. **Frontend** — the "Уведомления" matrix module (toggles, UniSender dropdown + refresh, telegram
   editor + preview).
5. **Docs** — event-notifier + event-admin `docs/` (API_CONTRACTS, SERVICE_OVERVIEW), ONBOARDING.

## Verification

In the admin UI: toggle a channel for an event, pick a UniSender template for email, edit + preview a
Telegram body, save. Run `scripts/calcom_sim.py lifecycle`; confirm the chosen channels fire with the
configured templates (notifier outbox + the booking detail's email/telegram sections), and that a
disabled channel does NOT fire. Editing the Telegram body changes the delivered text within the cache
TTL without a restart.

## Out of scope (v1)

- Per-locale management (the `en` set); reusable named Telegram templates (body is inline);
  the push channel; template version history / audit; a WYSIWYG editor; editing UniSender template
  bodies (only selection — bodies live in UniSender).
