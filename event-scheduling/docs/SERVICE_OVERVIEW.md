# event-scheduling: Service Overview

## Domain

`event-scheduling` owns the **booking/scheduling domain model** inside the `events`
monorepo. It holds organizer schedules, event types, hosts, and booking limits;
computes read-side slot availability; and, as of slice 3, accepts real bookings
over HTTP — the foundational data layer for a phased, incremental replacement of
the external cal.com CRM.

| Slice | Scope | Status |
|-------|-------|--------|
| 1 — Domain model | Schedules, event types, ETL from cal.com | **Delivered** |
| 2 — Slot engine | Read-side slot calculation (`GET /api/v1/slots`) | **Delivered** |
| 3 — Write-side bookings | `booking` table, booking creation, real `BusyTimesSource`, buffers, limits, round-robin | **Delivered** |
| 4a — Booking→events outbox | Transactional outbox + background dispatcher; publishes `booking.lifecycle` CloudEvents to event-receiver `POST /event/booking`; event-saver projects them | **Delivered** (this slice) |
| 4a.2 — event-booking integration | Make event-booking act on the CloudEvent payload (chat creation, Jitsi meeting URLs, reminders) instead of only reading the cal.com DB | Planned |
| 5 — Booker UI | Participant slot-picker SPA | Planned |
| 6 — Calendar sync | External busy-times (Google/Office) | Deferred/optional |
| 7 — Schedule editor | Organizer CRUD in their personal dashboard | Planned |

cal.com is the **one-time ETL source** for existing schedule data; after ETL it is
no longer required by this service.

## Subsystems

| Subsystem | Entry point | Toggle |
|-----------|-------------|--------|
| Schedule CRUD API (`/api/v1/schedules`) | `routers/schedule.py` | always on |
| Event-type CRUD API (`/api/v1/event-types`) | `routers/event_type.py` | always on |
| Slot availability API (`/api/v1/slots`) | `routers/slots.py` | always on |
| Booking API (`/api/v1/bookings`) | `routers/booking.py` | always on |
| Ops endpoints (`/health`, `/ready`, `/metrics`) | `routes.py` | always on |
| ETL from cal.com | `scripts/etl_from_calcom.py` | run once manually |
| Booking→events outbox dispatcher (slice 4a) | `publishing/dispatcher.py`, started in `main.py`'s `lifespan` | always on (background `asyncio.Task`, polling every `OUTBOX_DISPATCH_INTERVAL`) |

No RabbitMQ, no FastStream consumer — `event-scheduling` remains request/response
plus one background poller (the outbox dispatcher above), not a message-broker
participant. The container is the single Alembic migration runner (`entrypoint.sh`
runs `alembic upgrade head` first). Every booking mutation (`POST /api/v1/bookings`,
`.../cancel`, `.../reschedule`) writes an `outbox` row in its own transaction;
the request path itself is still synchronous HTTP-only — publishing to the events
pipeline happens later, out-of-band, on the dispatcher's own schedule.

## Key Design Choices

**One schedule per organizer.** `schedule.owner_user_id` has a UNIQUE constraint.
Time zone lives on the schedule (required, not nullable), eliminating the
nullable-fallback chain present in cal.com (`Schedule.timeZone ?? User.timeZone`).

**`owner_user_id` / `host.user_id` are opaque UUID references** to `event-users`.
No cross-service JOINs; names/emails are resolved by callers when needed via the
event-users API.

**Round-robin scheduling type only.** `event_type.scheduling_type` defaults to
`'round_robin'`; collective and managed types are out of scope for slice 1.

**Date overrides and travel schedules are first-class.** `date_override` rows
override a specific date's availability; `travel_schedule` rows temporarily
reassign the effective time zone for a date range.

**Append-only change log.** Every schedule PUT writes a JSONB snapshot to
`schedule_change_log` in the same transaction (`actor_source` tracks the initiator).
The log has no FK to `schedule` so it survives schedule deletion.

