# event-scheduling: Data Model

Database name: `event_scheduling` (own PostgreSQL database on the shared postgres instance).

Migrations:
- `alembic/versions/0001_initial.py` — creates the first 8 tables (schedule/event-type domain, slices 1–2).
- `alembic/versions/0002_booking.py` — adds `booking` + `booking_change_log` (slice 3, write-side bookings); also enables the `btree_gist` extension required by the exclusion constraint below.
- `alembic/versions/0003_outbox.py` — adds `outbox` (slice 4a, transactional outbox for `booking.lifecycle` CloudEvents).
- `alembic/versions/0004_booking_reminder_sent.py` — adds `booking.reminder_sent_at` + the partial index `ix_booking_reminder` (slice 4a.3, in-service booking reminders).
- `alembic/versions/0005_external_calendar.py` — adds `external_calendar` + `external_calendar_event` (slice 5, calendar-sync: iCal-URL busy-time import).

13 tables total.

## Tables

### `schedule`

One row per organizer. The UNIQUE constraint on `owner_user_id` enforces the
single-schedule-per-organizer invariant for slice 1.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `uuid` | PK, `server_default gen_random_uuid()` | |
| `owner_user_id` | `uuid` | NOT NULL, UNIQUE (`uq_schedule_owner`) | Opaque ref to event-users |
| `name` | `text` | NOT NULL | Display name, e.g. `"Default Schedule"` |
| `time_zone` | `text` | NOT NULL | IANA zone (required; no nullable fallback) |
| `created_at` | `timestamptz` | NOT NULL, `server_default now()` | |
| `updated_at` | `timestamptz` | NOT NULL, `server_default now()` | Set by adapter on every upsert |

### `weekly_hours`

Recurring weekly availability slots. One row per day-interval; multiple rows on
the same day = split shifts. `day_of_week` uses ISO-8601 (1=Monday … 7=Sunday),
remapped from cal.com's 0=Sunday convention during ETL.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `uuid` | PK, `server_default gen_random_uuid()` | |
| `schedule_id` | `uuid` | NOT NULL, FK→`schedule.id` ON DELETE CASCADE | |
| `day_of_week` | `smallint` | NOT NULL, CHECK 1..7 (`ck_weekly_hours_dow`) | ISO 1=Mon..7=Sun |
| `start_time` | `time` | NOT NULL | Local to schedule's effective time zone |
| `end_time` | `time` | NOT NULL, CHECK > start_time (`ck_weekly_hours_range`) | |

### `date_override`

Single-date availability overrides. NULL `start_time`/`end_time` pair = full-day
block (organizer is unavailable that date).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `uuid` | PK, `server_default gen_random_uuid()` | |
| `schedule_id` | `uuid` | NOT NULL, FK→`schedule.id` ON DELETE CASCADE | |
| `date` | `date` | NOT NULL | The specific date being overridden |
| `start_time` | `time` | NULLABLE | NULL ↔ full-day block |
| `end_time` | `time` | NULLABLE | CHECK: both NULL or both NOT NULL AND end > start (`ck_date_override_range`) |

### `travel_schedule`

Temporary time-zone override during travel. Effective when `current_date` falls in
`[start_date, end_date]` (null `end_date` = open-ended). Checked before the base
schedule time zone during slot calculation (slice 2).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `uuid` | PK, `server_default gen_random_uuid()` | |
| `schedule_id` | `uuid` | NOT NULL, FK→`schedule.id` ON DELETE CASCADE | |
| `time_zone` | `text` | NOT NULL | IANA zone during travel |
| `start_date` | `date` | NOT NULL | First date of travel |
| `end_date` | `date` | NULLABLE | Last date of travel; null = open-ended |
| `prev_time_zone` | `text` | NULLABLE | Informational: zone before travel |

### `event_type`

