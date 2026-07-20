# event-scheduling: API Contracts

Internal HTTP port **8004** (host). Container port 8888. Auth applies to
`/api/v1/*` only: `Authorization: Bearer <SCHEDULING_API_KEY>`, constant-time
compared (`hmac.compare_digest`). The ops endpoints are unauthenticated.

Optional headers on mutating schedule/travel endpoints:
- `actor-source` (str, default `"admin"`) — identifies who initiated the change.
- `actor-user-id` (UUID, optional) — user ID of the actor; stored in the change log.

## Schedule Endpoints

### PUT /api/v1/schedules/{owner_user_id}

Upsert (create or replace) the schedule bundle for an organizer. The operation
atomically replaces `weekly_hours` and `date_overrides` and appends a snapshot to
`schedule_change_log`. `travel_schedule` rows are NOT affected by this endpoint.

Request:
```json
{
  "name": "Default Schedule",
  "time_zone": "Europe/Moscow",
  "weekly_hours": [
    {"day_of_week": 1, "start_time": "09:00", "end_time": "17:00"},
    {"day_of_week": 3, "start_time": "09:00", "end_time": "17:00"}
  ],
  "date_overrides": [
    {"date": "2026-12-31", "start_time": "10:00", "end_time": "14:00"},
    {"date": "2027-01-01"}
  ]
}
```

- `day_of_week`: 1=Monday … 7=Sunday (ISO-8601).
- `date_overrides`: omitting both times = full-day block (no availability that day).
- `time_zone`: must be a valid IANA zone (e.g. `"Europe/Moscow"`, `"UTC"`).
- `weekly_hours` and `date_override` `start_time`/`end_time` must be **on the hour**
  (minute/second == 0); off-hour times are rejected.
- Validation errors: `422` if `time_zone` invalid, `weekly_hours` intervals overlap
  on the same day, or any weekly/date-override time is not a whole hour.

Response `200` — full bundle:
```json
{
  "schedule": {"id": "<uuid>", "owner_user_id": "<uuid>", "name": "Default Schedule", "time_zone": "Europe/Moscow"},
  "weekly_hours": [{"day_of_week": 1, "start_time": "09:00:00", "end_time": "17:00:00"}, ...],
  "date_overrides": [{"date": "2026-12-31", "start_time": "10:00:00", "end_time": "14:00:00"}, ...],
  "travel_schedules": []
}
```

### GET /api/v1/schedules/{owner_user_id}

Return the full schedule bundle for an organizer.

```
200  — bundle (same shape as PUT response)
404  — no schedule for this owner_user_id
```

### PUT /api/v1/schedules/{owner_user_id}/travel

Atomically replace all travel_schedule rows for this organizer. Sends an empty list
to clear all travel overrides.

Request:
```json
{
  "travel_schedules": [
    {
      "time_zone": "Asia/Almaty",
      "start_date": "2026-09-01",
      "end_date": "2026-09-10",
      "prev_time_zone": "Europe/Moscow"
    }
  ]
}
```

- `end_date`: optional (null = open-ended).
- `prev_time_zone`: optional; informational, records the zone before travel.
- Validation: `time_zone` must be a valid IANA zone; `422` otherwise.

Response `200` — full bundle (same shape as schedule PUT; includes updated `travel_schedules`).

### GET /api/v1/schedules/{owner_user_id}/change-log

Paginated change log for an organizer's schedule. Ordered by `at DESC`.

Query params: `limit` (default 50), `offset` (default 0).

Response `200`:
```json
{
  "entries": [
    {
      "id": "<uuid>",
      "at": "2026-07-03T12:00:00Z",
      "actor_source": "admin",
      "actor_user_id": null,
      "snapshot": {
        "schedule": {"name": "Default Schedule", "time_zone": "Europe/Moscow"},
        "weekly_hours": [...],
        "date_overrides": [...],
        "travel_schedules": [...]
      }
    }
  ]
}
```

