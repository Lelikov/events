# event-booking Service Design

## Context

calendar-bot (`~/PycharmProjects/calendar-bot`) — monolithic orchestrator for a volunteer psychological consultation platform. It receives Cal.com booking webhooks and synchronously coordinates: constraint validation, GetStream chat creation, Jitsi meeting URL generation, email/Telegram notifications, and CloudEvent emission.

**Goal:** Decompose calendar-bot into the events system (`~/PycharmProjects/events`), creating a new `event-booking` service and distributing responsibilities across existing services.

**Constraint:** Services are not in production — no backward compatibility required.

## Architecture Overview

```
Cal.com webhook
      │
      ▼
event-receiver (POST /event/booking)
      │
      ├── publishes CloudEvent → RabbitMQ (events.booking.lifecycle)
      │
      ▼
event-booking (FastStream consumer)
      │
      ├── reads/writes Cal.com PostgreSQL DB (constraints, enrichment)
      ├── creates GetStream chat channels
      ├── generates Jitsi meeting URLs (JWT + Shortify)
      │
      ├── publishes enriched events → event-receiver (HTTP POST)
      │         │
      │         ├── → events.notification.delivery → event-notifier
      │         └── → events.booking.lifecycle → event-saver
      │
      └── internal scheduler: every N minutes queries Cal.com DB
          for upcoming bookings → publishes booking.reminder events

event-receiver also receives:
  - Telegram bot webhook (new POST /telegram endpoint)
  - Enriched events from event-booking (existing /event/booking endpoint)

event-notifier consumes events.notification.delivery:
  - Renders Jinja2 email templates, sends via Unisender Go
  - Formats Telegram HTML messages, sends via Bot API
```

## event-booking — Internal Architecture

### Role

Consumer of `events.booking.lifecycle` queue + background reminder scheduler. Not an HTTP service for external clients.

### Layer Structure

```
event-booking/
├── event_booking/
│   ├── consumers/          # FastStream RabbitMQ subscribers
│   ├── controllers/
│   │   ├── booking.py      # dispatch by event type, orchestrate flow
│   │   ├── constraints.py  # validate limits (interval, monthly, yearly, overlap)
│   │   ├── meeting.py      # Jitsi JWT + Shortify URL generation
│   │   ├── chat.py         # GetStream chat lifecycle
│   │   └── reminder.py     # scheduler logic
│   ├── adapters/
│   │   ├── sql.py          # SqlExecutor (Cal.com DB, raw SQL)
│   │   ├── db.py           # BookingDatabaseAdapter
│   │   ├── events.py       # EventPublisher (HTTP POST to event-receiver)
│   │   ├── get_stream.py   # GetStream Chat SDK wrapper
│   │   └── shortener.py    # Shortify URL shortener
│   ├── interfaces/         # Protocol-based contracts
│   ├── scheduler/          # asyncio periodic task (reminder loop)
│   ├── dtos.py             # Frozen dataclasses
│   ├── ioc.py              # Dishka DI (APP/REQUEST scopes)
│   └── settings.py         # Pydantic Settings
├── tests/
├── pyproject.toml
└── CLAUDE.md
```

### Event Processing Matrix

| Incoming event | Actions in event-booking | Outgoing events |
|---|---|---|
| `booking.created` | constraints check → reject OR: create chat, create meeting URLs | `booking.enriched.created` + `meeting.url_created` |
| `booking.rescheduled` | update chat, update meeting URLs | `booking.enriched.rescheduled` + `meeting.url_created` |
| `booking.reassigned` | delete old chat, create new, new meeting URLs | `booking.enriched.reassigned` + `meeting.url_created` |
| `booking.cancelled` | delete chat, delete meeting URLs | `booking.enriched.cancelled` + `meeting.url_deleted` |
| constraints reject | send rejection event, delete booking in Cal.com DB | `booking.rejected` |
| (scheduler) | query Cal.com DB for upcoming bookings in time window | `booking.reminder` |

### Reminder Scheduler

