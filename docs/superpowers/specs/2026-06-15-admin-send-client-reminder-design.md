# Admin "Send Client Reminder" Button — Design

**Date:** 2026-06-15
**Status:** Approved (brainstorm)

## Problem

From the admin booking detail page, an operator needs a button to send the client
the meeting-info reminder email (the `BOOKING_REMINDER` template) on demand —
e.g. the client says they never got it, or the admin just changed the client's
email and wants to resend. The email MUST go to the client's **current** address,
not a value cached in the booking, because the admin may have changed it (the
change lives in event-users, applied asynchronously).

## Approach

event-admin gains an endpoint that:
1. resolves the client's current email from event-users (authoritative source),
2. builds a `BOOKING_REMINDER` `notification.send_requested` command, and
3. publishes it via the existing admin path (`/event/admin` → event-receiver → notifier).

This reuses the exact pipeline scheduled reminders already travel. **No
event-receiver or event-notifier code changes** — verified:
- `event_receiver/normalizers.py::_participants_from_notification_command` maps a
  command's `recipients` → `normalized.participants`.
- The event-receiver publisher centrally wraps every event in `{original,
  normalized}` and enriches participant `user_id` from event-users.
- `event_schemas.queues` routes `notification.send_requested` from **any** source
  (`RoutingRuleSpec(NOTIFICATION_COMMANDS, "*", "notification.send_requested")`)
  to `events.notification.commands`.

### Channel scope — config-driven (decided)

Publishing a `BOOKING_REMINDER` command fires every channel **enabled** for
`(BOOKING_REMINDER, client)` in `notification_bindings`. We do NOT hard-code
email-only in the endpoint. The per-role bindings UI (shipped 2026-06-15) is the
lever: to make it email-only, disable the `(BOOKING_REMINDER, client, telegram)`
binding in «Уведомления». The button means "send the client reminder"; channels
follow the config.

## Components

### 1. event-admin — new endpoint + controller method

**Route:** `POST /bookings/{booking_uid}/send-client-reminder` on the existing
`bookings_router` (`require_admin`), alongside `reassign-client`.

**Handler logic (delegates to `IBookingsController.send_client_reminder`):**
1. `controller.get_booking_details(booking_uid)` → 404 `booking_not_found` if None.
2. **Eligibility gate (server-enforced):**
   - `current_client_participant` is None → 409 `no_client_on_booking`.
   - `start_time` is None or `start_time <= now(UTC)` → 409 `booking_not_eligible`.
   - `current_status` in the cancelled/rejected set → 409 `booking_not_eligible`.
3. **Resolve current email (block on no account):**
   - `client_user_id = current_client_participant.user_id`; None → 409
     `client_has_no_account` (nothing sent).
   - `UsersClient.get_user(client_user_id)`; 404 → 409 `client_not_found`;
     5xx/transport → 502 (existing notifier/users error mapping).
   - Read `email` (current), `name`, `locale` from the returned user.
4. **Build the command payload** (mirrors the scheduler's client reminder keys,
   plus the client meeting URL and a resend nonce):
   ```
   NotificationCommandPayload(
     booking_id = booking_uid,
     trigger_event = "BOOKING_REMINDER",
     recipients = [{ email: <current>, role: "client", locale: <locale or None> }],
     template_data = {
       booking_uid, start_time (iso), end_time (iso),
       client_name: <name>, client_email: <current>,
       meeting_url: <client meeting link or "">,
       requested_at: <iso timestamp>,   # resend nonce (see Idempotency)
     },
   )
   ```
   `meeting_url`: from `meeting_links`, pick the item whose
   `participant.user_id == client_user_id`; if none, the most recent link;
   if no links, `""`.
5. **Publish:** `EventPublisher.publish(source="admin",
   event_type="notification.send_requested", data=<payload dict>)`. Publish
   failure → 502 (`EventPublishError`).
6. Return `202 {"status": "accepted", "email": <current>}`.

**Plumbing:** add `send_client_reminder` to `IBookingsController` +
`BookingsController`; the controller needs the `IUsersClient` and `IEventPublisher`
(both already DI-registered). The booking-data read uses the existing
`get_booking_details`. New DTO `SendClientReminderResultDto(email)` and response
schema. New error codes are added to the existing structured-error contract.

### 2. event-admin-frontend — button on the booking detail page

- A "Отправить напоминание клиенту" button in the booking detail view.
- **Disabled** (with a tooltip/hint) when ineligible, computed from the already
  loaded detail: no `current_client_participant`, `start_time` missing/past, or
  `current_status` cancelled/rejected. (Server re-checks; the UI disable is UX.)
- **Confirm dialog** before sending (it sends a real email).
- On success: show the resolved address ("Отправлено на <email>"); on error,
  surface the structured error message (e.g. "У клиента нет аккаунта").
- New API function `sendClientReminder(bookingUid)` → `POST …/send-client-reminder`.

### 3. event-notifier — unchanged

Consumes the command, selects the `(BOOKING_REMINDER, client, email)` binding
(and any other enabled client channel), sends to the current address.

## Idempotency / resend

event-receiver suppresses byte-identical payloads for 10 minutes
(`generate_idempotency_key(event_type, booking_id, data)`). The `requested_at`
timestamp in `template_data` makes each manual click a distinct payload, so a
deliberate resend is never silently swallowed. The notifier's own per-event
idempotency (`processed_events` by CloudEvent id; outbox key
`{event_id}:{email}:{channel}`) still prevents double-processing of a single click
(event-admin's publisher mints a fresh CloudEvent id per publish).

## Error handling (structured `{code, message}` contract)

| Condition | Status | code |
|---|---|---|
| booking uid unknown | 404 | `booking_not_found` |
| booking has no client participant | 409 | `no_client_on_booking` |
| start_time missing/past, or status cancelled/rejected | 409 | `booking_not_eligible` |
| client participant has no `user_id` | 409 | `client_has_no_account` |
| event-users returns 404 for the client | 409 | `client_not_found` |
| event-users 5xx/transport | 502 | `notifier_service_error`-style users mapping |
| publish to event-receiver fails | 502 | existing `EventPublishError` mapping |
| success | 202 | — (`{status, email}`) |

## Testing

**event-admin (no real DB/network, `FakeProvider`):**
- success: eligible booking + resolved user → 202, published payload asserts
  `trigger_event=BOOKING_REMINDER`, single client recipient with the CURRENT email
  (different from any stale booking value), `template_data` keys present incl.
  `requested_at` and `meeting_url`.
- gating matrix: no client → 409 `no_client_on_booking`; past/None start_time →
  409 `booking_not_eligible`; cancelled/rejected status → 409 `booking_not_eligible`.
- block-on-no-account: client participant `user_id=None` → 409
  `client_has_no_account`, publisher NOT called.
- event-users 404 → 409 `client_not_found`, publisher NOT called.
- publish failure → 502.

**event-admin-frontend (Vitest):**
- button disabled for ineligible bookings (each gate);
- confirm + POST calls `sendClientReminder(uid)`;
- success and error rendering.

## Out of scope (YAGNI)

- Sending to the organizer (this is client-only).
- A hard email-only override in the endpoint (channels are config-driven via the
  bindings UI).
- Choosing a different template/trigger from the UI (always `BOOKING_REMINDER`).
- Rate limiting beyond the existing 10-minute identical-payload suppression
  (defeated intentionally by `requested_at` for legitimate resends).
