# event-scheduling: Service Overview

## Domain

`event-scheduling` owns the **booking/scheduling domain model** inside the `events`
monorepo. It holds organizer schedules, event types, hosts, and booking limits, and
now also computes read-side slot availability — the foundational data layer for a
phased, incremental replacement of the external cal.com CRM.

| Slice | Scope | Status |
|-------|-------|--------|
| 1 — Domain model | Schedules, event types, ETL from cal.com | **Delivered** |
| 2 — Slot engine | Read-side slot calculation (`GET /api/v1/slots`) | **Delivered** |
| 3 — Write-side bookings | `booking` table, booking creation | Planned |
| 4 — Booker UI | Participant slot-picker SPA | Planned |
| 5 — Calendar sync | External busy-times (Google/Office) | Deferred/optional |
| 6 — Schedule editor | Organizer CRUD in their personal dashboard | Planned |

cal.com is the **one-time ETL source** for existing schedule data; after ETL it is
no longer required by this service.

## Subsystems

| Subsystem | Entry point | Toggle |
|-----------|-------------|--------|
| Schedule CRUD API (`/api/v1/schedules`) | `routers/schedule.py` | always on |
| Event-type CRUD API (`/api/v1/event-types`) | `routers/event_type.py` | always on |
| Slot availability API (`/api/v1/slots`) | `routers/slots.py` | always on |
| Ops endpoints (`/health`, `/ready`, `/metrics`) | `routes.py` | always on |
| ETL from cal.com | `scripts/etl_from_calcom.py` | run once manually |

No background tasks, no message consumers. The container is the single Alembic
migration runner (`entrypoint.sh` runs `alembic upgrade head` first).

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

**BusyTimesSource seam.** `interfaces/busy_times.py` defines the `BusyTimesSource`
Protocol; the `StubBusyTimesSource` returns `[]` (no busy times) until slice 3
backs it with real booking data.

**Slot engine (slice 2).** `GET /api/v1/slots` returns available slot start times
grouped by local calendar date. The pipeline: batch-load event type + hosts +
schedules → per-host UTC availability (weekly hours, date overrides, travel-tz DST
via `zoneinfo`) → subtract busy intervals (stub: `[]`) → union across hosts
(round-robin: all hosts contribute) → slice into `duration_minutes` slots at
`slot_interval_minutes` step → filter by `min_booking_notice_minutes` → group by
local date in caller's `time_zone`. The pure core (`slots/domain.py`,
`slots/timezones.py`) is IO-free and extractable. `buffer_before/after` and
`booking_limit` are plumbed but inert until slice 3.

## Tracing

OpenTelemetry auto-instrumentation (FastAPI, asyncpg); exported via OTLP/gRPC to
the OTel collector → Tempo; gated by `OTEL_SDK_DISABLED` (off by default).

## Maturity / Known Limitations

- **event_type ETL deferred.** `scripts/etl_from_calcom.py` migrates schedules
  only. EventType/Host/BookingLimit migration from cal.com is a future branch.
- **BusyTimesSource is a stub (slice 2).** `StubBusyTimesSource` always returns
  `[]` — no organizer conflicts are subtracted from slots. Slice 3 (write-side
  bookings) will replace it with a real implementation backed by the `booking`
  table.
- **Buffers and booking limits are inert (slice 2).** `buffer_before_minutes`,
  `buffer_after_minutes`, and `booking_limit` rows are stored and loaded but not
  applied during slot calculation. They become active in slice 3.
- **No external calendar integration.** Google/Office busy-times are out of scope
  until slice 5.
- **No slot caching.** Each request re-queries the DB and recomputes the pipeline.
- **Static single API key.** No per-caller keys or rotation mechanism.
- **No event emission.** CloudEvents about schedule changes are not published yet
  (YAGNI — no consumers exist for them).

## Verification

`uv run pytest` (53 tests) covers: schedule PUT/GET/404 (including timezone
validation and overlap checks), travel PUT, change-log pagination, event-type
CRUD (create/get/list/update/delete, 409 on slug conflict, 404 on missing),
BusyTimesSource stub, ETL mapping helpers, ETL integration run, schema
validation, slot domain primitives (merge/subtract/slice), timezone helpers
(DST, travel-tz, date grouping), slot service (min_notice, empty hosts, 404),
and slots API endpoint (200, 404, 422 variants). Tests run against a real
PostgreSQL (ephemeral local cluster, or `TEST_POSTGRES_DSN`).
