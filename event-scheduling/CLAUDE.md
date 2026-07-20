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

At-least-once delivery: the `ce-id` is stable per outbox row (set once at write time), and event-saver's own idempotency/dedup handles re-delivery on retry. **Additive to cal.com** — this does not touch `/event/calcom`, cal.com webhooks, or any existing producer; it is a second, independent `booking.lifecycle` producer using the *same* generic `/event/booking` endpoint. **event-booking reacts to these bookings as of slice 4a.2** — its composite booking adapter reads cal.com first and, on a miss, falls back to `GET /api/v1/bookings/{id}/detail` on this service, then creates the chat, per-participant Jitsi meeting URLs, and notifications (blacklist/constraints/reject are skipped for scheduling-source bookings). **Reminders for these bookings are now in-service too, as of slice 4a.3** — see the "Reminder dispatch flow" section below; the cal.com reminder poller in event-booking is untouched and continues to handle cal.com-sourced bookings.

**Reminder dispatch flow (slice 4a.3 — in-service booking reminders):** A second, independent background poller (`reminders/dispatcher.py::run_reminder_loop`), started alongside the outbox dispatcher in `main.py`'s `lifespan`, sends one ~1h-before reminder per confirmed booking **for bookings created in this service** (booking-write-side, slice 3) — it does not touch cal.com's bookings or its own reminder scheduler.

```
every REMINDER_INTERVAL_SECONDS tick (default 60s)
  → reminders/read_adapter.py::ReminderReadAdapter.due_bookings:
      SELECT confirmed bookings WHERE reminder_sent_at IS NULL
      AND start_time BETWEEN now()+REMINDER_SHIFT_FROM_MINUTES AND now()+REMINDER_SHIFT_TO_MINUTES
      (default window [+55m, +65m]; backed by the partial index ix_booking_reminder)
  → for each due booking: resolve host_user_id/client_user_id → email/name/locale
    via the existing IUsersClient.by_ids (publishing/users_client.py, same batch call the outbox uses)
    → skip (log + continue) if either participant can't be resolved
  → reminders/payload.py::build_reminder_command + build_reminder_sent:
      POST notification.send_requested (trigger_event=BOOKING_REMINDER, recipients=[organizer, client],
      template_data) then POST booking.reminder_sent ({booking_uid, email}) — both via the SAME
      IReceiverClient the outbox dispatcher uses (raw BOOKING_API_KEY, POST /event/booking, ce-source=booking)
  → reminders/write_adapter.py::ReminderWriteAdapter.mark_sent stamps booking.reminder_sent_at=now()
    (idempotent: WHERE reminder_sent_at IS NULL, so a redelivered tick can't double-stamp)
```

- **Reschedule re-arms the reminder.** `BookingWriteAdapter.update_times` (used by
  `POST /{id}/reschedule`) sets `reminder_sent_at=NULL` in the same `UPDATE` as the
  time change, so a moved booking becomes eligible for a fresh reminder against its
  new `start_time`.
- **`REMINDER_ENABLED` toggle.** When `false`, `main.py` never starts the reminder
  task at all (the outbox dispatcher is unaffected) — a hard kill-switch, not just a
  no-op tick.
- **Reuses existing config/clients** — no new `EVENT_RECEIVER_URL`/`BOOKING_API_KEY`/
  `EVENT_USERS_URL`/`EVENT_USERS_TOKEN`; the reminder poller shares the same
  `IReceiverClient`/`IUsersClient` instances the outbox dispatcher uses.
- **Additive, single-service.** This only covers bookings owned by `event-scheduling`
  (slice 3 write-side). The cal.com reminder scheduler in `event-booking` (which polls
  the cal.com DB) is completely untouched — the two reminder paths run independently
  against disjoint booking sources.

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
  `(ce_headers, body)` for `booking.created`/`booking.rescheduled`/`booking.reassigned`/`booking.cancelled`;
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
- **`reminders/dto.py`** — `DueBookingDTO` (frozen): the fields
  `ReminderReadAdapter.due_bookings` returns per candidate booking (id, event
  type, host/client user ids, start/end, attendee time zone, event-type title).
