# Per-Role Notification Bindings — Design

**Date:** 2026-06-15
**Status:** Approved (brainstorm)
**Builds on:** `2026-06-15-manageable-notification-templates-design.md`

## Problem

Notification templates are role-specific in reality — UniSender has separate
templates "Новая запись. **Волонтер**" vs "Новая запись. **Клиент**", and the
same Клиент/Волонтер split exists for rescheduled and cancelled events. But
`notification_bindings` has primary key `(trigger_event, channel)`, so a single
template/body serves **every** recipient of a trigger. The organizer and the
client therefore receive identical email/Telegram content. We need template +
enablement bound per `(trigger_event, recipient_role, channel)`.

## Key Insight

`recipient_role` is **already threaded end-to-end** and requires no new plumbing:

- `RecipientRole` is a canonical `StrEnum` in `event-schemas`
  (`event_schemas/types.py`): exactly `ORGANIZER = "organizer"` and
  `CLIENT = "client"`. event-notifier already imports from `event-schemas`.
- At command time: `CommandRecipient.role` (set by the producer).
- At resolution: `ChannelContact.role` is built from `recipient.role`.
- At send time: `EmailChannel.send` / `TelegramChannel.send` receive
  `contact.role` — and currently **ignore it** when calling
  `bindings.get(trigger_event, channel)`.
- In the outbox: `recipient_role` is already a stored column.

So this is a **keying change**: add the `recipient_role` dimension to the
bindings table and every lookup that consults it. No new event fields, no
outbox-schema change, no producer change.

## Roles

Fixed two-value set from `RecipientRole`: `client`, `organizer`. The admin UI
and the `PUT` validation enumerate exactly these; anything else is a 400.

## Components & Changes

### 1. Schema — event-notifier Alembic migration `004`

- `revision = "004"`, `down_revision = "003"`.
- Add column `recipient_role TEXT NOT NULL` to `notification_bindings`.
- Change primary key from `(trigger_event, channel)` to
  `(trigger_event, recipient_role, channel)`.
- **Migrate existing rows by expand-in-place** so current edits survive:
  1. `ALTER TABLE notification_bindings ADD COLUMN recipient_role TEXT NOT NULL DEFAULT 'client'`
     (existing 14 rows become the `client` rows).
  2. `INSERT INTO notification_bindings (trigger_event, recipient_role, channel,
     enabled, unisender_template_id, telegram_body, updated_at)
     SELECT trigger_event, 'organizer', channel, enabled, unisender_template_id,
     telegram_body, now() FROM notification_bindings WHERE recipient_role = 'client'`
     (clones every row for the organizer with identical values).
  3. Drop the old PK constraint `pk_notification_bindings`; add the new
     three-column PK with the same name.
  4. Drop the column server-default (writes are always explicit thereafter).
- Result: 28 rows (7 triggers × 2 roles × 2 channels). Both roles start with the
  **same** template/body; the operator differentiates per role in the UI.
- `downgrade()`: reverse — collapse to `client` rows, drop the column, restore
  the two-column PK. (Lossy for organizer-specific values, acceptable for a
  dev-managed table.)

### 2. Notifier runtime (mechanical fan-out of the key)

- `domain/models/binding.py` — `NotificationBinding` gains
  `recipient_role: str` (first field after `trigger_event`).
- `adapters/bindings_provider.py`:
  - `_QUERY` selects `recipient_role`.
  - Cache key becomes `(trigger_event, recipient_role, channel)`.
  - `get(self, trigger_event: str, recipient_role: str, channel: ChannelType)`.
- `infrastructure/channels/email.py` — `_template_id(trigger_event, recipient_role)`;
  `send()` passes `contact.role`.
- `infrastructure/channels/telegram.py` — `_render(trigger_event, recipient_role, template_data)`;
  `send()` passes `contact.role`.
- `application/use_cases/process_notification_command.py` —
  `_channel_enabled(trigger_event, recipient_role, channel)`; `_resolve_contacts`
  passes `recipient.role` (already in scope) into both `_channel_enabled` calls.
- `db/repository.py`:
  - `list_bindings()` selects and returns `recipient_role`.
  - `upsert_binding(*, trigger_event, recipient_role, channel, enabled,
    unisender_template_id, telegram_body)` with
    `ON CONFLICT (trigger_event, recipient_role, channel)`.

### 3. Notifier admin API (`routes_admin.py`)

- New path: `PUT /api/notifications/config/{trigger_event}/{recipient_role}/{channel}`.
  - Validate `recipient_role in {"client", "organizer"}` → 400 `unknown role` otherwise.
  - Existing channel + Jinja validation unchanged; still calls `bindings.invalidate()`.
