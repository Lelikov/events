# Contract Decisions (audit v2 — CONTRACTS coordinator)

Canonical cross-service contract decisions. Per-service fixers MUST follow these.
`event-schemas` v0.2.0 is the single source of truth; all literals below exist there as constants.

## D1. Canonical data envelope

Every message on the `events` exchange carries CloudEvent `data` in the form:

```json
{
  "original":   { ...domain payload, exactly as produced... },
  "normalized": { "participants": [ {"email", "role", "time_zone", "user_id"}, ... ] }
}
```

- Only **event-receiver** wraps. Producers POST bare domain payloads to receiver ingress.
- Typed accessors live in `event_schemas.envelope`: `EventEnvelope`, `NormalizedSection`,
  `EnvelopeParticipant`, `unwrap_payload(data)`.
- Consumers MUST NOT read domain fields at the top level of CloudEvent data — always
  `unwrap_payload()` (or `EventEnvelope.parse_payload(Model)`).
- `normalized.participants[].user_id` is the event-users UUID resolved by the receiver;
  consumers needing a user UUID (e.g. event-notifier) take it from here, keyed by email.

## D2. RabbitMQ topology (single source: `event_schemas.queues`)

- Exchanges: `events` (topic, durable) and DLX `events.dlx` (topic, durable).
- **One queue per consumer service.** Fan-out = multiple queues bound to the same routing key.
  - `events.booking.lifecycle.saver`   ← rk `events.booking.lifecycle` (event-saver)
  - `events.booking.lifecycle.booking` ← rk `events.booking.lifecycle` (event-booking)
  - `events.chat.lifecycle`, `events.chat.activity`, `events.chat`, `events.meeting.lifecycle`,
    `events.notification.delivery`, `events.jitsi`, `events.mail`, `events.unrouted` (event-saver)
  - `events.notification.commands` ← rk `events.notification.commands` (event-notifier)
  - `events.user.email` (event-users)
- Canonical queue arguments (verbatim, from `QueueSpec.arguments`):
  `{"x-max-priority": 10, "x-dead-letter-exchange": "events.dlx", "x-dead-letter-routing-key": "<queue>.dlq"}`.
- Every queue has a `<queue>.dlq` companion (args `{"x-message-ttl": 86400000}`) bound to
  `events.dlx` with rk `<queue>.dlq`.
- Every consumer declares `events.dlx`, its own queue(s) and their DLQs at startup (idempotent;
  removes startup-order dependency). event-receiver declares the FULL topology (`ALL_QUEUES`).
- **Removed:** `events.booking.reminder` (no producer, no consumer). `booking.reminder_sent`
  routes to `events.booking.lifecycle`. Reminders are sent via `notification.send_requested`
  with `trigger_event=BOOKING_REMINDER` from the event-booking scheduler.
- Routing rules: `event_schemas.queues.ROUTING_RULES` (glob on source/type). Receiver default
  rules are generated from this table.

## D3. CloudEvent attribute naming (`event_schemas.attributes`)

- Booking extension attribute: **`bookingid`** (header `ce-bookingid`). CloudEvents spec forbids
  underscores in extension attribute names. `ce-booking_id` / `booking_id` attribute readers are bugs.
- Also canonical: `traceid`, `spanid`, `idempotencykey`.

## D4. EventType policy

- `event_schemas.types.EventType` is the closed enum of internal types.
- event-receiver MUST NOT 500 on a type outside the enum (e.g. GetStream `member.added`):
  unknown types are published with routing key `events.unrouted`, priority NORMAL, empty
  `normalized.participants`, payload preserved in `original` (event-saver persists them).
- `event_schemas.mapping.PAYLOAD_MODELS: dict[EventType, type[BaseModel]]` maps each event type
  with a defined contract to its payload model (the shape of `original`). External pass-through
  types (jitsi.*, getstream.*, unisender.*) map to their pass-through models.
- Deleted dead models: `EmailRejectionNotificationPayload` (orphaned; rejection data travels in
  `NotificationCommandPayload.template_data`).

## D5. Canonical payload models (the `original` section)

| type | model | shape |
|---|---|---|
| booking.created | `BookingCreatedPayload` | `user{email,time_zone?}`, `client{email,time_zone?}`, `start_time`, `end_time`, `volunteer_id?`, `client_id?` |
| booking.rescheduled | `BookingRescheduledPayload` | `users[]`, `start_time`, `end_time`, `previous_start_time?`, **`previous_booking_uid?`**, `rescheduled_by?` |
| booking.cancelled | `BookingCancelledPayload` | `users[]`, `cancellation_reason?`, `cancelled_by?` |
| booking.reassigned | `BookingReassignedPayload` | `users[]` (roles: organizer, client, previous_organizer), `previous_organizer_email?` |
| booking.rejected | `BookingRejectedPayload` | `client_email`, `rejection_type?`, `rejection_reasons[]`, `available_from?`, `has_active_booking`, `active_booking_start?` |
| booking.reminder_sent | `BookingReminderSentPayload` | `email`, `client_id?` (no producer today; kept for saver routing) |
| meeting.url_created | `MeetingUrlCreatedPayload` | `email`, `recipient_role`, `meeting_url` |
| meeting.url_deleted | `MeetingUrlDeletedPayload` | `email`, `recipient_role` |
| chat.created / chat.deleted | `ChatCreatedPayload` / `ChatDeletedPayload` | `channel_id` (== booking uid) |
| chat.message_sent | `ChatMessageSentPayload` | `user_id` |
| notification.send_requested | `NotificationCommandPayload` | `booking_id`, `trigger_event`, `recipients[{email, role}]`, `template_data{}` |
| notification.email/telegram/push.message_sent | `EmailNotificationPayload` / `TelegramNotificationPayload` / `PushNotificationPayload` | unchanged |
| `users[]` element | `BookingParticipant` | `email`, `role?`, `time_zone?` |

- `ClientInfo` now extends `UserInfo` for real (inherits `time_zone`).
- **booking.rescheduled identity**: a real cal.com reschedule mints a NEW `uid`; the old uid
  arrives as `rescheduleUid`. The internal contract carries `previous_booking_uid` = old uid;
  CloudEvent `bookingid` = NEW uid. event-saver links lifecycle rows via
  `details.previous_booking_uid` (full projection re-keying is the saver fixer's job).

## D6. notification.send_requested chain

- Producer (event-booking) sends `NotificationCommandPayload` — recipients are `{email, role}`
  (NO user_id; producers don't know event-users UUIDs).
- event-receiver normalizes recipients into `normalized.participants` and enriches `user_id`
  via event-users resolve-or-create.
- event-notifier unwraps the envelope, validates `NotificationCommandPayload` against
  `original`, and resolves each recipient's `user_id` from `normalized.participants` by email.
  Recipients without a resolvable user_id are logged and skipped (backfill is a notifier-fixer
  concern). `template_context` = `original.template_data` merged over `original` (never the wrapper).

## D7. Receiver normalizer coverage

Participant extraction cases (receiver): booking.created (user/client), booking.cancelled /
rescheduled / reassigned (users[]), booking.rejected (client_email → role client),
booking.reminder_sent (email), meeting.url_created / url_deleted (email + recipient_role),
notification.send_requested (recipients[]), unisender / getstream / jitsi (unchanged).
chat.created/deleted carry no participants (channel_id only).

## D8. Out of scope for per-service fixers to NOT re-litigate

- Keep the envelope (D1) — do not flatten it.
- Do not re-introduce shared queues or per-service queue-arg literals.
- Do not read `ce-booking_id` anywhere.