- **`reminders/interfaces.py`** — `IReminderReadAdapter`, `IReminderWriteAdapter`
  Protocols. Reuses `IUsersClient`/`IReceiverClient` from `publishing/interfaces.py`
  — no separate client stack for reminders.
- **`reminders/read_adapter.py`** — `ReminderReadAdapter.due_bookings`: selects
  confirmed, not-yet-reminded bookings with `start_time` in
  `[now+shift_from_minutes, now+shift_to_minutes]`, backed by the partial index
  `ix_booking_reminder`.
- **`reminders/write_adapter.py`** — `ReminderWriteAdapter.mark_sent`: `UPDATE
  booking SET reminder_sent_at=:now WHERE id=:id AND reminder_sent_at IS NULL`
  (idempotent — a second call for an already-reminded booking is a no-op).
- **`reminders/payload.py`** — `build_reminder_command`/`build_reminder_sent`:
  pure functions building the `notification.send_requested`
  (`trigger_event=BOOKING_REMINDER`) and `booking.reminder_sent` CloudEvent
  headers/bodies; deterministic `ce-id` (`uuid5` of a fixed namespace + booking
  uid) so redelivery on a crashed tick re-emits the same id.
- **`reminders/dispatcher.py`** — `remind_once` (one poll batch: load due
  bookings → resolve participants via `IUsersClient.by_ids` → publish both
  CloudEvents via `IReceiverClient` → `mark_sent`) and `run_reminder_loop` (the
  background poll loop wired into `main.py`'s lifespan alongside the outbox
  dispatcher; own session per tick, commits after each batch, survives a
  failing tick, sleeps interruptibly on the same shutdown `asyncio.Event`).
- **`calendar/dto.py`** — `ExternalCalendarDTO` (frozen): `id`, `host_user_id`,
  `kind`, `url`, `enabled`, `last_synced_at`, `last_error`.
- **`calendar/interfaces.py`** — `IICalClient`, `IICalParser`,
  `ICalendarReadAdapter`, `ICalendarWriteAdapter` Protocols.
- **`calendar/ical_client.py`** — `ICalClient.fetch(url) -> bytes`: `http(s)`-only
  fetch (else `ValidationError`) via `httpx.AsyncClient` (`follow_redirects=True`,
  `CALENDAR_FETCH_TIMEOUT_SECONDS`); non-2xx → `UpstreamError`.
- **`calendar/ical_parser.py`** — `ICalParser.expand(ics_bytes, window) ->
  list[BusyInterval]`: parses with `icalendar` + `recurring_ical_events`, clips
  each occurrence to the window, skips `TRANSP:TRANSPARENT`/`STATUS:CANCELLED`,
  and turns `VALUE=DATE` all-day events into one UTC-midnight-to-midnight
  `BusyInterval`.
- **`calendar/read_adapter.py`** / **`calendar/write_adapter.py`** —
  `CalendarReadAdapter`/`CalendarWriteAdapter`: CRUD on `external_calendar`
  (`create` wraps the `uq_external_calendar_host_url` `IntegrityError` in a
  SAVEPOINT → `ConflictError`, same pattern as `booking/write_adapter.py`) plus
  `replace_cache` (delete+insert `external_calendar_event` for one calendar),
  `mark_synced`, `mark_error`.
- **`calendar/busy_source.py`** — `ExternalCalendarBusyTimesSource.get_busy`:
  reads cached busy intervals from `external_calendar_event` for enabled
  calendars belonging to the requested hosts, overlapping the window.
- **`calendar/composite_busy.py`** — `CompositeBusyTimesSource`: unions
  `BookingBusyTimesSource` + `ExternalCalendarBusyTimesSource`; forwards
  `exclude_booking_id` only to the booking source.
- **`calendar/sync_service.py`** — `sync_calendar`: fetch → expand → full
  replace-cache → `mark_synced`, or `mark_error` on any fetch/parse exception
  (last good cache left intact).
- **`calendar/dispatcher.py`** — `run_calendar_sync_loop`: background poller
  (own session per tick, syncs every enabled calendar, commits, sleeps
  interruptibly on the shared shutdown `asyncio.Event`) — same shape as
  `publishing/dispatcher.py`/`reminders/dispatcher.py`.