- Starts as `asyncio.create_task` at application lifespan startup
- Loop: sleep N minutes → query bookings where start_time in [now + shift_from, now + shift_to] → publish `booking.reminder` event for each
- Configurable via settings: `REMINDER_INTERVAL_MINUTES`, `REMINDER_SHIFT_FROM_MINUTES`, `REMINDER_SHIFT_TO_MINUTES`

### External Dependencies

| Dependency | Purpose | Failure mode |
|---|---|---|
| Cal.com PostgreSQL | Read/write bookings, constraints, enrichment | Fatal — cannot process |
| RabbitMQ | Consume events | Fatal — FastStream handles reconnect |
| GetStream Chat | Chat channel CRUD | Logged, swallowed (non-critical) |
| Shortify | URL shortening | Fallback to long Jitsi URLs |
| event-receiver | Publish enriched events | Logged, fire-and-forget |

## Changes to Existing Services

### event-receiver

**New endpoint: Telegram bot webhook** (`POST /telegram`)
- Validates secret token in header
- Parses Telegram Update → publishes CloudEvent (`telegram.message`) to RabbitMQ
- New queue: `events.telegram`

**New routing rules for enriched events:**

| Event type | Target queue(s) |
|---|---|
| `booking.enriched.*` | `events.notification.delivery` + `events.booking.lifecycle` |
| `booking.rejected` | `events.notification.delivery` + `events.booking.lifecycle` |
| `booking.reminder` | `events.notification.delivery` |
| `meeting.url_created` / `meeting.url_deleted` | `events.booking.lifecycle` |
| `telegram.message` | `events.telegram` |

### event-notifier

Absorbs all notification logic from calendar-bot:

- **Email templates** (Jinja2) — confirmation, reschedule, cancellation, reminder, rejection (organizer + client variants)
- **Telegram notifications** — HTML-formatted messages to organizer
- **Adapters**: Unisender Go client (with retry), Telegram Bot API
- **Consumer**: subscribes to `events.notification.delivery`
- Receives enriched events (already contain meeting URL, timezone info) — renders template and sends

### event-schemas

New types and payload models:

- `booking.enriched.created` — `BookingEnrichedCreatedPayload` (with meeting URLs, chat info)
- `booking.enriched.rescheduled` — `BookingEnrichedRescheduledPayload`
- `booking.enriched.reassigned` — `BookingEnrichedReassignedPayload`
- `booking.enriched.cancelled` — `BookingEnrichedCancelledPayload`
- `booking.rejected` — `BookingRejectedPayload` (rejection reason, available_from)
- `booking.reminder` — `BookingReminderPayload`
- `telegram.message` — `TelegramMessagePayload`
- Updates to `EVENT_PRIORITIES`, `EVENT_SCHEMA_VERSIONS`

### event-saver

- New handlers for `booking.enriched.*` events
- Store enriched data (meeting URLs, chat channel IDs) in projections

## Implementation Phases

### Phase 1 — Foundation
1. event-schemas: new event types and payload models
2. event-receiver: routing rules for new event types
3. event-booking: service skeleton (FastStream consumer, DI, adapters, settings)

### Phase 2 — Business Logic (event-booking)
4. Constraints validation (ported from calendar-bot)
5. Meeting URL generation (Jitsi JWT + Shortify)
6. Chat lifecycle (GetStream)
7. EventPublisher (publish enriched events to event-receiver)
8. Booking controller (dispatch + orchestration of steps 4-7)
9. Reminder scheduler

### Phase 3 — Notifications
10. event-notifier: Jinja2 email templates + Unisender Go adapter
11. event-notifier: Telegram notification adapter + formatting
12. event-receiver: Telegram bot webhook endpoint

### Phase 4 — Switch
13. End-to-end testing of full flow
14. Switch Cal.com webhook URL to event-receiver
15. Decommission calendar-bot

## What Is NOT Ported

- `/users` endpoint — removed
- `/jitsi/webhook` — already handled by event-receiver
- `/webhook/mail` — already handled by event-receiver
- In-memory `processed_mail_webhook_ids` set — not needed
- In-memory `background_tasks` set — FastStream manages consumer lifecycle
- CORS `allow_origins=["*"]` — event-booking is not an external HTTP service
- Redis notification deduplication — to be re-evaluated if needed
