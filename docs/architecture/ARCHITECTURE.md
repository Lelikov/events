# System Architecture

## System Purpose

An event-driven microservices system for managing bookings, participants, and notifications. External webhook events (most importantly **cal.com** booking webhooks) are ingested, normalized into a typed CloudEvent envelope, routed through RabbitMQ, persisted to PostgreSQL, and projected into materialized views consumed by an admin UI. A **booking orchestrator** (event-booking) provisions chat channels, meeting URLs and notification commands; a notification dispatcher fans out to email and Telegram channels and publishes delivery-result events back into the system.

> Audit status: all services were re-audited and hardened in **audit-v2 (2026-06-11)** —
> see `docs/audit/v2/AUDIT_REPORT_V2.md`. Test counts below are from its integration gate.

## Service Inventory

| Service | Purpose | Tech Stack | Tests (audit-v2) |
|---------|---------|-----------|------------------|
| event-receiver | HTTP ingress gateway: validates webhooks (incl. cal.com `POST /event/calcom`), normalizes to the `{original, normalized}` CloudEvent envelope, publishes to RabbitMQ | Python 3.14, FastAPI, FastStream, Dishka | 103 |
| event-saver | Consumes RabbitMQ queues, deduplicates, persists raw events, builds projection tables; owns the main DB schema (alembic) | Python 3.14, FastAPI, FastStream, SQLAlchemy 2.x, Alembic, Dishka | 100 |
| event-booking | Booking orchestrator: consumes `events.booking.lifecycle.booking`, reads/writes the cal.com DB, enforces booking constraints, creates GetStream chat channels, mints per-participant Jitsi JWT meeting URLs (shortened via event-shortener), schedules reminders, publishes follow-up events back through event-receiver | Python 3.14, FastAPI (health), FastStream, SQLAlchemy, Dishka, stream-chat, PyJWT | 88 |
| event-admin | Read-only API over event-saver's DB for admin UI; publishes admin actions (email change, client reassign) via event-receiver | Python 3.14, FastAPI, SQLAlchemy 2.x, Dishka | 75 |
| event-admin-frontend | Admin SPA: bookings list, booking details, participants (talks only to event-admin, incl. its users proxy) | TypeScript, React 18, Vite, Vitest | 27 |
| jitsi-chat | Participant-facing video meeting + chat SPA; sends Jitsi iframe telemetry CloudEvents to event-receiver | TypeScript, React 19, Vite 7, @jitsi/react-sdk, stream-chat, Vitest | 21 |
| event-users | User/contact CRUD with background CRM sync and CRM webhook outbox; consumes `events.user.email` | Python 3.14, FastAPI, SQLAlchemy 2.x, Dishka | 55 |
| event-notifier | Notification dispatcher: consumes `events.notification.commands`, transactional outbox, email/Telegram delivery, publishes `notification.*.message_sent` delivery results back via event-receiver | Python 3.14, FastAPI, FastStream, asyncpg, Dishka, Jinja2 | 80 |
| event-shortener | REST URL shortener: shortens meeting links for event-booking (`POST/GET/PATCH/DELETE /api/v1/urls/*` + public `GET /{ident}` redirect), own PostgreSQL. Replaced the `/shortify` WireMock stub | Python 3.14, FastAPI, SQLAlchemy 2.x, Alembic, Dishka | — |
| event-scheduling | **Scheduling domain model + slot engine + write-side bookings + booking→events outbox** (slices 1–4a of cal.com replacement): organizer schedules, event types, hosts, booking limits; `GET /api/v1/slots` returns available slots (weekly/override/travel/DST; busy source is real, buffer-expanded); `POST /api/v1/bookings` + cancel/reschedule/history, DB exclusion constraint prevents double-booking, round-robin host assignment, booking-limit enforcement; a transactional `outbox` + background dispatcher publishes `booking.lifecycle` CloudEvents to event-receiver `POST /event/booking` (additive alongside cal.com — same endpoint, same event types; event-saver projects them, event-booking chat/Jitsi integration is deferred to slice 4a.2). Own PostgreSQL (`event_scheduling`). One-time ETL from cal.com. No RabbitMQ — purely HTTP in and out (dispatcher makes outbound HTTP calls to event-receiver + event-users). | Python 3.14, FastAPI, SQLAlchemy 2.x, Alembic, Dishka | 111 |
| event-booker | **Public booking BFF** (slice 4b.1): stateless FastAPI service in front of event-scheduling and event-users, the **public trust boundary** for guest bookings — holds `SCHEDULING_API_KEY`/`EVENT_USERS_TOKEN` server-side and exposes 4 unauthenticated `/api/public/*` endpoints (list event types, get event type, get slots, create booking) that resolve-or-create the guest as a `client` user then create the booking. No DB, no RabbitMQ, no background tasks; frontend (slice 4b.2) not yet built. | Python 3.14, FastAPI, httpx, Dishka | 23 |
| event-schemas | Shared Python library (v0.2.0): Pydantic payload models, EventType enum, priorities, **canonical RabbitMQ topology** (`queues.py`), envelope (`envelope.py`), CloudEvent attributes | Python, Pydantic v2 | 73 |
| event-db-sync | Trigger-driven cal.com→event-users sync: applies an additive `AFTER INSERT/UPDATE` NOTIFY trigger on cal.com `"Attendee"`/`"users"`, listens on `pg_notify('user_sync')`, runs a watermark reconcile sweep + `POST /admin/full-sync`, and publishes `user.upserted` directly to RabbitMQ; own `sync_state` DB | Python 3.14, FastAPI, asyncpg, FastStream | — |

