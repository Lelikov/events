# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Run the server:**
```bash
uvicorn event_scheduling.main:app --reload --port 8004
```

**Lint and format:**
```bash
uv run ruff check .
uv run ruff format .
```

**Install dependencies:**
```bash
uv sync
```

**Tests:**
```bash
uv run pytest
```
Tests run against a real PostgreSQL. With no `TEST_POSTGRES_DSN` set, the suite
boots a throwaway local cluster via `initdb`/`pg_ctl` (Homebrew Postgres); if
neither is available the suite skips rather than failing. Point at an existing
DB with `TEST_POSTGRES_DSN=postgresql+asyncpg://...`.

**Pre-commit hooks:**
```bash
pre-commit run --all-files
```

**Alembic migrations:**
```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
alembic downgrade -1
```

**Configuration:** Requires a `.env` file. See `.env.example`.

## Architecture

Layered async FastAPI service that **owns the booking/scheduling domain model** —
organizer schedules, event types, hosts, booking limits, and (as of slice 3)
bookings themselves. Pure HTTP — no RabbitMQ, no background tasks. This is a
phased replacement of the external cal.com CRM with an in-house system inside
the `events` monorepo (slices 1–3 delivered; see `docs/SERVICE_OVERVIEW.md`).

**Request flow (schedule/event-type):**
`routers/{schedule,event_type}.py` → `controllers/{schedule,event_type}.py` → `adapters/{schedule,event_type}_db.py` → `adapters/sql.py` (`SqlExecutor`) → SQLAlchemy `AsyncSession` → PostgreSQL

**Request flow (slots):**
`routers/slots.py` → `SlotService` → `SlotsReadAdapter` (batch SQL load) + `BookingBusyTimesSource` (real, buffer-expanded, backed by `booking`) → pure domain (`slots/domain.py`, `slots/timezones.py`) → grouped response

**Request flow (bookings, slice 3):**
`routers/booking.py` → `BookingService` (`booking/service.py`) → `IBookingReadAdapter`/`IBookingWriteAdapter` (`booking/read_adapter.py`, `booking/write_adapter.py`) + `BookingBusyTimesSource` (`booking/busy_source.py`) + `ISlotsReadAdapter` (reused from slice 2, to load the event-type/host/schedule bundle) → `adapters/sql.py` → PostgreSQL

**Outbox dispatch flow (slice 4a — booking→events integration):**
`BookingService.create`/`reschedule`/`cancel` writes an `outbox` row (`publishing/outbox_writer.py::OutboxWriter.write`) in the **same transaction** as the booking mutation (same `AsyncSession`, so it can never be lost independently of the booking write). A background dispatcher task, started in `main.py`'s `lifespan` (not a request path — no RabbitMQ, no FastStream), polls on an interval:

```
outbox row (status='pending', next_attempt_at<=now())
  → publishing/dispatcher.py::dispatch_once (SELECT ... FOR UPDATE SKIP LOCKED, batched)
    → resolve host_user_id/client_user_id → email/time_zone via event-users
      POST /api/users/by-ids (publishing/users_client.py::UsersClient, Bearer EVENT_USERS_TOKEN)
    → build a booking.created|rescheduled|cancelled CloudEvent (publishing/payload.py::build_cloudevent,
      ce-id = the row's stable event_ce_id set at write time)
    → POST /event/booking on event-receiver (publishing/receiver_client.py::ReceiverClient,
      raw shared-secret Authorization: BOOKING_API_KEY — same contract/endpoint an external booking
      service already uses; NOT "Bearer ...", matches event-receiver's ingest_booking auth)
    → event-receiver routes it into events.booking.lifecycle same as any other booking.* event
      → event-saver projects it (bookings/events/participants tables)
  → 202 from event-receiver: row → 'sent'
  → 400/401 from event-receiver, or a malformed outbox payload: row → 'failed' (no further retries)
  → any other outcome (network error, other HTTP status, event-users failure/email not found):
    row stays 'pending', attempts+=1, next_attempt_at = now + backoff (5*2^attempts, capped at
    OUTBOX_MAX_BACKOFF_SECONDS)
```