**BusyTimesSource seam — now ACTIVE (slice 3).** `interfaces/busy_times.py`
defines the `BusyTimesSource` Protocol; `StubBusyTimesSource` (`[]`) still
exists for tests that don't need real bookings, but `ioc.py` binds the
Protocol to `BookingBusyTimesSource` (`booking/busy_source.py`) in the running
service. It queries confirmed `booking` rows for the requested hosts/window,
expanded by the owning event type's `buffer_before_minutes`/`buffer_after_minutes`.
Both the slot engine and `BookingService`'s own availability re-check use it.

**Slot engine (slice 2, buffers/limits activated in slice 3).** `GET
/api/v1/slots` returns available slot start times grouped by local calendar
date. The pipeline: batch-load event type + hosts + schedules → per-host UTC
availability (weekly hours, date overrides, travel-tz DST via `zoneinfo`) →
subtract busy intervals (real, buffer-expanded, via `BookingBusyTimesSource`) →
union across hosts (round-robin: all hosts contribute) → slice into
`duration_minutes` slots at `slot_interval_minutes` step → filter by
`min_booking_notice_minutes` → group by local date in caller's `time_zone`.
The pure core (`slots/domain.py`, `slots/timezones.py`) is IO-free and
extractable; `BookingService` reuses it unmodified for its own availability
checks. `booking_limit` is enforced at booking-create time, not subtracted
from the slots response (see `docs/API_CONTRACTS.md`).

**Write-side bookings (slice 3).** `POST /api/v1/bookings` re-validates
availability per candidate host, ranks free hosts with `rank_hosts`
(getLuckyUser: fewest future confirmed bookings → never-assigned before
assigned → oldest `last_assigned_at`), enforces `booking_limit`s for the
top-ranked host, then inserts optimistically — retrying the next ranked host
if the DB exclusion constraint (`ex_booking_no_overlap`) rejects the insert
because the slot was taken concurrently. `cancel` is a soft, idempotent status
flip; `reschedule` moves `start_time`/`end_time` in place for the *same* host
only. Every transition (`created`/`rescheduled`/`cancelled`) is appended to
`booking_change_log`.

**Booking→events outbox (slice 4a).** Every booking mutation writes an `outbox`
row — same transaction, same `AsyncSession` as the booking write — via
`publishing/outbox_writer.py::OutboxWriter`. A background dispatcher
(`publishing/dispatcher.py::run_dispatcher_loop`, started in `main.py`'s
`lifespan`) polls `outbox` on an interval, resolves `host_user_id`/
`client_user_id` → email/time_zone via event-users (`POST /api/users/by-ids`,
admin `Bearer`), builds a `booking.created`/`rescheduled`/`cancelled` CloudEvent
with a stable `ce-id` (`publishing/payload.py::build_cloudevent`), and POSTs it
to event-receiver `POST /event/booking` (raw shared-secret `Authorization`,
same generic endpoint an external booking service already uses). This is
**additive to cal.com** — no existing producer or the `/event/calcom` webhook
path is touched. **event-saver is the only active consumer at this slice**:
its projections (bookings/events/participants) reflect `event-scheduling`
bookings correctly. **event-booking does not react to these events yet** — it
resolves booking context from the cal.com DB by `booking_uid`, and an
`event-scheduling` booking has no cal.com row, so chat creation, Jitsi meeting
URLs, and reminders are a no-op for these bookings until slice 4a.2 makes
event-booking payload/API-driven instead of cal.com-DB-driven.

## Tracing

OpenTelemetry auto-instrumentation (FastAPI, asyncpg); exported via OTLP/gRPC to
the OTel collector → Tempo; gated by `OTEL_SDK_DISABLED` (off by default).

## Maturity / Known Limitations

- **event_type ETL deferred.** `scripts/etl_from_calcom.py` migrates schedules
  only. EventType/Host/BookingLimit migration from cal.com is a future branch.
- **BusyTimesSource is real (slice 3).** `BookingBusyTimesSource` backs both the
  slot engine and booking creation — organizer conflicts (including buffers) are
  subtracted for real. `StubBusyTimesSource` remains available for tests only.
- **Buffers and booking limits are ACTIVE (slice 3).** `buffer_before_minutes`/
  `buffer_after_minutes` are applied in SQL when computing busy intervals;
  `booking_limit` (`booking_count`/`booking_duration`, per period in the host's
  schedule time zone) is enforced on `POST /api/v1/bookings`.