## System Topology

```mermaid
flowchart TD
    subgraph External["External Clients / Webhooks"]
        CALCOM[cal.com Webhooks]
        BOOKING_SVC[Internal producers\n(event-booking, event-notifier,\nevent-admin)]
        GETSTREAM[GetStream]
        JITSI_CHAT[jitsi-chat SPA\nReact + Jitsi + Stream Chat]
        UNISENDER_WH[UniSender Go Webhook]
    end

    subgraph External_APIs["External APIs"]
        UNISENDER_API[UniSender Go API]
        TELEGRAM_API[Telegram Bot API]
        CRM[External CRM API]
        GETSTREAM_API[GetStream Chat API]
    end

    subgraph Infra["Infrastructure"]
        RMQ[(RabbitMQ\ntopic exchange: events\n+ DLX events.dlx)]
        PG_MAIN[(PostgreSQL\nmain DB\nport 5439)]
        PG_USERS[(PostgreSQL\nusers DB\nport 5446)]
        PG_NOTIFIER[(PostgreSQL\nnotifier DB\nport 5432)]
        PG_CALCOM[(PostgreSQL\ncal.com DB)]
        PG_SHORTENER[(PostgreSQL\nshortener DB)]
    end

    subgraph Services["Application Services"]
        RECEIVER[event-receiver\nPort 8888]
        SAVER[event-saver\nBackground consumer]
        BOOKING[event-booking\nOrchestrator consumer\nPort 8990 health]
        ADMIN[event-admin\nPort 8002]
        FRONTEND[event-admin-frontend\nVite SPA]
        USERS[event-users\nPort 8001]
        NOTIFIER[event-notifier\nBackground consumer]
        SHORTENER[event-shortener\nREST URL shortener\nPort 8000]
    end

    subgraph Lib["Shared Libraries"]
        SCHEMAS[event-schemas v0.2.0\npip package\nqueues + envelope + payloads]
    end

    %% Inbound HTTP (sync)
    CALCOM -->|"POST /event/calcom\n(X-Cal-Signature-256 HMAC)"| RECEIVER
    BOOKING_SVC -->|"POST /event/booking, /event/admin\n(API key)"| RECEIVER
    GETSTREAM -->|"POST /event/getstream\n(HMAC-SHA256)"| RECEIVER
    JITSI_CHAT -->|"POST /event/jitsi\n(JWT)"| RECEIVER
    UNISENDER_WH -->|"POST /event/unisender-go\n(MD5 sig)"| RECEIVER

    %% Receiver -> RabbitMQ (async publish)
    RECEIVER -->|"publish CloudEvent envelope\n{original, normalized}\n(topic routing)"| RMQ

    %% RabbitMQ -> consumers (async)
    RMQ -->|"events.booking.lifecycle.saver\nevents.chat.* / events.meeting.*\nevents.notification.delivery\nevents.jitsi / events.mail\nevents.unrouted"| SAVER
    RMQ -->|"events.booking.lifecycle.booking"| BOOKING
    RMQ -->|"events.notification.commands"| NOTIFIER
    RMQ -->|"events.user.email"| USERS

    %% DB connections
    SAVER -->|"read/write\n(schema owner)"| PG_MAIN
    ADMIN -->|"read-only\n(same DSN)"| PG_MAIN
    USERS -->|"read/write"| PG_USERS
    NOTIFIER -->|"read/write\n(outbox, processed_events)"| PG_NOTIFIER
    BOOKING -->|"read + constrained writes\n(status, metadata)"| PG_CALCOM

    %% Sync HTTP between services
    RECEIVER -->|"resolve-or-create users\n(Bearer token)"| USERS
    FRONTEND -->|"auth, bookings, users proxy\n(JWT)"| ADMIN
    ADMIN -->|"GET /api/users/*\n(Bearer token)"| USERS
    NOTIFIER -->|"GET /api/users/id/{id}\n(Bearer token)"| USERS
    BOOKING -->|"POST follow-up events\n(chat.*, meeting.url_*,\nbooking.rejected,\nnotification.send_requested)"| RECEIVER
    NOTIFIER -->|"POST notification.*.message_sent\n(delivery results)"| RECEIVER
    ADMIN -->|"POST /event/admin\n(user.email.change_requested,\nbooking.client_reassigned)"| RECEIVER

    %% Outbound delivery
    NOTIFIER -->|"email send"| UNISENDER_API
    NOTIFIER -->|"sendMessage"| TELEGRAM_API
    USERS -->|"CRM sync + webhook outbox\n(AES encrypted)"| CRM
    BOOKING -->|"create channels, tokens"| GETSTREAM_API
    BOOKING -->|"shorten meeting URLs\n(Bearer key, REST)"| SHORTENER
    SHORTENER -->|"read/write"| PG_SHORTENER

    %% Library dependencies (compile-time)
    SCHEMAS -.->|"pip import"| RECEIVER
    SCHEMAS -.->|"pip import"| SAVER
    SCHEMAS -.->|"pip import"| BOOKING
    SCHEMAS -.->|"pip import"| NOTIFIER
```