Meeting template. `slug` is the stable, human-readable identifier (e.g.
`"30-min-intro"`). `scheduling_type` is `'round_robin'` by default (collective and
managed are out of scope).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `uuid` | PK, `server_default gen_random_uuid()` | |
| `slug` | `text` | NOT NULL, UNIQUE (`uq_event_type_slug`) | URL-safe identifier |
| `title` | `text` | NOT NULL | Display name |
| `scheduling_type` | `text` | NOT NULL, `server_default 'round_robin'` | Currently only `'round_robin'` |
| `duration_minutes` | `int` | NOT NULL | Meeting duration |
| `slot_interval_minutes` | `int` | NULLABLE | Slot granularity; null = use duration |
| `min_booking_notice_minutes` | `int` | NOT NULL, `server_default 0` | Lead time before booking |
| `buffer_before_minutes` | `int` | NOT NULL, `server_default 0` | Prep buffer before meeting |
| `buffer_after_minutes` | `int` | NOT NULL, `server_default 0` | Wrap-up buffer after meeting |
| `created_at` | `timestamptz` | NOT NULL, `server_default now()` | |
| `updated_at` | `timestamptz` | NOT NULL, `server_default now()` | |

### `host`

Junction table mapping event types to their host organizers. Composite PK.
`schedule_id` links the host to the schedule that determines their availability
(RESTRICT prevents deleting a schedule while it is still referenced by a host).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `event_type_id` | `uuid` | PK (composite), FK→`event_type.id` ON DELETE CASCADE | |
| `user_id` | `uuid` | PK (composite) | Opaque ref to event-users |
| `schedule_id` | `uuid` | NOT NULL, FK→`schedule.id` ON DELETE RESTRICT | Must exist before assigning |

### `booking_limit`

Per-event-type booking limits. The composite UNIQUE on `(event_type_id, limit_type, period)`
prevents duplicate limits for the same combination.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `uuid` | PK, `server_default gen_random_uuid()` | |
| `event_type_id` | `uuid` | NOT NULL, FK→`event_type.id` ON DELETE CASCADE | |
| `limit_type` | `text` | NOT NULL | e.g. `"count"` |
| `period` | `text` | NOT NULL | `"day"`, `"week"`, `"month"`, `"year"` |
| `value` | `int` | NOT NULL, CHECK > 0 (`ck_booking_limit_value`) | Max bookings in that period |
| — | — | UNIQUE (`uq_booking_limit`) on `(event_type_id, limit_type, period)` | |

### `schedule_change_log`

Append-only audit log. A JSONB snapshot of the full schedule bundle is written in
the same transaction as every schedule PUT. No FK to `schedule` — entries survive
schedule deletion.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `uuid` | PK, `server_default gen_random_uuid()` | |
| `owner_user_id` | `uuid` | NOT NULL | Denormalized for efficient per-owner queries |
| `schedule_id` | `uuid` | NOT NULL | No FK (intentional — survives delete) |
| `actor_source` | `text` | NOT NULL | e.g. `"admin"`, `"etl"`, `"api"` |
| `actor_user_id` | `uuid` | NULLABLE | UUID of the acting user when applicable |
| `at` | `timestamptz` | NOT NULL, `server_default now()` | When the change occurred |
| `snapshot` | `jsonb` | NOT NULL | Full bundle at the time of change |

`snapshot` shape:
```json
{
  "schedule": {"name": "...", "time_zone": "..."},
  "weekly_hours": [{"day_of_week": 1, "start_time": "09:00", "end_time": "17:00"}],
  "date_overrides": [{"date": "2026-12-31", "start_time": "10:00", "end_time": "14:00"}],
  "travel_schedules": [{"time_zone": "Asia/Almaty", "start_date": "2026-09-01", ...}]
}
```

### `booking` (slice 3)

