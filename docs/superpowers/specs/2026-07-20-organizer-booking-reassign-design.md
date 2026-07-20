# Organizer cabinet: reassign a booking to another host

**Date:** 2026-07-20
**Status:** design approved (user: "сделай продюсер + BFF + кнопку, переназначать на другого хоста").

## Problem

Add a **«Переназначить»** action in the organizer cabinet: hand a booking to another
host of the same event type. The downstream for the `booking.reassigned`
(organizer-reassignment) contract is **already built** — this feature adds the only
missing piece: the **producer** in `event-scheduling`, plus a BFF proxy and a UI.

## What already exists (do NOT rebuild)

- **event-schemas**: `EventType.BOOKING_REASSIGNED = "booking.reassigned"` (CRITICAL,
  v1), `BookingReassignedPayload{users: [BookingParticipant], previous_organizer_email}`
  with participant roles `organizer` (new), `client`, `previous_organizer` (old);
  routing `RoutingRuleSpec(BOOKING_LIFECYCLE, source="booking", "booking.reassigned")`;
  `TriggerEvent.BOOKING_REASSIGNED`.
- **event-receiver**: normalizer `_participants_from_users_list` for BOOKING_REASSIGNED.
- **event-saver**: `LifecycleProjection` maps `BOOKING_REASSIGNED → "reassigned"` and
  extracts `previous_organizer` (user_id of the `previous_organizer`-role participant).
- **event-booking**: `handle_reassigned` — hard-deletes + recreates the chat, regenerates
  meeting URLs in place, notifies participants with `TriggerEvent.BOOKING_REASSIGNED`
  + `previous_organizer_email`.

Separately, `booking.client_reassigned` (change the **client**) is a fully-built
event-admin feature — **out of scope**.

## Scope

- **event-scheduling** (producer): a reassign domain write + `booking.reassigned` outbox
  emit + router endpoint.
- **event-organizer** (BFF): list reassign targets + reassign proxy.
- **event-organizer-frontend**: a «Переназначить» button + host-picker modal.

## Design — event-scheduling (producer)

### Availability check without notice

`BookingService._free_host` gains `check_notice: bool = True`; the `start < now + notice`
guard runs only when true. `create`/`reschedule` keep `check_notice=True` (default);
`reassign` passes `check_notice=False` — the booking's time is fixed, so a last-minute
hand-off must not be blocked by the create-time notice window.

### Write adapter

`BookingWriteAdapter.update_host(booking_id, new_host_user_id) -> BookingDTO`, mirroring
`update_times`' SAVEPOINT pattern:

```python
async def update_host(self, booking_id: UUID, new_host_user_id: UUID) -> BookingDTO:
    try:
        async with self._sql.begin_nested():
            row = await self._sql.fetch_one(
                f"UPDATE booking SET host_user_id=:h, updated_at=now() WHERE id=:id RETURNING {_COLS}",
                {"id": booking_id, "h": new_host_user_id},
            )
    except IntegrityError as e:
        raise ConflictError("host already has a booking at this time") from e
    return _row_to_dto(row)
```
`reminder_sent_at` is **not** reset: a not-yet-sent reminder resolves the host at send
time and naturally goes to the new host; the reassignment itself notifies both parties.

### Service

```python
async def reassign(self, booking_id: UUID, new_host_user_id: UUID, actor: ActorDTO) -> BookingDTO:
    booking = await self.get(booking_id)
    if booking.status == "cancelled":
        raise ConflictError("cannot reassign a cancelled booking")
    if new_host_user_id == booking.host_user_id:
        raise ValidationError("new host is the same as the current host")
    bundle = await self._slots.load(booking.event_type_id)
    if bundle is None:
        raise NotFoundError(f"event_type {booking.event_type_id} not found")
    new_host = next((h for h in bundle.hosts if h.user_id == new_host_user_id), None)
    if new_host is None:
        raise ValidationError("new host is not a host of this event type")
    now = self._clock.now()
    if not await self._free_host(new_host, booking.start_time, booking.end_time, 0, now, booking_id, check_notice=False):
        raise ConflictError("new host is not available at this time")
    previous_host = booking.host_user_id
    updated = await self._write.update_host(booking_id, new_host_user_id)
    await self._write.append_log(booking_id, "reassigned", booking.start_time, booking.end_time,
                                 booking.start_time, booking.end_time, actor)
    await self._outbox.write("booking.reassigned", updated, previous_host_user_id=previous_host)
    return updated
```

### Outbox emit (the `booking.reassigned` payload)

- `OutboxWriter.write` gains `previous_host_user_id: UUID | None = None`; when set, the
  stored payload includes `"previous_host_user_id": str(previous_host_user_id)`.
- `publishing/dispatcher.py::_resolve_participants` resolves the previous host too when
  the payload carries `previous_host_user_id` (add its id to the `by_ids` batch); returns
  `(host, client, previous_host_or_None)`. `_dispatch_row` passes `previous_host` into
  `build_cloudevent`.
- `publishing/payload.py`:
  - `_users(host, client, attendee_tz, previous_host=None)` appends
    `{"email": previous_host.email, "role": "previous_organizer", "time_zone": previous_host.time_zone}`
    when `previous_host` is set.
  - `build_cloudevent(..., previous_host: ParticipantInfo | None = None)` passes it to `_users`.
  - `_reassigned_body(booking_uid, payload, users)` → `{"users": users, "booking_uid": booking_uid,
    "previous_organizer_email": <email of the previous_organizer-role user, or None>}`.
  - Register `"booking.reassigned": _reassigned_body` in `_BUILDERS`.