- **Round-robin host assignment is ACTIVE (slice 3).** `booking/assignment.py`
  (`rank_hosts`) picks the host with the fewest future confirmed bookings, tie-broken
  by never-assigned-first then oldest-assignment-first.
- **Booking→events outbox is ACTIVE (slice 4a).** `booking.created`/`rescheduled`/
  `cancelled` CloudEvents are published to event-receiver via a transactional
  outbox + background dispatcher — see "Booking→events outbox" above. This
  replaces the old "no event emission" limitation for the booking-lifecycle case.
- **event-booking integration is still a no-op (slice 4a.2, deferred).** event-saver's
  projections reflect `event-scheduling` bookings, but event-booking (chat creation,
  Jitsi meeting URLs, reminder scheduling) does not react to them — it is wired to
  the cal.com DB by `booking_uid`, not to the CloudEvent payload. Making it act on
  the payload directly is the next slice.
- **`EVENT_USERS_TOKEN` is a real deploy prerequisite.** Email resolution
  (`POST /api/users/by-ids`) requires a valid event-users admin token; without one,
  every resolution attempt `401`s and the affected outbox rows retry indefinitely
  rather than reaching `sent` — see `docs/DEPENDENCIES.md`.
- **No slot reservations/holds (slice 3).** A slot returned by `GET /api/v1/slots`
  is not reserved for the caller; `POST /api/v1/bookings` re-validates and can
  still return `409` if another request won the race. The DB exclusion constraint
  is the actual concurrency guard, not application-level locking.
- **No slot caching.** Each request re-queries the DB and recomputes the pipeline.
- **No external calendar integration.** Google/Office busy-times are out of scope
  until slice 6.
- **Static single API key.** No per-caller keys or rotation mechanism (this also
  applies to the new dispatcher-side secrets — `BOOKING_API_KEY`/`EVENT_USERS_TOKEN`
  are single static values, no rotation).
- **No event emission for schedule/event-type changes.** Only booking lifecycle
  events are published (slice 4a); schedule and event-type CRUD still emit nothing
  (YAGNI — no consumers exist for those yet).

## Verification

`uv run pytest` (111 tests) covers: schedule PUT/GET/404 (including timezone
validation and overlap checks), travel PUT, change-log pagination, event-type
CRUD (create/get/list/update/delete, 409 on slug conflict, 404 on missing),
BusyTimesSource stub, ETL mapping helpers, ETL integration run, schema
validation, slot domain primitives (merge/subtract/slice), timezone helpers
(DST, travel-tz, date grouping), slot service (min_notice, empty hosts, 404),
and slots API endpoint (200, 404, 422 variants) — plus (slice 3) booking
assignment ranking, booking-limit period-bounds/exceeded pure logic,
`BookingBusyTimesSource` buffer expansion, `BookingWriteAdapter` SAVEPOINT
retry-on-conflict, and `BookingService`/HTTP-level tests: create + host
assignment, double-book 409 (DB exclusion constraint), unknown event type 404,
past `start_time` 422, cancel (idempotent, frees the slot), reschedule
(same host, conflict on cancelled booking), history chain, get/list, the full
`/api/v1/bookings` router wired through the real DI container (real
`BookingBusyTimesSource`), buffer-blocks-adjacent-slot, and `booking_count`
limit enforcement (409 on the 2nd booking past the daily limit) — plus (slice 4a)
`build_cloudevent` payload construction for all three event types,
`ReceiverClient`/`UsersClient` HTTP-contract tests (headers, auth scheme, missing-id
handling, transport-error propagation), `OutboxWriter` writing inside the caller's
transaction, and `dispatcher.dispatch_once`/`_dispatch_row` end-to-end against a
stub receiver: sent on `202`, failed (no retry) on malformed payload/`400`/`401`,
retry-with-backoff on transport errors/other statuses/email-not-found, and
`FOR UPDATE SKIP LOCKED` batching. Tests run against a real PostgreSQL (ephemeral
local cluster, or `TEST_POSTGRES_DSN`).
