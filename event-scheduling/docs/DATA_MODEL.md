# event-scheduling: Data Model

Database name: `event_scheduling` (own PostgreSQL database on the shared postgres instance).

Migrations:
- `alembic/versions/0001_initial.py` — creates the first 8 tables (schedule/event-type domain, slices 1–2).
- `alembic/versions/0002_booking.py` — adds `booking` + `booking_change_log` (slice 3, write-side bookings); also enables the `btree_gist` extension required by the exclusion constraint below.

10 tables total.

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

Indexes: `ix_booking_host (host_user_id, status, start_time)`,
`ix_booking_event_type (event_type_id, status, start_time)`,
`ix_booking_client (client_user_id)`.

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

schedule_change_log  (no FK — audit survives delete)
booking_change_log   (no FK to booking — survives delete, kind='created'|'rescheduled'|'cancelled')
```