At-least-once delivery: the `ce-id` is stable per outbox row (set once at write time), and event-saver's own idempotency/dedup handles re-delivery on retry. **Additive to cal.com** — this does not touch `/event/calcom`, cal.com webhooks, or any existing producer; it is a second, independent `booking.lifecycle` producer using the *same* generic `/event/booking` endpoint. **event-booking reacts to these bookings as of slice 4a.2** — its composite booking adapter reads cal.com first and, on a miss, falls back to `GET /api/v1/bookings/{id}/detail` on this service, then creates the chat, per-participant Jitsi meeting URLs, and notifications (blacklist/constraints/reject are skipped for scheduling-source bookings). Reminders are still cal.com-only (the reminder scheduler polls the cal.com DB), deferred to **slice 4a.3**.

**Layers:**

- **`routers/schedule.py`** — schedule endpoints (`PUT/GET /{owner_user_id}`,
  `PUT /{owner_user_id}/travel`, `GET /{owner_user_id}/change-log`); all under
  `/api/v1/schedules`. Converts request bodies → DTOs, calls controller via DI.
- **`routers/event_type.py`** — event-type CRUD (`POST`, `GET`, `GET/{id}`,
  `PUT/{id}`, `DELETE/{id}`); all under `/api/v1/event-types`.
- **`routers/slots.py`** — `GET /api/v1/slots`; validates `time_zone`, UTC window,
  62-day cap; calls `ISlotService`; returns `SlotsResponse`.
- **`routers/booking.py`** — booking endpoints (`POST`, `GET/{id}`, `GET` list,
  `POST/{id}/cancel`, `POST/{id}/reschedule`, `GET/{id}/history`); all under
  `/api/v1/bookings`. Reads `actor-source`/`actor-user-id` headers (default
  `"api"`/`None`) into `ActorDTO` for the change log.
- **`routes.py`** — ops endpoints (`/health`, `/ready`, `/metrics`).
- **`controllers/schedule.py`** — schedule business logic: upsert (replace-in-tx),
  travel replace, change-log pagination, validation gate.
- **`controllers/event_type.py`** — event-type CRUD + host/booking-limit cascade.
- **`adapters/schedule_db.py`** — all schedule/weekly_hours/date_override/
  travel_schedule/change-log SQL via `SqlExecutor`.
- **`adapters/event_type_db.py`** — all event_type/host/booking_limit SQL.
- **`adapters/sql.py`** — `SqlExecutor` wraps `AsyncSession` with `text()` SQL.
- **`slots/dto.py`** — frozen dataclasses for the slot engine: `EventTypeConfig`,
  `HostSchedule`, `Interval` (half-open epoch-minute range), `SlotBundle`.
- **`slots/domain.py`** — pure, IO-free slot computation: `to_epoch_min`,
  `from_epoch_min`, `merge_intervals`, `subtract_intervals`, `slice_into_slots`,
  `host_availability_intervals` (weekly/override/travel/DST). No imports of
  SQLAlchemy or HTTP — extractable to a standalone library.
- **`slots/timezones.py`** — `effective_time_zone` (travel-tz override),
  `local_interval_to_utc` (DST-aware via `zoneinfo`), `group_slots_by_local_date`.
- **`slots/read_adapter.py`** — `SlotsReadAdapter`: single batch SQL load of
  event_type + hosts + schedules + weekly_hours + date_overrides + travel_schedule.
- **`slots/service.py`** — `SlotService`: orchestrates the pipeline (load →
  per-host UTC intervals → subtract busy → union → slice → group). `SystemClock`
  provides wall-clock `now()`.
- **`slots/interfaces.py`** — `ISlotsReadAdapter`, `Clock`, `ISlotService`
  Protocols.
- **`interfaces/`** — Protocol interfaces (`ISqlExecutor`, `IScheduleDBAdapter`,
  `IScheduleController`, `IEventTypeDBAdapter`, `IEventTypeController`,
  `BusyTimesSource`) for loose coupling.
- **`interfaces/busy_times.py`** — `BusyTimesSource` Protocol + `StubBusyTimesSource`
  that returns `[]` (kept for tests/DI-independent use). **Slice-3: the production
  binding is `BookingBusyTimesSource`** (`booking/busy_source.py`), backed by the
  `booking` table.