## Event-Type Endpoints

### POST /api/v1/event-types

Create a new event type with optional hosts and booking limits.

Request:
```json
{
  "slug": "30-min-intro",
  "title": "30-Minute Introduction",
  "scheduling_type": "round_robin",
  "duration_minutes": 30,
  "slot_interval_minutes": null,
  "min_booking_notice_minutes": 60,
  "buffer_before_minutes": 5,
  "buffer_after_minutes": 5,
  "hosts": [
    {"user_id": "<uuid>", "schedule_id": "<uuid>"}
  ],
  "booking_limits": [
    {"limit_type": "count", "period": "day", "value": 4}
  ]
}
```

- `slug`: must be unique across all event types (`409` on conflict).
- `scheduling_type`: currently `"round_robin"` only.
- `slot_interval_minutes`: if null, defaults to `duration_minutes`.

Response `201` — full event-type response (see GET /{id}).

### GET /api/v1/event-types

List all event types.

Response `200`:
```json
{
  "items": [<EventTypeResponse>, ...]
}
```

### GET /api/v1/event-types/{id}

Get a single event type.

```
200  — EventTypeResponse
404  — unknown id
```

Response shape:
```json
{
  "id": "<uuid>",
  "slug": "30-min-intro",
  "title": "30-Minute Introduction",
  "scheduling_type": "round_robin",
  "duration_minutes": 30,
  "slot_interval_minutes": null,
  "min_booking_notice_minutes": 60,
  "buffer_before_minutes": 5,
  "buffer_after_minutes": 5,
  "hosts": [{"user_id": "<uuid>", "schedule_id": "<uuid>"}],
  "booking_limits": [{"limit_type": "count", "period": "day", "value": 4}]
}
```

### PUT /api/v1/event-types/{id}

Replace an event type (hosts and booking limits are cascade-deleted then
re-inserted). Request body is the same as POST.

```
200  — updated EventTypeResponse
404  — unknown id
```

### DELETE /api/v1/event-types/{id}

Delete an event type (cascades to hosts and booking limits).

```
204  — deleted
404  — unknown id
```

## Slots Endpoint

### GET /api/v1/slots

Return available slots for an event type within a UTC time window, grouped by
local calendar date in the requested time zone.

**Auth:** `Authorization: Bearer <SCHEDULING_API_KEY>` (same key as other `/api/v1/*`).

**Query parameters:**

| Parameter | Type | Required | Description |
|-----------|------|----------|-------------|
| `event_type_id` | UUID | yes | ID of the event type to query |
| `start` | ISO-8601 datetime | yes | Window start (UTC; naive input treated as UTC) |
| `end` | ISO-8601 datetime | yes | Window end (UTC; naive input treated as UTC) |
| `time_zone` | IANA string | yes | Caller's local time zone for date grouping (e.g. `Europe/Moscow`) |

**Validation (422 on failure):**
- `time_zone` must be a valid IANA zone.
- `end` must be after `start` (`end > start`).
- Window must not exceed 62 days.

**Response `200`:**
```json
{
  "event_type_id": "<uuid>",
  "time_zone": "Europe/Moscow",
  "slots": {
    "2026-07-10": [
      "2026-07-10T07:00:00Z",
      "2026-07-10T07:30:00Z"
    ],
    "2026-07-11": [
      "2026-07-11T07:00:00Z"
    ]
  }
}
```

- Keys in `slots` are local calendar dates in the requested `time_zone`
  (ISO-8601 `YYYY-MM-DD`).
- Values are lists of UTC slot start times in `YYYY-MM-DDThh:mm:ssZ` format.
- An empty `slots` dict (`{}`) means no availability in the window.
- Slot times are UTC offsets from local weekly/override/travel-schedule
  intervals, clipped to the requested window, then filtered by
  `min_booking_notice_minutes` (relative to server clock at request time).