**Queue topology (audit-v2):** one queue per consumer service; fan-out is achieved by binding
several queues to the same routing key — `events.booking.lifecycle.saver` (event-saver) and
`events.booking.lifecycle.booking` (event-booking) are both bound to routing key
`events.booking.lifecycle`. Every queue dead-letters to `events.dlx` with a `<queue>.dlq`
companion (24h TTL). Single source of truth: `event-schemas/event_schemas/queues.py`; full
registry in `docs/architecture/MESSAGE_CONTRACTS.md`.

**User-sync flow (event-db-sync):** `event-db-sync` replaces the old HTTP CRM poll with a
trigger-driven path. It applies an additive `AFTER INSERT/UPDATE` NOTIFY trigger on the cal.com
DB (`"Attendee"` → `role=client`, `"users"` → `role=organizer`; a *sanctioned integration hook*,
not a cal.com schema migration), and on `pg_notify('user_sync')` publishes `user.upserted`
**directly to RabbitMQ** on `events.user.email` (no event-receiver hop). event-users consumes it,
upserts the user, and publishes `user.synced` **directly to RabbitMQ** on the new saver-owned
`events.user.synced` queue; event-saver backfills `bookings.{organizer,client}_user_id` by
participant email. A watermark reconcile sweep (own `sync_state` DB) plus `POST /admin/full-sync`
re-emit rows missed during downtime / force a full cutover pass.

**Public booking BFF (event-booker, slice 4b.1):** `event-booker` is the **public trust boundary**
in front of `event-scheduling` and `event-users` — the only service in this system designed to be
called directly by an untrusted public browser:
`public browser → event-booker (holds keys) → event-scheduling + event-users`. It holds
`SCHEDULING_API_KEY`/`EVENT_USERS_TOKEN` server-side and exposes 4 unauthenticated
`/api/public/*` endpoints; response schemas are hand-mapped from internal DTOs so upstream ids
(`client_user_id`/`host_user_id`) and raw upstream bodies never reach the browser. No DB, no
RabbitMQ. The public frontend that will call these endpoints (slice 4b.2) does not exist yet.

## Key Architectural Decisions

### 1. Why Microservices (not Monolith)

**Rationale:** Independent failure domains. The booking ingestion path (event-receiver + RabbitMQ + event-saver) must remain available even if notifications, admin UI, or user management fail. Each service has different scaling characteristics -- event-receiver handles bursty webhook traffic while event-saver needs steady throughput.

**Evidence:** The minimum viable path for booking receipt requires only 4 components: event-receiver, RabbitMQ, event-saver, PostgreSQL main DB. All other services can fail independently without data loss (`docs/audit/DEPENDENCY_GRAPH.md:200-209`).