One row per booking. Written by `booking/write_adapter.py` (`INSERT ... RETURNING`,
`UPDATE ... SET status='cancelled'`, `UPDATE ... SET start_time=..., end_time=...`
for reschedule — bookings are never deleted, only soft-cancelled or moved).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `uuid` | PK, `server_default gen_random_uuid()` | |
| `event_type_id` | `uuid` | NOT NULL, FK→`event_type.id` ON DELETE RESTRICT | Can't delete an event type with bookings |
| `host_user_id` | `uuid` | NOT NULL | Opaque ref to event-users; the assigned host |
| `client_user_id` | `uuid` | NOT NULL | Opaque ref to event-users; the booking attendee |
| `start_time` | `timestamptz` | NOT NULL | |
| `end_time` | `timestamptz` | NOT NULL, CHECK > `start_time` (`ck_booking_range`) | |
| `status` | `text` | NOT NULL, `server_default 'confirmed'`, CHECK IN `('confirmed','cancelled')` (`ck_booking_status`) | No `'rescheduled'` status — reschedule updates `start_time`/`end_time` in place and logs the transition separately |
| `attendee_time_zone` | `text` | NOT NULL | IANA zone the client booked in (display only; all scheduling math is UTC) |
| `created_at` | `timestamptz` | NOT NULL, `server_default now()` | |
| `updated_at` | `timestamptz` | NOT NULL, `server_default now()` | Bumped on reschedule/cancel |
| `reminder_sent_at` (slice 4a.3) | `timestamptz` | NULLABLE | Set by the reminder poller (`reminders/write_adapter.py::ReminderWriteAdapter.mark_sent`) once the ~1h-before reminder has been dispatched for this booking. `NULL` = not yet reminded (or eligible again). `BookingWriteAdapter.update_times` (reschedule) resets it back to `NULL` in the same `UPDATE` so a moved booking is re-armed for a fresh reminder. |

Indexes: `ix_booking_host (host_user_id, status, start_time)`,
`ix_booking_event_type (event_type_id, status, start_time)`,
`ix_booking_client (client_user_id)`.

**`ix_booking_reminder` (slice 4a.3)** — partial index backing the reminder poller's
poll query:
```sql
CREATE INDEX ix_booking_reminder ON booking (start_time)
  WHERE status = 'confirmed' AND reminder_sent_at IS NULL
```
`reminders/read_adapter.py::ReminderReadAdapter.due_bookings` selects confirmed,
not-yet-reminded bookings with `start_time` in `[now + REMINDER_SHIFT_FROM_MINUTES,
now + REMINDER_SHIFT_TO_MINUTES]` (default window `[+55m, +65m]`); the partial index
keeps that scan cheap by excluding already-reminded/cancelled rows entirely.

**`ex_booking_no_overlap` — the no-double-booking guarantee:**
```sql
ALTER TABLE booking ADD CONSTRAINT ex_booking_no_overlap
  EXCLUDE USING gist (host_user_id WITH =, tstzrange(start_time, end_time) WITH &&)
  WHERE (status = 'confirmed')
```
A PostgreSQL `EXCLUDE` constraint (GiST index, requires the `btree_gist`
extension for the equality operator on `host_user_id`) that rejects any INSERT
or UPDATE producing two **confirmed** bookings for the same `host_user_id` with
overlapping `[start_time, end_time)` ranges. This is enforced **inside the
database**, not just in application code — it is the actual concurrency guard:
`BookingService.create` optimistically inserts, and a concurrent conflicting
insert fails at the DB with `IntegrityError`, which `BookingWriteAdapter.insert`
catches and re-raises as `ConflictError` so the service can retry the next
ranked host. The `WHERE status='confirmed'` predicate means cancelling a booking
immediately frees the slot for a new confirmed booking with no cleanup needed.

### `booking_change_log` (slice 3)