**Response `404`:** `{"detail": "event_type <uuid> not found"}` — unknown event type.

**Pipeline summary:**

1. Batch-load `event_type` + `host` rows + each host's `schedule` + `weekly_hours` + `date_overrides` + `travel_schedule` (single DB round-trip per table via `ANY(:ids)`).
2. Per host: compute UTC availability intervals over `[start, end]` applying weekly hours, date overrides, and travel-tz DST conversion (`slots/domain.py`, `slots/timezones.py`).
3. Subtract busy intervals from `BusyTimesSource.get_busy(user_ids, window)` — **slice 3: bound to `BookingBusyTimesSource`**, which queries confirmed `booking` rows for the given hosts overlapping the window, each expanded by the owning event type's `buffer_before_minutes`/`buffer_after_minutes` in SQL.
4. Union free intervals across all hosts (`merge_intervals`); slice into slots of `duration_minutes` at `slot_interval_minutes` step.
5. Apply `min_booking_notice_minutes` gate (`not_before = clock.now() + notice`).
6. Group slot UTC datetimes by local date in `time_zone` (`group_slots_by_local_date`).

**Maturity notes (slice 3):**
- `BusyTimesSource` is **`BookingBusyTimesSource` (real)** — a slot overlapping a confirmed booking, or that booking's `buffer_before`/`buffer_after`, is excluded from the response. Booking a slot via `POST /api/v1/bookings` makes it disappear from subsequent `GET /api/v1/slots` calls; cancelling frees it again.
- `booking_limit` rows are enforced on `POST /api/v1/bookings` (see below) but are **not** subtracted from `GET /api/v1/slots` — a slot can still be *offered* by the read endpoint even if creating a booking there would hit a limit; the limit is only checked at create time.
- No external calendar integration (Google/Office busy times deferred to slice 5).
- No slot caching.
- No reservations/holds — a slot returned by `GET /api/v1/slots` is not reserved; `POST /api/v1/bookings` re-validates availability at write time and can still 409 if it was taken concurrently.

## Booking Endpoints (slice 3)

All under `/api/v1/bookings`, gated by the same Bearer key. Mutating endpoints
(`POST`, `POST .../cancel`, `POST .../reschedule`) accept the same optional
`actor-source` (default `"api"`) / `actor-user-id` headers as the schedule
endpoints, recorded on each `booking_change_log` row. All timestamps in
responses serialize as `YYYY-MM-DDThh:mm:ssZ` (UTC).

### POST /api/v1/bookings

Create a booking. The server re-validates availability, assigns a host by
round-robin, enforces booking limits, and inserts optimistically.

Request:
```json
{
  "event_type_id": "<uuid>",
  "client_user_id": "<uuid>",
  "start_time": "2026-10-01T09:00:00Z",
  "attendee_time_zone": "Europe/Berlin",
  "field_answers": [
    {"key": "reason", "value": "help please"},
    {"key": "topics", "value": ["anxiety", "sleep"]}
  ]
}
```

`field_answers` is optional (default `[]`); each answer's `value` is a string
(`text`/`textarea`/`select`/`radio`), a list of strings (`checkbox`), or a boolean
(`boolean`), per the event type's configured booking fields (see
`GET/PUT /api/v1/event-types/{id}/booking-fields`).

Flow:
1. Validate `attendee_time_zone` (IANA) and `start_time` (must not be in the past).
2. Load the event type + its hosts + their schedules (same bundle the slot engine uses).
   Validate `field_answers` against the type's `booking_field`s (required present & non-empty;
   `select`/`radio` value ∈ options; `checkbox` ⊆ options; `boolean` is a bool; unknown key
   rejected) **before** host assignment — `422` on any violation, with no booking created.
   The validated answers are stored as a snapshot (`[{key, label, type, value}]`) on
   `booking.field_answers` and echoed on the response + the `booking.created` event payload.