- **`booking/dto.py`** — frozen dataclasses: `CreateBookingDTO`, `BookingDTO`,
  `HostStat` (for round-robin ranking), `BookingChangeEntryDTO`.
- **`booking/interfaces.py`** — `IBookingReadAdapter`, `IBookingWriteAdapter`,
  `IBookingService` Protocols.
- **`booking/assignment.py`** — `rank_hosts`/`pick_host`: pure getLuckyUser
  ranking (fewest future confirmed bookings first, then never-assigned before
  assigned, then oldest `last_assigned_at` first).
- **`booking/limits.py`** — pure helpers: `period_bounds_utc` (day/week/month/year
  window in the host's schedule time zone, returned in UTC) and `limit_exceeded`
  (`booking_count` vs `booking_duration` check).
- **`booking/busy_source.py`** — `BookingBusyTimesSource`: real `BusyTimesSource`
  implementation. Queries confirmed `booking` rows for the given hosts overlapping
  the window, expanding each interval by the owning event type's
  `buffer_before_minutes`/`buffer_after_minutes` in SQL (`make_interval`), with an
  optional `exclude_booking_id` (used by reschedule to ignore the booking's own row).
- **`booking/read_adapter.py`** — `BookingReadAdapter`: `get`, `list_by`,
  `history`, `limits` (booking_limit rows for an event type), `host_stats`
  (future-count + last-assigned-at per host), `period_counts` (count + total
  minutes booked in a period, for limit enforcement).
- **`booking/write_adapter.py`** — `BookingWriteAdapter`: `insert` (wraps each
  attempt in `begin_nested()`/SAVEPOINT so an `IntegrityError` from the exclusion
  constraint — raised as `ConflictError` — doesn't abort the outer transaction,
  letting the service retry the next ranked host), `update_times`, `set_cancelled`,
  `append_log`.
- **`booking/service.py`** — `BookingService`: orchestrates create (re-validate
  availability per free host via `host_availability_intervals`/`subtract_intervals`
  reused from the slot engine → rank via `rank_hosts` → enforce `booking_limit`s
  for the top-ranked host → optimistic insert with fallback to the next ranked
  host on conflict → append change-log row), `cancel` (soft, idempotent), `reschedule`
  (in-place, same host only, re-checks availability excluding its own booking),
  `get`/`list_by`/`history`.
- **`publishing/dto.py`** — frozen dataclasses: `ParticipantInfo` (email, time_zone, name, locale — the
  latter two default to `None` so existing `ParticipantInfo(email, tz)` call sites keep working),
  `OutboxRow` (id, event_ce_id, event_type, booking_uid, payload, status, attempts,
  next_attempt_at).
- **`publishing/interfaces.py`** — `IOutboxWriter`, `IReceiverClient`, `IUsersClient`
  Protocols.
- **`publishing/payload.py`** — `build_cloudevent`: pure function mapping
  `(event_type, booking_uid, ce_id, payload, host, client, now)` →
  `(ce_headers, body)` for `booking.created`/`booking.rescheduled`/`booking.cancelled`;
  no IO, dict-driven per-type body builders (no `elif`).
- **`publishing/outbox_writer.py`** — `OutboxWriter`: inserts one `outbox` row via
  the request's own `ISqlExecutor`/`AsyncSession` — runs inside the caller's
  transaction, so the row commits or rolls back atomically with the booking write.
- **`publishing/receiver_client.py`** — `ReceiverClient`: POSTs to event-receiver
  `/event/booking` with the raw `BOOKING_API_KEY` in `Authorization` (not `Bearer`).
- **`publishing/users_client.py`** — `UsersClient`: POSTs to event-users
  `/api/users/by-ids` with `Authorization: Bearer EVENT_USERS_TOKEN`; missing ids
  are silently absent from the result map (never an error).
- **`publishing/dispatcher.py`** — `dispatch_once` (one poll batch:
  `SELECT ... FOR UPDATE SKIP LOCKED`, resolve → build → publish → mark
  sent/failed/retry) and `run_dispatcher_loop` (the background poll loop wired
  into `main.py`'s lifespan; opens its own session per tick, commits after each
  batch, sleeps interruptibly on a shutdown `asyncio.Event`).
- **`dto/{schedule,event_type}.py`** — frozen dataclasses (`UpsertScheduleDTO`,
  `ScheduleBundleDTO`, `ActorDTO`, `WeeklyHourDTO`, `DateOverrideDTO`, `TravelDTO`,
  `UpsertEventTypeDTO`, `EventTypeDTO`, `HostDTO`, `BookingLimitDTO`).
- **`schemas/{schedule,event_type}.py`** — Pydantic request/response models.
- **`schemas/slots.py`** — `SlotsResponse` Pydantic model
  (`event_type_id`, `time_zone`, `slots: dict[str, list[str]]`).
- **`schemas/booking.py`** — `CreateBookingRequest`, `RescheduleRequest`,
  `BookingResponse`, `BookingListResponse`, `ChangeEntryModel`,
  `BookingHistoryResponse`; timestamps serialize as `YYYY-MM-DDThh:mm:ssZ`.
- **`validation.py`** — IANA time-zone validation and weekly-hours overlap check.
- **`auth.py`** — `require_api_key`: static `Authorization: Bearer` compared with
  `hmac.compare_digest`; gates the `/api/v1` router only.
- **`metrics.py`** — Prometheus: HTTP RED middleware.
- **`ioc.py`** — Dishka container. APP scope: `Settings`, `AsyncEngine`,
  `async_sessionmaker`, `Clock` (SystemClock), `IReceiverClient` (`ReceiverClient`),
  `IUsersClient` (`UsersClient`). REQUEST scope: `AsyncSession`,
  `ISqlExecutor`, `IScheduleDBAdapter`, `IScheduleController`, `IEventTypeDBAdapter`,
  `IEventTypeController`, `BusyTimesSource` (**`BookingBusyTimesSource`, real**),
  `ISlotsReadAdapter`, `ISlotService`, `IBookingReadAdapter`, `IBookingWriteAdapter`,
  `IOutboxWriter` (`OutboxWriter`), `IBookingService` (now takes `outbox` as a
  constructor arg). The dispatcher's `run_dispatcher_loop` is started/stopped
  directly in `main.py`'s `lifespan` (not itself a Dishka-provided service) —
  it pulls `Settings`/`async_sessionmaker`/`IUsersClient`/`IReceiverClient`/`Clock`
  out of the container once at startup.
- **`db/models.py`** — SQLAlchemy ORM models (11 tables); used by Alembic only.

## Database Tables (11)

| Table | Description |
|-------|-------------|
| `schedule` | One per organizer (`owner_user_id` UNIQUE). Holds IANA `time_zone`. |
| `weekly_hours` | Recurring weekly slots; one row per day-interval. `day_of_week` 1=Mon…7=Sun (ISO). FK→schedule CASCADE. |
| `date_override` | Single-date availability overrides. NULL times = full-day block. FK→schedule CASCADE. |
| `travel_schedule` | Temporary time-zone override for travel periods. FK→schedule CASCADE. |
| `event_type` | Meeting template: slug (UNIQUE), duration, scheduling type, buffers, notice. |
| `host` | `(event_type_id, user_id)` composite PK; references a `schedule_id`. FK→event_type CASCADE, FK→schedule RESTRICT. |
| `booking_limit` | Per-event-type limits by `limit_type`+`period` (UNIQUE). CHECK value>0. FK→event_type CASCADE. |
| `schedule_change_log` | Append-only audit log: JSONB snapshot of the full schedule bundle written on every PUT. No FK to schedule (survives delete). |
| `booking` (slice 3) | One row per booking. `EXCLUDE USING gist (host_user_id WITH =, tstzrange(start_time, end_time) WITH &&) WHERE status='confirmed'` — DB-enforced no-double-booking per host, race-safe under concurrency. FK→event_type RESTRICT. |
| `booking_change_log` (slice 3) | Append-only transition log (`created`/`rescheduled`/`cancelled`) per booking, written in the same statement as the mutation. No FK to `booking` (kept for parity with `schedule_change_log`'s survive-delete pattern, though bookings are soft-cancelled, never deleted). |
| `outbox` (slice 4a) | Transactional outbox for `booking.lifecycle` CloudEvents. One row per booking mutation, written in the same transaction. `status` IN `('pending','sent','failed')`; `event_type` IN `('booking.created','booking.rescheduled','booking.cancelled')`; `event_ce_id` is the stable `ce-id` used for at-least-once dedup downstream; `next_attempt_at`/`attempts`/`last_error` drive the backoff retry loop. No FK (booking identity is carried as `booking_uid` text, not a DB FK, so the outbox row survives independent of the booking row's lifecycle). Index `ix_outbox_dispatch (status, next_attempt_at)` backs the dispatcher's poll query. |

## Endpoints

| Method | Path | Auth | Behaviour |
|--------|------|------|-----------|
| PUT | `/api/v1/schedules/{owner_user_id}` | Bearer | Upsert schedule bundle (schedule + weekly_hours + date_overrides) atomically; appends change-log snapshot |
| GET | `/api/v1/schedules/{owner_user_id}` | Bearer | Return full schedule bundle; `404` if not found |
| PUT | `/api/v1/schedules/{owner_user_id}/travel` | Bearer | Replace all travel_schedule rows for this owner atomically |
| GET | `/api/v1/schedules/{owner_user_id}/change-log` | Bearer | Paginated change-log (`?limit=50&offset=0`) |
| POST | `/api/v1/event-types` | Bearer | Create event type with hosts + booking limits; `201` |
| GET | `/api/v1/event-types` | Bearer | List all event types |
| GET | `/api/v1/event-types/{id}` | Bearer | Get single event type; `404` if not found |
| PUT | `/api/v1/event-types/{id}` | Bearer | Replace event type (hosts + limits cascade-deleted then re-inserted) |
| DELETE | `/api/v1/event-types/{id}` | Bearer | Delete event type; `204` |
| GET | `/api/v1/slots` | Bearer | Return available slots for an event type. Query params: `event_type_id` (UUID), `start` (ISO-8601 datetime, UTC window start), `end` (ISO-8601 datetime, UTC window end), `time_zone` (IANA). Responses: `200` `{event_type_id, time_zone, slots: {local_date: [utc_iso_z]}}`, `404` unknown event type, `422` invalid time zone / `end <= start` / window > 62 days. **Slice 3: busy times now come from `BookingBusyTimesSource`** (real, backed by confirmed `booking` rows, expanded by the event type's `buffer_before/after_minutes`) — slots overlapping a confirmed booking (or its buffer) are excluded. |
| POST | `/api/v1/bookings` | Bearer | Create a booking (slice 3). Body: `{event_type_id, client_user_id, start_time, attendee_time_zone}`. Re-validates availability server-side, assigns a host via round-robin (`rank_hosts`: fewest future confirmed bookings, then never-assigned, then oldest `last_assigned_at`), enforces `booking_limit`s for the assigned host's period, inserts optimistically (falls back to the next ranked host on `ConflictError` from the DB exclusion constraint). `201`; `404` unknown event type; `422` past `start_time` / invalid `attendee_time_zone`; `409` no host available / limit exceeded / slot taken concurrently by all ranked hosts. |
| GET | `/api/v1/bookings/{id}` | Bearer | Get a single booking; `404` if not found |
| GET | `/api/v1/bookings/{id}/detail` | Bearer | Participant-enriched booking view for event-booking (slice 4a.2). Resolves host/client user ids → email/name/time_zone/locale via event-users `POST /api/users/by-ids`; client `time_zone` = booking's `attendee_time_zone`. Response `{uid, title, start_time, end_time, status, host{email,name,time_zone,locale}, client{email,name,time_zone,locale}}`; `404` if not found. event-booking's composite adapter falls back here when a uid isn't in cal.com, then provisions chat/Jitsi/notifications. |
| GET | `/api/v1/bookings?host_user_id=\|client_user_id=` | Bearer | List bookings; exactly one of `host_user_id`/`client_user_id` required (`422` otherwise), optional `from_`/`to` window |
| POST | `/api/v1/bookings/{id}/cancel` | Bearer | Soft-cancel (`status='confirmed'→'cancelled'`); idempotent (second call returns the already-cancelled booking, no duplicate log row); frees the slot (exclusion constraint only applies `WHERE status='confirmed'`) |
| POST | `/api/v1/bookings/{id}/reschedule` | Bearer | In-place move to a new `start_time`, **same host only**; re-checks host availability excluding the booking's own row (`exclude_booking_id`); `409` if cancelled or the new slot isn't free; `404` unknown booking |
| GET | `/api/v1/bookings/{id}/history` | Bearer | Ordered `booking_change_log` entries (`created`→`rescheduled`*→`cancelled`?) |
| GET | `/health` | public | Liveness — no deps |
| GET | `/ready` | public | Static readiness probe — returns `{"status":"ready"}` with no DB check |
| GET | `/metrics` | public | Prometheus exposition |

Error codes: `422 ValidationError`, `404 NotFoundError`, `409 ConflictError`.

**Slice 4a:** `POST /api/v1/bookings`, `POST /{id}/cancel`, and `POST /{id}/reschedule`
each write an `outbox` row in the same transaction as the booking mutation. The HTTP
response is unaffected either way — publishing to the events pipeline happens
asynchronously, out-of-band, via the background dispatcher (see `publishing/dispatcher.py`
above); a slow or unreachable event-receiver/event-users never adds latency or a
failure mode to these endpoints.

## Configuration

| Env var | Meaning |
|---------|---------|
| `POSTGRES_DSN` | asyncpg URL for the service's own `event_scheduling` DB |
| `SCHEDULING_API_KEY` | Static bearer key gating `/api/v1/*` |
| `LOG_LEVEL` | Log level (default `INFO`) |
| `DEBUG` | Console log rendering (default `false`) |
| `EVENT_RECEIVER_URL` | Base URL of event-receiver, e.g. `http://event-receiver:8888` (dispatcher only) |
| `BOOKING_API_KEY` | Raw shared secret sent in `Authorization` (not `Bearer`) to event-receiver `POST /event/booking` — **must match** event-receiver's own `BOOKING_API_KEY` (dispatcher only) |
| `EVENT_USERS_URL` | Base URL of event-users, e.g. `http://event-users:8888` (dispatcher only) |
| `EVENT_USERS_TOKEN` | `Authorization: Bearer` token for event-users `POST /api/users/by-ids`, gated by `require_admin` — **needs a real admin token** in any environment where email resolution must succeed (dispatcher only); see `docs/DEPENDENCIES.md` |
| `OUTBOX_DISPATCH_INTERVAL` | Seconds between dispatcher poll ticks (default `5.0`) |
| `OUTBOX_BATCH_SIZE` | Max outbox rows claimed per tick (default `50`) |
| `OUTBOX_MAX_BACKOFF_SECONDS` | Cap on the exponential retry backoff (default `300`) |

## ETL from cal.com

`scripts/etl_from_calcom.py` — one-time migration of organizer schedules from the
cal.com DB (`Schedule`, `Availability`, `users` tables) to `event_scheduling`.

- Migrates only the **default schedule** per organizer (`defaultScheduleId`).
- Resolves cal.com `email` → event-users UUID via a caller-supplied callback.
- Row-resilient: skipped rows are logged in `EtlReport.skips`; the run never aborts.
- Writes a baseline `schedule_change_log` snapshot with `actor_source='etl'`.

**event_type ETL is DEFERRED.** The `EtlReport` reserves an `event_type` counter
(remains 0). EventType/Host/BookingLimit migration is a separate future branch.

`scripts/etl_mapping.py` — pure-function helpers: `remap_day_of_week` (cal.com
0=Sun → ISO 1=Mon), `resolve_time_zone`, `expand_weekly`, `expand_booking_limits`.

## BusyTimesSource Seam

`interfaces/busy_times.py` defines `BusyTimesSource` (Protocol) and
`StubBusyTimesSource` (returns `[]` always; still used directly in unit tests
that don't need real bookings). **The seam is now ACTIVE**: `ioc.py` binds
`BusyTimesSource` to `BookingBusyTimesSource` (`booking/busy_source.py`), which
queries confirmed `booking` rows for the requested hosts/window, expanded by the
owning event type's `buffer_before_minutes`/`buffer_after_minutes`. Both
`SlotService` (read-side, `GET /api/v1/slots`) and `BookingService` (write-side
availability re-check) call `get_busy(user_ids, window, exclude_booking_id=...)`
through the same Protocol.

**Slice-3 maturity notes:**
- `BusyTimesSource` is `BookingBusyTimesSource` (real) — slots and booking creation both exclude time overlapping a confirmed booking or its buffer.
- `buffer_before_minutes` / `buffer_after_minutes` are applied in SQL when computing busy intervals (`make_interval(mins => ...)`), not in the pure domain layer.
- `booking_limit` rows (`booking_count`/`booking_duration`, per `day`/`week`/`month`/`year` in the assigned host's schedule time zone) are enforced on `POST /api/v1/bookings` — see `booking/limits.py`.
- Round-robin host assignment (`booking/assignment.py`) is active: fewest future confirmed bookings first, then never-assigned before assigned, then oldest `last_assigned_at`.
- The pure core (`slots/domain.py`, `slots/timezones.py`) is IO-free and extractable; reused unmodified by `BookingService`.
- No slot caching. No reservations/holds (create is re-validate-then-insert, race-safe only via the DB exclusion constraint).

**Slice-4a maturity notes (booking→events outbox integration):**
- Booking mutations now publish `booking.created`/`booking.rescheduled`/`booking.cancelled`
  CloudEvents via a transactional outbox + background dispatcher — see `publishing/`
  above and the "Outbox dispatch flow" section. Still no RabbitMQ/FastStream consumer
  in this service — it is purely a producer, over plain HTTP, still no message broker.
- **Additive to cal.com**: `/event/calcom`, the cal.com webhook signature flow, and
  all existing producers are untouched. `event-scheduling` is a second, independent
  `booking.lifecycle` producer using the same generic `/event/booking` endpoint that
  an external booking service already targets.
- **Consumers = event-saver + event-booking.** event-saver's projections (bookings/
  events/participants tables) reflect `event-scheduling` bookings correctly.
- **event-booking now reacts to these bookings (slice 4a.2).** event-booking's composite
  booking adapter reads cal.com first and falls back to `GET /api/v1/bookings/{id}/detail`
  on this service when a `booking_uid` isn't in the cal.com DB. On a scheduling-source
  booking it creates the GetStream chat, mints per-participant Jitsi meeting URLs, and
  sends notifications — skipping the blacklist/constraints/reject sub-flow (those apply to
  cal.com bookings only; scheduling bookings are pre-validated upstream). **Reminders remain
  cal.com-only** (the reminder scheduler still polls the cal.com DB) — deferred to slice 4a.3.
- **`EVENT_USERS_TOKEN` is a real deploy prerequisite, not just a default.** event-users'
  `POST /api/users/by-ids` is gated by `require_admin`; a wrong/absent/non-admin token
  401s. `UsersClient.by_ids` turns that into an `httpx.HTTPStatusError` via
  `raise_for_status()`, which the dispatcher's generic `except Exception` treats as a
  transient failure — it retries with backoff rather than marking the row `failed`
  (the `{400, 401}` → `failed` short-circuit in `dispatcher.py` only applies to the
  *event-receiver* response, not to `UsersClient`). Net effect: without a valid admin
  token, outbox rows for real bookings retry forever and never reach `sent` — see
  `docs/DEPENDENCIES.md`.

## Service Documentation

- `docs/SERVICE_OVERVIEW.md` — architecture, maturity, replacement roadmap
- `docs/API_CONTRACTS.md` — HTTP endpoints, request/response schemas
- `docs/DATA_MODEL.md` — 11 tables with columns and constraints
- `docs/DEPENDENCIES.md` — runtime dependencies and failure modes
- `docs/AUDIT.md` — audit findings for this service

Cross-service architecture docs live in the monorepo root `../docs/`.

Domain model spec: `../docs/superpowers/specs/2026-07-03-event-scheduling-domain-model-design.md`
Domain model plan: `../docs/superpowers/plans/2026-07-03-event-scheduling-domain-model.md`
Slot engine spec: `../docs/superpowers/specs/2026-07-05-event-scheduling-slot-engine-design.md`
Slot engine plan: `../docs/superpowers/plans/2026-07-05-event-scheduling-slot-engine.md`
Booking write-side spec: `../docs/superpowers/specs/2026-07-05-event-scheduling-booking-write-side-design.md`
Booking write-side plan: `../docs/superpowers/plans/2026-07-05-event-scheduling-booking-write-side.md`