The resulting CloudEvent (ce-source `booking`, ce-type `booking.reassigned`) is POSTed to
event-receiver `/event/booking` by the existing dispatcher, then flows through the
already-built normalize → project → react chain.

### Router

`POST /api/v1/bookings/{booking_id}/reassign` body `{new_host_user_id: UUID}`, headers
`actor-source`/`actor-user-id` → `service.reassign(...)` → `BookingResponse`.
Schema `ReassignRequest{new_host_user_id: UUID}`. Errors: `409` (cancelled / new host
busy), `422` (same host / not a host of the event type), `404`.

## Design — event-organizer (BFF)

Scheduling-client methods (`ISchedulingClient` + `SchedulingClient`):
- `get_event_type(event_type_id: str) -> dict` → `GET /api/v1/event-types/{id}` (for its hosts).
- `reassign_booking(booking_id: str, new_host_user_id: str, actor_user_id: UUID) -> dict`
  → `POST /api/v1/bookings/{id}/reassign` `{new_host_user_id}` with actor headers (via `_ok`,
  so 409→ConflictError / 422→ValidationError already forward).

Routes (`routers/me.py`), both owner-scoped via `_owned_row`:
- `GET /api/me/bookings/{id}/reassign-targets` → owned row (has `host_user_id` +
  `event_type_id`) → `get_event_type(event_type_id)` → hosts' `user_id`s, minus the
  current host → for each, `users.get_user(uid)` → `ReassignTarget{user_id, name, email}`.
  Returns `list[ReassignTarget]` (empty if no other hosts).
- `POST /api/me/bookings/{id}/reassign` body `{new_host_user_id: str}` → owned row →
  `scheduling.reassign_booking(id, new_host_user_id, me.user_id)` → updated booking dict.

Schemas: `ReassignTarget{user_id: str, name: str | None, email: str}`,
`ReassignRequest{new_host_user_id: str}`.

## Design — event-organizer-frontend

- `bookingsApi.ts`: `getReassignTargets(id) -> ReassignTarget[]`,
  `reassignBooking(id, newHostUserId) -> void`.
- `types.ts`: `ReassignTarget{user_id, name, email}`.
- `BookingDetailPanel`: a **«Переназначить»** button next to «Перенести» (same
  confirmed-future guard); opens `ReassignModal`; reuse the `onRescheduled` refresh (rename
  the callback to `onChanged` — one refresh callback covers reschedule + reassign).
- New `ReassignModal.tsx` (reuses the DS modal): on open, `getReassignTargets(id)` → render
  each target as a selectable row (`name` — `email`). States: loading, empty («Нет других
  хостов для этого типа встречи»), error. Footer «Отмена» + «Переназначить» (disabled until
  a target is picked). Confirm → `reassignBooking(id, picked)` → on success close +
  `onChanged()`; on ApiError show the message, stay open.
- CSS: reuse `.slot-chip`/list styles (add `.target-row` if needed).

## Data flow

Open modal → BFF resolves the owned booking's event type → its other hosts (names via
event-users) → picker. Confirm → BFF proxies `/reassign` with the organizer as actor →
event-scheduling validates (host of event type, new host free), updates `host_user_id`,
logs `reassigned`, emits `booking.reassigned` (users: new organizer + client +
previous_organizer, `previous_organizer_email`) → event-receiver normalize → event-saver
project (`reassigned`) + event-booking react (recreate chat/meeting, notify). BFF 200 →
SPA refreshes list + detail. The booking leaves this organizer's list (host is now someone
else) once reloaded.

## Error handling

- `409` (cancelled / new host busy / DB exclusion) → ConflictError → modal shows the reason.
- `422` (same host / not a host of the event type) → ValidationError → same.
- Empty targets → «Нет других хостов…» (not an error).
- Outbox is at-least-once with a stable ce-id per row; downstream dedup handles re-delivery.

## Testing

- **event-scheduling**: `_free_host` honours `check_notice=False` (a soon-starting slot
  still passes). `BookingService.reassign` — happy path (updates host, logs `reassigned`,
  writes an outbox row `booking.reassigned` with `previous_host_user_id`); rejects a
  cancelled booking (409), a same-host (422), a non-host target (422), a busy new host
  (409). `update_host` maps the exclusion IntegrityError → ConflictError.
  `payload.build_cloudevent("booking.reassigned", …, previous_host=…)` → body has three
  `users` (organizer/client/previous_organizer) + `previous_organizer_email`; dispatcher
  resolves the previous host. Router `POST /{id}/reassign` returns the updated booking and
  forwards actor headers.
- **BFF**: `get_event_type`/`reassign_booking` client methods (path, body, actor headers,
  409/422 forwarding). Routes: `reassign-targets` for an owned booking lists the event
  type's other hosts with names, `404` for an unknown id; `reassign` forwards
  `new_host_user_id` + actor, `404` for an unknown id.
- **Frontend**: `ReassignModal` — loads targets, enables «Переназначить» on a pick, confirm
  calls `reassignBooking` + `onChanged`, error stays open; `BookingDetailPanel` shows
  «Переназначить» only for a confirmed future booking.

## Decision Log

- **Reassign = organizer change via the existing `booking.reassigned`** — the whole
  downstream (normalize/project/react) is built; only the producer + BFF + UI are new.
- **Target = another host of the same event type** (user-confirmed) — candidates come from
  the event type's `host` rows, minus the current host.
- **No notice check on reassign** — the time is fixed; a last-minute hand-off must be allowed.
- **Don't reset `reminder_sent_at`** — a pending reminder resolves the host at send time and
  reaches the new host; the reassignment notification already alerts both parties.
- **Same refresh path as reschedule** — one `onChanged` callback bumps the list/detail refresh.