### 2. Why RabbitMQ (not Kafka or Direct HTTP)

**Rationale:** Decouples ingestion throughput from processing speed. Event-receiver can accept webhooks at any rate without back-pressure from slow projections. Topic exchange with routing keys provides flexible event routing without producer awareness of consumers. Priority queues (`x-max-priority=10`) ensure booking lifecycle events are processed before chat events.

**Trade-off:** The system currently has no replay capability (unlike Kafka). Once consumed, events exist only in PostgreSQL.

### 3. Why event-receiver Separate from event-saver

**Rationale:** event-receiver is stateless (no DB connection), horizontally scalable, and handles 4 different auth methods (API key, JWT, HMAC, MD5 signature). event-saver is a long-running consumer with projection logic tightly coupled to the DB schema. Separating them allows the ingress gateway to restart independently without interrupting queue consumption.

**Evidence:** event-receiver has no `POSTGRES_DSN` configuration (`event-receiver/docs/SERVICE_OVERVIEW.md:24`).

### 4. Why event-notifier Separate

**Rationale:** Notification fan-out has different reliability semantics (transactional outbox with retries), its own database, and calls external APIs that may be slow or unavailable. Isolating it prevents slow Telegram/email delivery from blocking booking event persistence.

**Evidence:** event-notifier uses asyncpg directly (not SQLAlchemy), has its own DB schema with `notification_outbox` and `processed_events` tables (the dead `routing_rules` table was dropped in migration 002), and polls a transactional outbox for delivery with permanent/transient retry classification (`event-notifier/docs/SERVICE_OVERVIEW.md`).

### 5. Why event-admin is Read-Only

**Rationale:** Enforces data ownership -- event-saver is the single writer to the main DB. event-admin exposes only `fetch_one`/`fetch_all` in its `ISqlExecutor` interface (`event-admin/event_admin/adapters/sql.py:11-21`). Schema migrations live exclusively in `event-saver/alembic/`.

**Inconsistency (audit finding):** Despite read-only intent, event-admin uses the same PostgreSQL superuser credentials as event-saver (`postgres`/`postgres`). No database-level role enforcement exists (`docs/audit/AUDIT_REPORT.md:136`).

### 6. Audit-v2 Decisions (2026-06-11, canonical)

Frozen in `docs/audit/v2/CONTRACT_DECISIONS.md` (D1–D8); fixers and future changes MUST follow them:

| Decision | Rationale |
|----------|-----------|
| **One queue per consumer** | event-saver and event-booking were competing consumers of one `events.booking.lifecycle` queue (round-robin split the stream). Now each consumer has its OWN queue; fan-out = multiple queues bound to the same routing key (`events.booking.lifecycle.saver` + `events.booking.lifecycle.booking`). |
| **Typed `{original, normalized}` envelope** | Only event-receiver wraps; every consumer unwraps via `event_schemas.envelope.unwrap_payload()`. `normalized.participants[].user_id` carries the event-users UUID resolved at ingress. Top-level domain-field reads are bugs. |
| **Canonical topology in event-schemas** | `event_schemas.queues` (QueueSpec, ALL_QUEUES, ROUTING_RULES, DLX/DLQ args) is the single source of truth; all services declare identical arguments idempotently. Removed: `events.booking.reminder`, `events.notifications`. |
| **Canonical CloudEvent attributes** | `bookingid`/`ce-bookingid` (CloudEvents forbids underscores in extension names), `traceid`, `spanid`, `idempotencykey` — from `event_schemas.attributes`. |
| **`/event/calcom` ingress** | cal.com webhooks are ingested natively (HMAC `X-Cal-Signature-256`), normalized against canonical payload models; unknown event types route to `events.unrouted` instead of failing with 500. |
| **Notifier command path + delivery results** | event-notifier validates `NotificationCommandPayload` from the envelope, resolves recipients from `normalized.participants`, and publishes `notification.*.message_sent` delivery-result events back through event-receiver (persisted by event-saver via `events.notification.delivery`). |

### 7. Decisions That Looked Wrong in Hindsight (April 2026 — since resolved)