3. For each host, recompute availability over `[start, start+duration]` — weekly hours/overrides/travel via `slots/domain.py`, minus busy intervals from `BookingBusyTimesSource` (buffer-expanded) — and keep only hosts free for this exact window. `409` if none are free.
4. Rank the free hosts with `rank_hosts` (getLuckyUser): fewest future confirmed bookings first, then never-assigned before assigned, then oldest `last_assigned_at` first.
5. Enforce `booking_limit`s (see below) for the top-ranked host; `409` if exceeded.
6. Attempt `INSERT` for the top-ranked host inside a SAVEPOINT. If the DB exclusion constraint rejects it (`ConflictError` — the slot was taken concurrently since step 3), retry the next ranked host. `409` if every ranked host fails.
7. Append a `created` row to `booking_change_log`.

Response `201` — `BookingResponse` (see shape below).

Errors: `404` unknown `event_type_id`; `422` `start_time` in the past / invalid `attendee_time_zone` / invalid or missing-required `field_answers`; `409` no host available for the slot / booking_limit exceeded / slot taken concurrently by all ranked hosts.

### GET /api/v1/bookings/{id}

```
200  — BookingResponse
404  — unknown booking
```

`BookingResponse` shape:
```json
{
  "id": "<uuid>",
  "event_type_id": "<uuid>",
  "host_user_id": "<uuid>",
  "client_user_id": "<uuid>",
  "start_time": "2026-10-01T09:00:00Z",
  "end_time": "2026-10-01T10:00:00Z",
  "status": "confirmed",
  "attendee_time_zone": "Europe/Berlin",
  "created_at": "2026-09-15T12:00:00Z",
  "field_answers": [
    {"key": "reason", "label": "Почему нужна помощь", "type": "textarea", "value": "help please"}
  ]
}
```

### GET /api/v1/bookings/{id}/detail

Participant-enriched view of a booking, consumed by **event-booking** (slice 4a.2)
to provision the chat channel, Jitsi meeting URLs, and notifications for
`event-scheduling` bookings — the same side effects it already performs for
cal.com bookings. event-booking's composite booking adapter falls back to this
endpoint when a `booking_uid` isn't present in the cal.com DB.

The service resolves `host_user_id`/`client_user_id` to email/name/time_zone/locale
via event-users (`POST /api/users/by-ids`, `require_admin`-gated Bearer). The
client's `time_zone` is the booking's own `attendee_time_zone`; the host's
`time_zone`/`name`/`locale` come from event-users. Missing participant fields
degrade gracefully (`email` empty, `name`/`time_zone`/`locale` null) rather than
erroring.

```
200  — BookingDetailResponse
404  — unknown booking
```

`BookingDetailResponse` shape:
```json
{
  "uid": "<booking uuid, as string>",
  "title": "30-Minute Introduction",
  "start_time": "2026-10-01T09:00:00Z",
  "end_time": "2026-10-01T10:00:00Z",
  "status": "confirmed",
  "host": {"email": "host@example.com", "name": "Host Name", "time_zone": "Europe/Moscow", "locale": "ru"},
  "client": {"email": "client@example.com", "name": "Client Name", "time_zone": "Europe/Berlin", "locale": "en"}
}
```

- `uid` is the booking UUID rendered as a string (the same value event-scheduling
  publishes as `booking_uid` on its `booking.lifecycle` CloudEvents), so
  event-booking can key on it directly.
- `title` is the event type's `title` (empty string if the event type is gone).
- `host.name`/`host.time_zone`/`host.locale` and `client.name`/`client.locale`
  may be `null` when the participant isn't resolvable via event-users.

### GET /api/v1/bookings

List bookings by host or client. Exactly one of `host_user_id` / `client_user_id`
is required.

Query params: `host_user_id` (UUID, exclusive with `client_user_id`), `client_user_id`
(UUID, exclusive with `host_user_id`), `from_` (ISO-8601 datetime, optional),
`to` (ISO-8601 datetime, optional).