- **`routers/calendar.py`** / **`schemas/calendar.py`** — `/api/v1/calendars`
  endpoints (connect/list/delete/sync) and their Pydantic request/response models.
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
  `IEventTypeController`, `BusyTimesSource` (**`CompositeBusyTimesSource`, slice 5**
  — unions `BookingBusyTimesSource` with `ExternalCalendarBusyTimesSource`),
  `ISlotsReadAdapter`, `ISlotService`, `IBookingReadAdapter`, `IBookingWriteAdapter`,
  `IOutboxWriter` (`OutboxWriter`), `IBookingService` (now takes `outbox` as a
  constructor arg), `IReminderReadAdapter` (`ReminderReadAdapter`),
  `IReminderWriteAdapter` (`ReminderWriteAdapter`), `ICalendarReadAdapter`
  (`CalendarReadAdapter`), `ICalendarWriteAdapter` (`CalendarWriteAdapter`),
  `IICalClient` (`ICalClient`). Both the outbox dispatcher's
  `run_dispatcher_loop` and the reminder poller's `run_reminder_loop` are
  started/stopped directly in `main.py`'s `lifespan` (neither is itself a
  Dishka-provided service) — each pulls `Settings`/`async_sessionmaker`/
  `IUsersClient`/`IReceiverClient`/`Clock` out of the container once at startup
  and constructs its own read/write adapters per tick from a fresh session.
  The calendar-sync poller (`run_calendar_sync_loop`, slice 5) is started the
  same way, alongside the other two.
- **`db/models.py`** — SQLAlchemy ORM models (14 tables); used by Alembic only.