- `GET /api/notifications/config` — each binding dict now includes
  `recipient_role` (comes for free from `list_bindings()`).
- `GET /unisender-templates` and `POST /telegram/preview` unchanged.

### 4. event-admin proxy

- `interfaces/notifier.py` `INotifierClient.put_config` gains a
  `recipient_role: str` parameter.
- `adapters/notifier_client.py` `NotifierClient.put_config` forwards to
  `/api/notifications/config/{trigger_event}/{recipient_role}/{channel}`.
- `routes.py` notifications proxy: the PUT route becomes
  `/api/notifications/config/{trigger_event}/{recipient_role}/{channel}` under
  `require_admin`, calling the updated client method. `GET /config` unchanged.

### 5. Frontend — "Уведомления" page (role tabs over the matrix)

- `notificationsApi.ts`: the `Binding` type gains `recipient_role`; `putConfig`
  takes `recipientRole` and targets `…/config/{trigger}/{role}/{channel}`.
- `NotificationsPage.tsx`:
  - Add role tab state: `[ Клиент | Волонтёр ]` (`client` | `organizer`),
    default `client`.
  - Fetch `/config` once (28 rows); filter to the active role for the grid.
  - The existing 7×(email/telegram) grid renders the active role's rows:
    enabled toggle, UniSender `<select>` + "Обновить", telegram `<textarea>` +
    "Предпросмотр", per-row "Сохранить" → `putConfig(trigger, role, channel, …)`.
  - Switching tabs re-filters the already-fetched config (no refetch needed;
    refetch after a successful save to stay consistent).
- Test updated for tab switching and role-scoped PUT path.

### 6. Docs & memory

- event-notifier `CLAUDE.md`: bindings PK, `BindingsProvider.get` signature,
  admin PUT path now include `recipient_role`.
- event-notifier `docs/API_CONTRACTS.md`: new PUT path + `recipient_role` column.
- event-admin `CLAUDE.md` Notifications-proxy note: PUT path gains role segment.
- `docs/architecture/MESSAGE_CONTRACTS.md`: only if it documents binding keys.
- Update the `project-manageable-notifications` memory with the role dimension.

## Data Flow (unchanged except the lookup key)

```
command.recipients[].role ─┐
                           ▼
ProcessNotificationCommandUseCase._resolve_contacts(recipient, trigger)
   _channel_enabled(trigger, recipient.role, channel)  ── BindingsProvider.get(trigger, role, channel)
   → ChannelContact(role=recipient.role)
                           ▼   (outbox row carries recipient_role, as today)
OutboxSender → EmailChannel/TelegramChannel.send(contact, trigger, data)
   _template_id/_render(trigger, contact.role)  ── BindingsProvider.get(trigger, role, channel)
                           ▼
   UniSender template_id / Telegram body chosen PER ROLE
```

## Error Handling

- `PUT` with an unknown `recipient_role` → 400 (mirrors the unknown-channel 400).
- No binding for a resolved `(trigger, role, channel)` → unchanged behavior:
  permanent `DeliveryResult(success=False, retryable=False, "No … template
  configured")`. (Producers only send to roles that are actual recipients, so an
  inert organizer row for a client-only trigger is never consulted.)
- Migration runs inside Alembic's transaction; the expand-then-repk sequence is
  atomic.

## Backward Compatibility

- Outbox schema untouched; outbox rows written before the migration already carry
  `recipient_role`, so they resolve correctly against the new three-key lookup at
  send time.
- `GET /config` is additive (extra field); existing callers tolerate it.

## Testing

- **Notifier unit:** BindingsProvider three-key cache + `get`; Email/Telegram
  channel select by role; use-case passes `recipient.role`; repository
  `upsert_binding`/`list_bindings` round-trip with role; admin API role path +
  unknown-role 400.
- **Migration:** apply `004` on a DB seeded by `003`; assert 28 rows, organizer
  rows mirror client rows, three-column PK present.
- **event-admin:** proxy `put_config` builds the role path (FakeProvider, no net).
- **Frontend:** tab switch filters rows; "Сохранить" issues PUT to
  `…/{role}/{channel}`.

## Out of Scope (YAGNI)

- Per-locale management (still single-locale, seeded from `DEFAULT_LOCALE`).
- Push channel.
- Role-aware `UNISENDER_TEMPLATE_IDS` env format — real templates are assigned in
  the UI; the migration just clones existing values as a starting point.
