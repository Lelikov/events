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
organizer schedules, event types, hosts, and booking limits. Pure HTTP — no
RabbitMQ, no background tasks. This is **slice 1** of a phased replacement of the
external cal.com CRM with an in-house system inside the `events` monorepo.

**Request flow (schedule/event-type):**
`routers/{schedule,event_type}.py` → `controllers/{schedule,event_type}.py` → `adapters/{schedule,event_type}_db.py` → `adapters/sql.py` (`SqlExecutor`) → SQLAlchemy `AsyncSession` → PostgreSQL

**Request flow (slots):**
`routers/slots.py` → `SlotService` → `SlotsReadAdapter` (batch SQL load) + `StubBusyTimesSource` (stub, returns `[]`) → pure domain (`slots/domain.py`, `slots/timezones.py`) → grouped response

**Layers:**

- **`routers/schedule.py`** — schedule endpoints (`PUT/GET /{owner_user_id}`,
  `PUT /{owner_user_id}/travel`, `GET /{owner_user_id}/change-log`); all under
  `/api/v1/schedules`. Converts request bodies → DTOs, calls controller via DI.
- **`routers/event_type.py`** — event-type CRUD (`POST`, `GET`, `GET/{id}`,
  `PUT/{id}`, `DELETE/{id}`); all under `/api/v1/event-types`.
- **`routers/slots.py`** — `GET /api/v1/slots`; validates `time_zone`, UTC window,
  62-day cap; calls `ISlotService`; returns `SlotsResponse`.
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
  that returns `[]`. Slice-1/2 seam; backed by real booking data in slice 3.
- **`dto/{schedule,event_type}.py`** — frozen dataclasses (`UpsertScheduleDTO`,
  `ScheduleBundleDTO`, `ActorDTO`, `WeeklyHourDTO`, `DateOverrideDTO`, `TravelDTO`,
  `UpsertEventTypeDTO`, `EventTypeDTO`, `HostDTO`, `BookingLimitDTO`).
- **`schemas/{schedule,event_type}.py`** — Pydantic request/response models.
- **`schemas/slots.py`** — `SlotsResponse` Pydantic model
  (`event_type_id`, `time_zone`, `slots: dict[str, list[str]]`).
- **`validation.py`** — IANA time-zone validation and weekly-hours overlap check.
- **`auth.py`** — `require_api_key`: static `Authorization: Bearer` compared with
  `hmac.compare_digest`; gates the `/api/v1` router only.
- **`metrics.py`** — Prometheus: HTTP RED middleware.
- **`ioc.py`** — Dishka container. APP scope: `Settings`, `AsyncEngine`,
  `async_sessionmaker`, `Clock` (SystemClock), `BusyTimesSource` (StubBusyTimesSource).
  REQUEST scope: `AsyncSession`, `ISqlExecutor`, `IScheduleDBAdapter`,
  `IScheduleController`, `IEventTypeDBAdapter`, `IEventTypeController`,
  `ISlotsReadAdapter`, `ISlotService`.
- **`db/models.py`** — SQLAlchemy ORM models (8 tables); used by Alembic only.

## Database Tables (8)

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
| GET | `/api/v1/slots` | Bearer | Return available slots for an event type. Query params: `event_type_id` (UUID), `start` (ISO-8601 datetime, UTC window start), `end` (ISO-8601 datetime, UTC window end), `time_zone` (IANA). Responses: `200` `{event_type_id, time_zone, slots: {local_date: [utc_iso_z]}}`, `404` unknown event type, `422` invalid time zone / `end <= start` / window > 62 days. Note: busy times are still provided by `StubBusyTimesSource` (returns `[]`); `buffer_before/after` and `booking_limit` are plumbed but inert until slice 3. |
| GET | `/health` | public | Liveness — no deps |
| GET | `/ready` | public | Static readiness probe — returns `{"status":"ready"}` with no DB check |
| GET | `/metrics` | public | Prometheus exposition |

Error codes: `422 ValidationError`, `404 NotFoundError`, `409 ConflictError`.

## Configuration

| Env var | Meaning |
|---------|---------|
| `POSTGRES_DSN` | asyncpg URL for the service's own `event_scheduling` DB |
| `SCHEDULING_API_KEY` | Static bearer key gating `/api/v1/*` |
| `LOG_LEVEL` | Log level (default `INFO`) |
| `DEBUG` | Console log rendering (default `false`) |

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
`StubBusyTimesSource` (returns `[]` always). The slot engine (`SlotService`) calls
`get_busy(user_ids, window)` per host; the stub means no organizer conflicts are
subtracted until slice 3 (write-side bookings) provides a real implementation
backed by the `booking` table.

**Slice-2 maturity notes:**
- `BusyTimesSource` is `StubBusyTimesSource` — slots are never blocked by existing bookings.
- `buffer_before_minutes` / `buffer_after_minutes` on `EventTypeConfig` are loaded and stored but not yet applied to interval subtraction.
- `booking_limit` rows are loaded (via `event_type_db`) but not enforced during slot calculation.
- The pure core (`slots/domain.py`, `slots/timezones.py`) is IO-free and extractable.

## Service Documentation

- `docs/SERVICE_OVERVIEW.md` — architecture, maturity, replacement roadmap
- `docs/API_CONTRACTS.md` — HTTP endpoints, request/response schemas
- `docs/DATA_MODEL.md` — 8 tables with columns and constraints
- `docs/DEPENDENCIES.md` — runtime dependencies and failure modes
- `docs/AUDIT.md` — audit findings for this service

Cross-service architecture docs live in the monorepo root `../docs/`.

Domain model spec: `../docs/superpowers/specs/2026-07-03-event-scheduling-domain-model-design.md`
Domain model plan: `../docs/superpowers/plans/2026-07-03-event-scheduling-domain-model.md`
Slot engine spec: `../docs/superpowers/specs/2026-07-05-event-scheduling-slot-engine-design.md`
Slot engine plan: `../docs/superpowers/plans/2026-07-05-event-scheduling-slot-engine.md`