| Decision | Problem | Status |
|----------|---------|--------|
| Dual EventType enums | event-schemas defined `"booking.created"` while event-saver defined `"booking.events.v1.booking.created.create"`. | **Resolved** — event-saver consumes `event_schemas` types/queues directly (audit-v2) |
| First-match routing rules in receiver config | Rules for `events.notifications` shadowed `events.booking.lifecycle`; booking events never reached event-saver. | **Resolved** — routing rules generated from `event_schemas.queues.ROUTING_RULES` |
| Queue name mismatch (notifier) | event-notifier subscribed to `events.notifications` while receiver published to `events.notification.commands`. | **Resolved** — canonical `NOTIFICATION_COMMANDS_QUEUE` |
| SqlExecutor auto-commit | `execute()` committed after every statement. | Largely resolved per service (e.g. notifier per-operation sessions + `transaction()`, users batch transactions); pattern still varies by service |
| Same DB credentials for reader and writer | event-admin has full write access despite being architecturally read-only. | **Still open** — no DB-level read-only role enforcement |

### 8. event-scheduling: Domain Owner + Slot Engine + Write-Side Bookings + Booking→Events Outbox (slices 1–4a)

`event-scheduling` (port 8004) is the first four slices of a phased replacement of
the external cal.com CRM with an in-house booking system. The goal is full cal.com
independence in several incremental slices:

| Slice | Scope | Status |
|-------|-------|--------|
| 1 | Domain model: schedules, event types, hosts, booking limits; one-time ETL from cal.com | **Delivered (2026-07-03)** |
| 2 | Slot-availability engine: `GET /api/v1/slots` (read-side) | **Delivered (2026-07-05)** |
| 3 | Write-side bookings (`booking` table, `POST/GET /api/v1/bookings` + cancel/reschedule/history; real `BusyTimesSource`; buffers, booking limits, round-robin all active) | **Delivered (2026-07-12)** |
| 4a | Booking→events outbox: transactional `outbox` table + background dispatcher publishes `booking.lifecycle` CloudEvents to event-receiver `POST /event/booking` (additive alongside cal.com) | **Delivered (2026-07-13)** |
| 4a.2 | Make event-booking act on the CloudEvent payload directly (chat creation, Jitsi meeting URLs, reminders) instead of reading only the cal.com DB | Planned |
| 5 | Booker UI (participant slot-picker SPA) | Planned |
| 6 | External calendar sync (Google/Office busy-times) | Deferred/optional |
| 7 | Schedule editor in organizer dashboard | Planned |