## Database Tables (14)

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
| `booking` (slice 3) | One row per booking. `EXCLUDE USING gist (host_user_id WITH =, tstzrange(start_time, end_time) WITH &&) WHERE status='confirmed'` — DB-enforced no-double-booking per host, race-safe under concurrency. FK→event_type RESTRICT. `field_answers` (booking fields phase 1) is a JSONB snapshot `[{"key","label","type","value"}]` of the guest's answers at booking time, validated against `booking_field` and stored/echoed by `BookingWriteAdapter.insert`/`BookingReadAdapter`. |
| `booking_change_log` (slice 3) | Append-only transition log (`created`/`rescheduled`/`reassigned`/`cancelled`) per booking, written in the same statement as the mutation. No FK to `booking` (kept for parity with `schedule_change_log`'s survive-delete pattern, though bookings are soft-cancelled, never deleted). |
| `outbox` (slice 4a) | Transactional outbox for `booking.lifecycle` CloudEvents. One row per booking mutation, written in the same transaction. `status` IN `('pending','sent','failed')`; `event_type` IN `('booking.created','booking.rescheduled','booking.reassigned','booking.cancelled')`; `event_ce_id` is the stable `ce-id` used for at-least-once dedup downstream; `next_attempt_at`/`attempts`/`last_error` drive the backoff retry loop. No FK (booking identity is carried as `booking_uid` text, not a DB FK, so the outbox row survives independent of the booking row's lifecycle). Index `ix_outbox_dispatch (status, next_attempt_at)` backs the dispatcher's poll query. |
| `external_calendar` (slice 5) | One row per connected iCal-URL calendar. `kind` CHECK IN `('ical_url')`; `host_user_id`+`url` UNIQUE (`uq_external_calendar_host_url`); `enabled` gates whether the poller/busy-source consider it; `last_synced_at`/`last_error` track the most recent sync tick. |
| `external_calendar_event` (slice 5) | Busy-interval cache for one `external_calendar`. Fully replaced (delete-all + insert) on every sync tick — not an incremental diff. FK→`external_calendar.id` ON DELETE CASCADE (deleting a calendar drops its cache). CHECK `busy_end > busy_start`. Index `ix_ext_cal_event_window (calendar_id, busy_start, busy_end)`. |
| `booking_field` (booking fields phase 1) | Per-event-type configurable guest-facing form fields (`text`/`textarea`/`select`/`radio`/`checkbox`/`boolean`). `field_key` is slugified from `label` and unique per event type (`uq_booking_field_key`); `options` (JSONB) holds `[{"value","label"}]` for choice types; `position` orders display. FK→event_type CASCADE. Managed via `PUT/GET /api/v1/event-types/{id}/booking-fields` (`booking_fields/` module); read by `BookingService.create` to validate `POST /api/v1/bookings`' `field_answers` (`booking_fields.domain.validate_and_snapshot`) before the snapshot is stored on `booking.field_answers`. |

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
| POST | `/api/v1/bookings/{id}/reassign` | Bearer | Reassign to another host of the same event type (body `{new_host_user_id}`). **Same time, different host**: validates the target is a host of the event type (`422`) and free at the booking's time (`409`, no notice gate — a last-minute hand-off is allowed); writes a `reassigned` change-log + a `booking.reassigned` outbox row carrying the previous host. `422` same host / not a host; `409` cancelled / target busy; `404` unknown booking |
| GET | `/api/v1/bookings/{id}/history` | Bearer | Ordered `booking_change_log` entries (`created`→`rescheduled`*→`cancelled`?) |
| POST | `/api/v1/calendars` | Bearer | Connect a host's iCal-URL calendar (slice 5). Body: `{host_user_id, url}`. `201`; `422` if `url` isn't `http(s)://`; `409` if this `host_user_id`+`url` is already connected. |
| GET | `/api/v1/calendars?host_user_id=` | Bearer | List a host's connected calendars (id, kind, url, enabled, last_synced_at, last_error). |
| DELETE | `/api/v1/calendars/{id}` | Bearer | Disconnect a calendar; `204`. Cascade-deletes its cached busy events. |
| POST | `/api/v1/calendars/{id}/sync` | Bearer | Force an immediate fetch+expand+replace-cache sync outside the poller's own interval; `404` unknown id. |
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
| `REMINDER_ENABLED` | Kill-switch for the reminder poller (default `true`); when `false`, `main.py` never starts the reminder background task |
| `REMINDER_INTERVAL_SECONDS` | Seconds between reminder poll ticks (default `60`) |
| `REMINDER_SHIFT_FROM_MINUTES` | Lower bound of the "due soon" window, minutes before `start_time` (default `55`) |
| `REMINDER_SHIFT_TO_MINUTES` | Upper bound of the "due soon" window, minutes before `start_time` (default `65`) |
| `REMINDER_BATCH_SIZE` | Max due bookings claimed per reminder tick (default `100`; not overridden in `docker-compose.services.yml`, default is used) |
| `CALENDAR_SYNC_ENABLED` | Kill-switch for the calendar-sync poller (default `true`); when `false`, `main.py` never starts the calendar-sync background task |
| `CALENDAR_SYNC_INTERVAL_SECONDS` | Seconds between calendar-sync poll ticks (default `300`) |
| `CALENDAR_SYNC_WINDOW_DAYS` | Rolling window (days from `now()`) over which each calendar's busy events are expanded and cached (default `62`, matches the slot-engine's own 62-day cap) |
| `CALENDAR_FETCH_TIMEOUT_SECONDS` | HTTP timeout for `ICalClient.fetch` (default `15`) |

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
that don't need real bookings). **The seam is now ACTIVE and, as of slice 5,
composite**: `ioc.py` binds `BusyTimesSource` to `CompositeBusyTimesSource`
(`calendar/composite_busy.py`), which unions `BookingBusyTimesSource`
(`booking/busy_source.py` — confirmed `booking` rows, buffer-expanded) with
`ExternalCalendarBusyTimesSource` (`calendar/busy_source.py` — cached iCal
busy events, see "Calendar sync" above). Both `SlotService` (read-side, `GET
/api/v1/slots`) and `BookingService` (write-side availability re-check) call
`get_busy(user_ids, window, exclude_booking_id=...)` through the same Protocol,
unaware that the implementation now also consults external calendars —
`exclude_booking_id` is forwarded only to the booking half of the union.

**Slice-3 maturity notes:**
- `BusyTimesSource` is `BookingBusyTimesSource` (real) — slots and booking creation both exclude time overlapping a confirmed booking or its buffer.
- `buffer_before_minutes` / `buffer_after_minutes` are applied in SQL when computing busy intervals (`make_interval(mins => ...)`), not in the pure domain layer.
- `booking_limit` rows (`booking_count`/`booking_duration`, per `day`/`week`/`month`/`year` in the assigned host's schedule time zone) are enforced on `POST /api/v1/bookings` — see `booking/limits.py`.
- Round-robin host assignment (`booking/assignment.py`) is active: fewest future confirmed bookings first, then never-assigned before assigned, then oldest `last_assigned_at`.
- The pure core (`slots/domain.py`, `slots/timezones.py`) is IO-free and extractable; reused unmodified by `BookingService`.
- No slot caching. No reservations/holds (create is re-validate-then-insert, race-safe only via the DB exclusion constraint).

**Slice-4a maturity notes (booking→events outbox integration):**
- Booking mutations now publish `booking.created`/`booking.rescheduled`/`booking.reassigned`/`booking.cancelled`
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
  cal.com bookings only; scheduling bookings are pre-validated upstream).
- **Reminders are now in-service too (slice 4a.3).** A second background poller
  (`reminders/dispatcher.py::run_reminder_loop`) sends one ~1h-before reminder per
  confirmed booking owned by this service — see the "Reminder dispatch flow" section
  above. This is additive: the cal.com reminder scheduler in `event-booking` (which
  still polls the cal.com DB directly) is untouched and keeps handling cal.com-sourced
  bookings; the two reminder paths are independent and cover disjoint booking sources.
- **`EVENT_USERS_TOKEN` is a real deploy prerequisite, not just a default.** event-users'
  `POST /api/users/by-ids` is gated by `require_admin`; a wrong/absent/non-admin token
  401s. `UsersClient.by_ids` turns that into an `httpx.HTTPStatusError` via
  `raise_for_status()`, which the dispatcher's generic `except Exception` treats as a
  transient failure — it retries with backoff rather than marking the row `failed`
  (the `{400, 401}` → `failed` short-circuit in `dispatcher.py` only applies to the
  *event-receiver* response, not to `UsersClient`). Net effect: without a valid admin
  token, outbox rows for real bookings retry forever and never reach `sent` — see
  `docs/DEPENDENCIES.md`.

**Slice-5 maturity notes (calendar sync — external busy-times via iCal URL):**
- `BusyTimesSource` is now `CompositeBusyTimesSource` — slots and booking creation
  both exclude time overlapping a confirmed booking (as before) **and** time
  covered by any of the host's enabled external iCal calendars.
- **iCal-URL subscription only, import-only.** No OAuth (Google/Microsoft
  Calendar API), no CalDAV, no export of this service's own bookings anywhere.
  A single `kind='ical_url'` is the only supported connection type today.
- **Poller-based, not real-time.** Freshness is bounded by
  `CALENDAR_SYNC_INTERVAL_SECONDS` (default 300s) — an external event created
  moments ago may not yet be reflected in availability. `POST
  /api/v1/calendars/{id}/sync` exists for an on-demand refresh.
- **Full replace per sync, not incremental.** Each tick deletes and reinserts
  the *entire* cache for a calendar; a fetch/parse failure leaves the prior
  good cache untouched rather than clearing it.
- **SSRF hardening deferred — see the SECURITY note above.** This is the
  primary known gap before wider/production exposure of this feature.
- **Additive.** `slots/` and `booking/service.py` are unchanged; the only
  wiring change is the `provide_busy_source` factory in `ioc.py`.

## Calendar sync (slice 5 — external busy-times via iCal URL)

A new `calendar/` module lets a host connect an external calendar by **iCal URL
subscription** (e.g. a Google/Outlook "secret address in iCal format" export) so
the slot engine and booking-create also treat time already busy on that external
calendar as unavailable. This is **additive and import-only**: no OAuth, no
writing/exporting events anywhere, and the existing `slots/` and
`booking/service.py` code is unchanged — the only wiring change is the
`BusyTimesSource` binding in `ioc.py`.

```
POST /api/v1/calendars {host_user_id, url}   → connects a calendar (kind='ical_url', enabled=true)
  → background poller (run_calendar_sync_loop, 3rd lifespan task, tick every
    CALENDAR_SYNC_INTERVAL_SECONDS, default 300s):
      for each enabled external_calendar row:
        ICalClient.fetch(url)                → raw .ics bytes (http/https only; UpstreamError on non-2xx)
        ICalParser.expand(bytes, window)      → list[BusyInterval] (window = [now, now+CALENDAR_SYNC_WINDOW_DAYS])
          (skips TRANSP:TRANSPARENT and STATUS:CANCELLED; all-day/VALUE=DATE
           events become UTC-midnight-to-next-UTC-midnight busy)
        CalendarWriteAdapter.replace_cache(calendar_id, events)
          → DELETE all external_calendar_event rows for this calendar, then
            INSERT the freshly-expanded set — full replace per calendar per
            tick, in one transaction (not an incremental diff)
        on fetch/parse failure: mark_error(calendar_id, now, err); the LAST
          GOOD cache is left intact (no partial/empty overwrite)
  → ExternalCalendarBusyTimesSource.get_busy(user_ids, window) reads the cache
    (external_calendar_event JOIN external_calendar WHERE enabled)
  → CompositeBusyTimesSource(booking, external).get_busy(user_ids, window, exclude_booking_id=None)
    unions BookingBusyTimesSource's busy intervals with the external ones;
    exclude_booking_id is forwarded ONLY to the booking source (the external
    source has no notion of "this service's own booking")
  → ioc.py binds BusyTimesSource → CompositeBusyTimesSource, so SlotService
    (GET /api/v1/slots) and BookingService (create/reschedule re-validation)
    pick up external busy-times with NO code change in slots/ or booking/
```

- **`CALENDAR_SYNC_ENABLED` toggle.** When `false`, `main.py` never starts the
  calendar-sync background task at all (same kill-switch pattern as
  `REMINDER_ENABLED`) — the outbox dispatcher and reminder poller are unaffected.
- **Management endpoints** (`routers/calendar.py`, `/api/v1/calendars`, gated by
  the same `require_api_key` as every other `/api/v1/*` route):
  - `POST /api/v1/calendars` `{host_user_id, url}` — connect a calendar (`409` if
    the same `host_user_id`+`url` pair already exists, via the
    `uq_external_calendar_host_url` constraint); `422` if the URL isn't `http://`/`https://`.
  - `GET /api/v1/calendars?host_user_id=` — list a host's connected calendars.
  - `DELETE /api/v1/calendars/{id}` — disconnect (cascade-deletes its cached
    `external_calendar_event` rows).
  - `POST /api/v1/calendars/{id}/sync` — force an immediate sync outside the
    poller's own interval; `404` if the calendar id is unknown.
- **iCal-URL only, import-only.** `kind` is DB-constrained to `'ical_url'`
  (`ck_external_calendar_kind`) — no Google/Microsoft OAuth flow, no CalDAV, and
  no export of this service's own bookings into anyone's calendar. Deferred/out
  of scope for this slice.

**SECURITY — SSRF hardening is DEFERRED, required before production.**
`calendar/ical_client.py::ICalClient.fetch` validates **only the URL scheme**
(`http`/`https`, else `ValidationError`) before issuing the GET with
`follow_redirects=True`. It does **not** block private/loopback/link-local/cloud
metadata IPs (e.g. `127.0.0.1`, `169.254.169.254`, RFC1918 ranges), does not
re-validate the resolved IP on each redirect hop, and is not DNS-rebinding-safe.
A host-supplied calendar URL can currently be used to probe or fetch from the
service's internal network. The `POST /api/v1/calendars` endpoint being gated by
`require_api_key` (the shared `SCHEDULING_API_KEY`) **limits** who can register a
malicious URL — it does **not eliminate** the SSRF risk, since any caller holding
that one static key can still supply an arbitrary URL. This remaining hardening
(private/loopback/metadata-IP blocking, per-redirect-hop IP re-validation, a host
allowlist, and DNS-rebinding-safe IP pinning) must land before this feature is
exposed beyond trusted admin use.

*Partially hardened (already applied):* the fetch streams the body with a **2 MiB
response-size cap** (`_MAX_ICS_BYTES` → `UpstreamError`), and sync failures persist
only a **coarse `last_error` category** (`fetch_failed`/`parse_failed`) — the raw
exception text goes to the server log, never to the DB column or the API response.

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