Append-only transition log — one row per `created`/`rescheduled`/`cancelled`
event, written by `BookingWriteAdapter.append_log` in the same request as the
mutation. Mirrors the `from_*`/`to_*` shape needed to reconstruct a booking's
full history (`GET /api/v1/bookings/{id}/history`).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `uuid` | PK, `server_default gen_random_uuid()` | |
| `booking_id` | `uuid` | NOT NULL | No FK — intentional, entries survive booking deletion (bookings are never hard-deleted today, but the log doesn't assume that) |
| `kind` | `text` | NOT NULL, CHECK IN `('created','rescheduled','cancelled')` (`ck_booking_log_kind`) | |
| `from_start` / `from_end` | `timestamptz` | NULLABLE | NULL for `created` |
| `to_start` / `to_end` | `timestamptz` | NULLABLE | NULL for `cancelled` |
| `actor_source` | `text` | NOT NULL | e.g. `"api"`, `"admin"` — from the `actor-source` request header |
| `actor_user_id` | `uuid` | NULLABLE | From the `actor-user-id` request header, when supplied |
| `at` | `timestamptz` | NOT NULL, `server_default now()` | |

`cancel` is idempotent at the service layer: cancelling an already-cancelled
booking returns the booking without inserting a second `cancelled` row.

### `outbox` (slice 4a)

Transactional outbox for `booking.lifecycle` CloudEvents. Written by
`publishing/outbox_writer.py::OutboxWriter.write` in the **same transaction** as
the triggering booking mutation (`BookingService.create`/`reschedule`/`cancel`) —
the outbox row and the booking row commit or roll back together, so a booking
mutation can never "silently" fail to be queued for publishing. Read and
transitioned by the background dispatcher (`publishing/dispatcher.py`).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `uuid` | PK, `server_default gen_random_uuid()` | |
| `event_ce_id` | `uuid` | NOT NULL | Generated once at write time (`uuid4()`); becomes the CloudEvent `ce-id` sent downstream — stable across retries so at-least-once redelivery is safely deduped by consumers |
| `event_type` | `text` | NOT NULL, CHECK IN `('booking.created','booking.rescheduled','booking.cancelled')` (`ck_outbox_type`) | |
| `booking_uid` | `text` | NOT NULL | The booking's `id` (as text) — no FK; the outbox row's lifecycle is independent of the booking row |
| `payload` | `jsonb` | NOT NULL | Domain fields captured at write time: `host_user_id`, `client_user_id`, `start_time`, `end_time`, `attendee_time_zone`, plus `previous_start_time` (reschedule) or `cancellation_reason` (cancel) when applicable |
| `status` | `text` | NOT NULL, `server_default 'pending'`, CHECK IN `('pending','sent','failed')` (`ck_outbox_status`) | No `'sending'`/in-flight status — dispatch uses `SELECT ... FOR UPDATE SKIP LOCKED` for concurrency safety instead |
| `attempts` | `int` | NOT NULL, `server_default 0` | Incremented on every retry; feeds the backoff calculation (`5 * 2^attempts`, capped at `OUTBOX_MAX_BACKOFF_SECONDS`) |
| `next_attempt_at` | `timestamptz` | NOT NULL, `server_default now()` | Dispatcher only claims rows where this is `<= now()` |
| `last_error` | `text` | NULLABLE | Set on `failed` or on each retry (e.g. `"malformed-payload:..."`, `"users:..."`, `"email-not-found"`, `"transport:..."`, `"http:<status>"`) |
| `created_at` | `timestamptz` | NOT NULL, `server_default now()` | |
| `sent_at` | `timestamptz` | NULLABLE | Set when `status` transitions to `sent` |

Index: `ix_outbox_dispatch (status, next_attempt_at)` — backs the dispatcher's
poll query (`WHERE status='pending' AND next_attempt_at<=now() ORDER BY
created_at LIMIT :batch FOR UPDATE SKIP LOCKED`).

**Terminal states.** `sent` = event-receiver returned `202`. `failed` = event-receiver
returned `400`/`401`, or the row's own `payload` was malformed (missing/invalid
`host_user_id`/`client_user_id`) — neither is retried again. Every other outcome
(network/transport error, any other HTTP status, event-users call failing, or a
resolved participant email not found) leaves the row `pending` with `attempts`
incremented and `next_attempt_at` pushed out — it will be retried on a later tick.

### `external_calendar` (slice 5)