```
200  — {"bookings": [<BookingResponse>, ...]}, ordered by start_time ASC
422  — neither or both of host_user_id/client_user_id supplied
```

### POST /api/v1/bookings/{id}/cancel

Soft-cancel (`status: 'confirmed' → 'cancelled'`). Idempotent: cancelling an
already-cancelled booking returns it unchanged with no second `booking_change_log`
row. The slot is immediately free again (the exclusion constraint only applies
`WHERE status='confirmed'`).

```
200  — BookingResponse (status: "cancelled")
404  — unknown booking
```

### POST /api/v1/bookings/{id}/reschedule

Move a booking to a new `start_time`, **in place, same host only** — this is not
a cancel+recreate; `event_type_id`/`host_user_id`/`client_user_id` are unchanged.
Duration comes from the event type's current `duration_minutes`.

Request:
```json
{"start_time": "2026-10-01T11:00:00Z"}
```

Re-checks the assigned host's availability at the new time, excluding the
booking's own row from the busy-time query (`exclude_booking_id`) so the
booking doesn't conflict with itself.

```
200  — BookingResponse (updated start_time/end_time)
404  — unknown booking
409  — booking is cancelled, host is not available at the new time, or the assigned host is no longer on this event type
422  — start_time in the past
```

Appends a `rescheduled` row to `booking_change_log` (`from_start`/`from_end` →
`to_start`/`to_end`).

### GET /api/v1/bookings/{id}/history

Full transition history for a booking, ordered `at ASC`.

```
200  — {"entries": [<ChangeEntry>, ...]}
```

`ChangeEntry` shape:
```json
{
  "kind": "rescheduled",
  "from_start": "2026-10-01T09:00:00Z",
  "from_end": "2026-10-01T10:00:00Z",
  "to_start": "2026-10-01T11:00:00Z",
  "to_end": "2026-10-01T12:00:00Z",
  "actor_source": "admin",
  "actor_user_id": null,
  "at": "2026-09-20T08:00:00Z"
}
```
`kind` is one of `created` / `rescheduled` / `cancelled`; `from_*` is null for
`created`, `to_*` is null for `cancelled`.

### Booking limits (enforced on create, not a separate endpoint)

`booking_limit` rows (created via `event-types` `hosts`/`booking_limits`, see
above) are evaluated in `POST /api/v1/bookings` against the **assigned host's**
period, computed in that host's schedule time zone and converted to UTC
(`day`/`week`(ISO Monday)/`month`/`year`):

| `limit_type` | Check |
|--------------|-------|
| `booking_count` | reject if the host already has `>= value` confirmed bookings for this event type in the period |
| `booking_duration` | reject if existing confirmed minutes + this booking's duration `> value` minutes for this event type in the period |

Any other `limit_type` value is a documented no-op (`limit_exceeded` returns
`False` for unrecognized types rather than raising).

## Ops Endpoints (unauthenticated)

- `GET /health` — liveness; `200 {"status":"ok"}`, no dependency calls.
- `GET /ready` — readiness; pings PostgreSQL.
  `200 {"status":"ready"}` or `503 {"status":"not_ready"}`.
- `GET /metrics` — Prometheus text exposition.

## Error Responses

All domain errors return JSON `{"detail": "<message>"}`:

| HTTP status | Domain exception | Trigger |
|-------------|-----------------|---------|
| `422` | `ValidationError` | Invalid time zone, overlapping weekly intervals, invalid booking limit, booking `start_time` in the past, missing/duplicate `host_user_id`/`client_user_id` on booking list |
| `404` | `NotFoundError` | Schedule, event type, or booking not found |
| `409` | `ConflictError` | `slug` already in use for event types; booking: no host available for the slot, `booking_limit` exceeded, slot taken concurrently, reschedule of a cancelled booking, host not available at the new reschedule time, or the assigned host is no longer on the event type at reschedule time |
