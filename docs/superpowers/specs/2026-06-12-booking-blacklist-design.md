# Booking Blacklist — Design

**Date:** 2026-06-12
**Status:** Approved

## Goal

Reject bookings whose client matches a blacklist entry, alongside the existing
`analyze_on_create` constraints in event-booking. Admins manage the blacklist in the admin
panel. Today only `client_email` is checked; the model must allow new check-fields later
without schema changes.

## Decisions (interview 2026-06-12)

| Question | Decision |
|---|---|
| Storage / read path | Blacklist lives in the main DB; event-booking reads via **event-admin HTTP API with a long-lived in-memory cache** (no new DB connection in booking) |
| API unavailable at check time | **Fail-open**: log an error, treat the email as not blacklisted; stale cache (if any) is used in preference to failing open |
| Entry activity | `is_active = true` **AND** now within `[active_from, active_until]` (NULL bound = unbounded) |
| Matching | Exact, case-insensitive (values stored lowercased) |
| Client notification | **Notify with a dedicated template** — new trigger `BOOKING_REJECTED_BLACKLISTED`, separate email/telegram templates (not the generic rejection text) |

## Data model (main DB; migration owned by event-saver)

`blacklist_entries`:
`id uuid PK, field text NOT NULL, value text NOT NULL, is_active bool NOT NULL DEFAULT true,
active_from timestamptz NULL, active_until timestamptz NULL, comment text NULL,
created_by text NOT NULL, created_at timestamptz NOT NULL DEFAULT now(),
updated_at timestamptz NOT NULL DEFAULT now()`
Index `(field, lower(value))`. ORM model added for the autogenerate drift-guard.
`field` is an open string (`client_email` today); new check-fields are new values.

## event-admin

- CRUD under admin JWT, structured `{code, message}` errors, pagination + filters
  (field, value substring, only-effective):
  `GET /api/blacklist`, `POST /api/blacklist`, `PATCH /api/blacklist/{id}`,
  `DELETE /api/blacklist/{id}`.
- Service endpoint for event-booking: `GET /api/blacklist/active?field=client_email` —
  returns currently-effective entries (flag + window evaluated in SQL); authenticated by a
  static service token (constant-time compare), not admin JWT.
- This service writes the main DB here (same sanctioned exception as `admin_users`).

## event-booking

- `BlacklistClient` adapter (httpx, explicit timeout) + in-memory full-list cache with TTL
  (`BLACKLIST_CACHE_TTL`, default 300 s; long-lived is acceptable). Refresh on expiry; on
  refresh failure keep serving stale cache and log; with no cache and API down — fail-open
  with an error log.
- Check runs in the `booking.created` handler before `analyze_on_create`. Match →
  rejection flow: booking cancelled in cal.com (status `rejected`), `booking.rejected`
  published with `rejection_type='blacklisted'`, and `notification.send_requested`
  published with `trigger_event=BOOKING_REJECTED_BLACKLISTED`.

## event-schemas / event-notifier

- New `TriggerEvent.BOOKING_REJECTED_BLACKLISTED` + `rejection_type` docs mention
  `blacklisted`.
- Notifier: locale templates (`ru`/`en`, telegram) for the new trigger; UniSender template
  id entry per locale in `UNISENDER_TEMPLATE_IDS` (compose/.env.example get a dev UUID
  + WireMock keeps accepting it).

## event-admin-frontend

New «Чёрный список» page: table (field, value, активность, окно действия, комментарий,
автор, обновлено), add/edit modal, delete confirmation, active toggle, filters. Errors by
machine-readable code.

## Verification

Unit tests in every touched repo (suites + ruff/lint green). E2E on the compose stack:
create entry via admin API → `calcom_sim.py create --attendee-email <entry>` → booking
rejected, `booking.rejected (blacklisted)` in saver events, dedicated-template notification
in notifier outbox / WireMock journal; a non-listed email still books fine; entry with
expired window does not block.

## Out of scope

- Pattern/domain matching (`*@domain`) — future rule type.
- Blacklist audit history (created_by/updated_at suffice for now).