**Key design choices:**
- `owner_user_id` / `host.user_id` are opaque UUID references to `event-users`; no cross-service JOINs.
- Time zone is required on the schedule (eliminates cal.com's nullable fallback chain).
- `schedule_change_log` / `booking_change_log` (append-only, `schedule_change_log` JSONB / `booking_change_log` typed columns) provide a full audit trail per save/transition.
- `BusyTimesSource` Protocol in `interfaces/busy_times.py` is now bound in production to `BookingBusyTimesSource` (`booking/busy_source.py`) — queries confirmed `booking` rows, expanded by the event type's buffers; `StubBusyTimesSource` (`[]`) remains for tests only.
- **No double-booking is enforced at the database layer**: `booking` has a PostgreSQL `EXCLUDE USING gist (host_user_id WITH =, tstzrange(start_time, end_time) WITH &&) WHERE status='confirmed'` constraint (via `btree_gist`) — the actual concurrency guard, not application-level locking. `BookingService.create` optimistically inserts and retries the next round-robin-ranked host on `IntegrityError`/`ConflictError`.
- Round-robin host assignment (`booking/assignment.py`, getLuckyUser-style): fewest future confirmed bookings first, then never-assigned before assigned, then oldest `last_assigned_at`.
- `booking_limit` (`booking_count`/`booking_duration`, per `day`/`week`/`month`/`year` computed in the assigned host's schedule time zone) is enforced on `POST /api/v1/bookings`.
- cal.com is the **one-time ETL source** (`scripts/etl_from_calcom.py` migrates schedules; event_type ETL is a deferred future branch).
- `slots/domain.py` + `slots/timezones.py` are pure IO-free Python (no SQLAlchemy, no HTTP); `BookingService` reuses them unmodified for its own availability re-check.
- **Booking→events outbox is now ACTIVE (slice 4a).** `BookingService.create`/`reschedule`/`cancel` writes an `outbox` row in the same transaction as the booking mutation; a background dispatcher (started in `main.py`'s `lifespan`, no RabbitMQ) polls it, resolves participant emails via event-users (`POST /api/users/by-ids`, admin `Bearer`), and POSTs a `booking.created`/`rescheduled`/`cancelled` CloudEvent to event-receiver `POST /event/booking` — the same generic endpoint/contract an external booking service already targets. This is **additive to cal.com**: `/event/calcom` and every existing producer are untouched. At-least-once delivery via a stable per-row `ce-id`; failed/malformed rows terminate as `failed`, everything else retries with exponential backoff.
- **event-booking does not react to `event-scheduling` bookings yet.** It resolves booking context by reading the cal.com DB keyed on `booking_uid`; an `event-scheduling` booking has no cal.com row, so chat creation, Jitsi meeting URLs, and reminders remain a no-op for it. **event-saver is the only active consumer of these events at this slice** — its projections (bookings/events/participants) are correct for `event-scheduling` bookings. Making event-booking payload/API-driven instead of cal.com-DB-driven is slice 4a.2.

**Slice-4a maturity notes:**
- `buffer_before/after` and `booking_limit` are ACTIVE — applied to busy-interval computation and enforced at booking-create time, respectively.
- No slot reservations/holds — a returned slot isn't locked; create re-validates and can still 409 on a lost race.
- No slot caching; each `GET /api/v1/slots` request re-queries and recomputes.
- No external calendar busy-times (slice 6).
- **`EVENT_USERS_TOKEN` must be a real admin token in any environment where email resolution needs to succeed** — a wrong/placeholder value 401s against event-users' `require_admin` gate, and the affected outbox rows retry indefinitely rather than reaching `sent`. This is a deploy prerequisite, not a code defect.

**References:**
- Domain model spec: `docs/superpowers/specs/2026-07-03-event-scheduling-domain-model-design.md`
- Domain model plan: `docs/superpowers/plans/2026-07-03-event-scheduling-domain-model.md`
- Slot engine spec: `docs/superpowers/specs/2026-07-05-event-scheduling-slot-engine-design.md`
- Slot engine plan: `docs/superpowers/plans/2026-07-05-event-scheduling-slot-engine.md`
- Booking write-side spec: `docs/superpowers/specs/2026-07-05-event-scheduling-booking-write-side-design.md`
- Booking write-side plan: `docs/superpowers/plans/2026-07-05-event-scheduling-booking-write-side.md`
- Service guide: `event-scheduling/CLAUDE.md`

## What is Intentionally Out of Scope

- **Event replay / event sourcing** -- Events are persisted but there is no replay mechanism. Recovery relies on webhook re-delivery by external callers.
- **Service mesh / API gateway** -- Services communicate directly via HTTP and RabbitMQ. No Envoy, Istio, or centralized gateway.
- **Multi-tenancy** -- Single-tenant system.
- **Push notifications** -- FCM channel is implemented in event-notifier but deliberately not wired pending FCM credentials and an OAuth token provider.
- **Locale-aware notification text** -- per-recipient time-zone localization exists; language/locale propagation from cal.com is a documented follow-up (audit-v2 §5a).
- **CI/CD pipeline** -- Not present in the repository.
- **Event replay from DLQs** -- dead letters expire after 24h; redrive is a manual runbook (`event-receiver/QUEUES_DIGEST.md`), alerting is a platform TODO.

## Known Architectural Concerns

The April 2026 concern list (wrong-queue routing, auth bypass, SKIP LOCKED bug, no tests, no
DLQs, no pagination) was **resolved by audit-v2** — verified per service and end-to-end on a
real broker. Current open items live in `docs/audit/v2/AUDIT_REPORT_V2.md` §5:

- **Follow-ups (code work)**: event-users hardcoded queue args (no event-schemas dependency);
  event-receiver still on the deprecated event-users `roles/{role}/emails/{email}` route;
  absolute `file://` event-schemas paths in three pyprojects; locale-aware notifications;
  FCM wiring; machine-readable error codes from event-admin; DLQ alerting; user_id backfill.
- **Accepted risks**: jitsi-chat tokens in URL (Referrer-Policy mitigated); DLQ 24h TTL;
  JWT in sessionStorage; notification commands bypass event-saver (only resulting facts are
  persisted); same DB credentials for event-saver/event-admin (no DB-level read-only role).

Shared-secret coupling note: event-admin and event-users must share `JWT_SECRET_KEY` (and,
when enabled, matching `JWT_AUDIENCE`/`JWT_ISSUER`); both services now refuse weak/placeholder
secrets at startup outside DEBUG.

Historical details: `docs/audit/AUDIT_REPORT.md` (superseded, April 2026).
