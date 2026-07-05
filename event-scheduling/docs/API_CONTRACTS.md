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
- Validation errors: `422` if `time_zone` invalid or `weekly_hours` intervals overlap
  on the same day.

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
3. Subtract busy intervals from `BusyTimesSource.get_busy(user_ids, window)` — currently `StubBusyTimesSource` which returns `[]`.
4. Union free intervals across all hosts (`merge_intervals`); slice into slots of `duration_minutes` at `slot_interval_minutes` step.
5. Apply `min_booking_notice_minutes` gate (`not_before = clock.now() + notice`).
6. Group slot UTC datetimes by local date in `time_zone` (`group_slots_by_local_date`).

**Maturity notes (slice 2):**
- `BusyTimesSource` is `StubBusyTimesSource` — slots are never blocked by existing bookings. Slice 3 will replace the stub with real booking data.
- `buffer_before_minutes` / `buffer_after_minutes` on the event type are loaded but not yet applied to interval subtraction (inert until slice 3).
- `booking_limit` rows are stored but not enforced during slot calculation (inert until slice 3).
- No external calendar integration (Google/Office busy times deferred to slice 5).
- No slot caching.

## Ops Endpoints (unauthenticated)

- `GET /health` — liveness; `200 {"status":"ok"}`, no dependency calls.
- `GET /ready` — readiness; pings PostgreSQL.
  `200 {"status":"ready"}` or `503 {"status":"not_ready"}`.
- `GET /metrics` — Prometheus text exposition.

## Error Responses

All domain errors return JSON `{"detail": "<message>"}`:

| HTTP status | Domain exception | Trigger |
|-------------|-----------------|---------|
| `422` | `ValidationError` | Invalid time zone, overlapping weekly intervals, invalid booking limit |
| `404` | `NotFoundError` | Schedule or event type not found |
| `409` | `ConflictError` | `slug` already in use for event types |