One row per host's connected external calendar. `host_user_id` is an opaque
reference to event-users, same convention as `schedule.owner_user_id`. Written
by `calendar/write_adapter.py::CalendarWriteAdapter`; read by
`calendar/read_adapter.py::CalendarReadAdapter` and by the background poller
(`calendar/dispatcher.py::run_calendar_sync_loop`, which only considers
`enabled` rows).

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `uuid` | PK, `server_default gen_random_uuid()` | |
| `host_user_id` | `uuid` | NOT NULL | Opaque ref to event-users |
| `kind` | `text` | NOT NULL, `server_default 'ical_url'`, CHECK IN `('ical_url')` (`ck_external_calendar_kind`) | Only one connection type today — iCal-URL subscription; no OAuth providers |
| `url` | `text` | NOT NULL | The `.ics` subscription URL fetched each sync tick; `http`/`https` only (enforced at the router, `422 ValidationError` otherwise) |
| `enabled` | `bool` | NOT NULL, `server_default true` | Poller and `ExternalCalendarBusyTimesSource` both filter `WHERE enabled` |
| `last_synced_at` | `timestamptz` | NULLABLE | Set by `mark_synced` after a successful sync tick |
| `last_error` | `text` | NULLABLE | Set by `mark_error` on fetch/parse failure; cleared (`NULL`) on the next successful sync |
| `created_at` / `updated_at` | `timestamptz` | NOT NULL, `server_default now()` | `updated_at` is bumped by both `mark_synced` and `mark_error` |
| — | — | UNIQUE (`uq_external_calendar_host_url`) on `(host_user_id, url)` | Connecting the same URL twice for the same host raises `ConflictError` (`409`) — caught via a SAVEPOINT around the INSERT, same pattern as `booking/write_adapter.py::insert` |

Index: `ix_external_calendar_enabled` — partial index on `host_user_id WHERE enabled`.

### `external_calendar_event` (slice 5)

Cached busy-interval rows expanded from one `external_calendar`'s `.ics` feed.
**Fully replaced on every sync tick** — `CalendarWriteAdapter.replace_cache`
deletes all rows for the calendar then inserts the freshly-parsed set, in one
transaction (not an incremental diff). This is the table
`ExternalCalendarBusyTimesSource.get_busy` reads from.

| Column | Type | Constraints | Notes |
|--------|------|-------------|-------|
| `id` | `uuid` | PK, `server_default gen_random_uuid()` | |
| `calendar_id` | `uuid` | NOT NULL, FK→`external_calendar.id` ON DELETE CASCADE | Deleting a calendar (`DELETE /api/v1/calendars/{id}`) drops its whole cache |
| `busy_start` / `busy_end` | `timestamptz` | NOT NULL, CHECK `busy_end > busy_start` (`ck_ext_cal_event_range`) | One row per expanded occurrence within the sync window; all-day (`VALUE=DATE`) events are stored as UTC-midnight-to-next-UTC-midnight |

Index: `ix_ext_cal_event_window (calendar_id, busy_start, busy_end)` — backs
`ExternalCalendarBusyTimesSource`'s `tstzrange(...) && tstzrange(:lo, :hi)`
overlap query.

## Referential Integrity Summary

```
schedule (owner_user_id UNIQUE)
  ├── weekly_hours    (FK schedule CASCADE)
  ├── date_override   (FK schedule CASCADE)
  ├── travel_schedule (FK schedule CASCADE)
  └── host            (FK schedule RESTRICT)

event_type (slug UNIQUE)
  ├── host          (FK event_type CASCADE)
  ├── booking_limit (FK event_type CASCADE)
  └── booking       (FK event_type RESTRICT)

external_calendar (host_user_id, url UNIQUE)
  └── external_calendar_event (FK external_calendar CASCADE)

schedule_change_log  (no FK — audit survives delete)
booking_change_log   (no FK to booking — survives delete, kind='created'|'rescheduled'|'cancelled')
outbox               (no FK — booking_uid is a text reference, not a DB FK; survives independent of booking's own lifecycle)
```
