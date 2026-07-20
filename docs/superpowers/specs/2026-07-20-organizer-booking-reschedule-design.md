# Organizer cabinet: reschedule a booking (defer reassign)

**Date:** 2026-07-20
**Status:** design locked for **reschedule**; **reassign deferred** (see Reassign section). User stepped away mid-brainstorm; conservative options chosen.

## Problem

The user asked for two per-booking actions in the organizer cabinet detail panel:
**Перенести** (reschedule) and **Переназначить** (reassign).

API study result:

- **Перенести** is fully supported: `POST /api/v1/bookings/{id}/reschedule {start_time}`
  → `BookingService.reschedule` — moves the booking to a new time, **same host**,
  re-validates that host's availability (schedule + busy + buffers + notice),
  writes a `rescheduled` change-log row, and publishes `booking.rescheduled` so
  event-booking updates the meeting/reminder. Errors: `409` (cancelled / new slot
  not free / host no longer on event type), `422` (past time), `404`.
- **Переназначить** is **not supported anywhere** — no endpoint, no service method,
  no write-adapter host change (`insert`/`update_times`/`set_cancelled`/`append_log`
  only), no schema field, no `reassign` in the codebase. Implementing it is a new
  cross-service capability **and** its meaning is ambiguous (reassign host vs.
  client). It is **deferred** — see the Reassign section.

## Scope (this spec = reschedule only)

- `event-organizer` (BFF): two owner-scoped endpoints — available slots for a
  booking on a date, and the reschedule proxy — plus scheduling-client methods and
  409 error forwarding.
- `event-organizer-frontend`: a «Перенести» action in the booking detail panel, a
  reschedule modal (pick a new available slot), and a list/detail refresh on success.
- `event-scheduling`: **unchanged** — reuses `reschedule` + `slots`.

## Design — reschedule

### BFF: scheduling-client methods

`event_organizer/adapters/scheduling_client.py` (+ `ISchedulingClient`):

```python
async def get_slots(self, event_type_id: str, start_iso: str, end_iso: str, time_zone: str) -> dict:
    async with self._http() as c:
        resp = await c.get(f"{self._base_url}/api/v1/slots",
                           params={"event_type_id": event_type_id, "start": start_iso, "end": end_iso, "time_zone": time_zone})
    return self._ok(resp)

async def reschedule_booking(self, booking_id: str, start_time_iso: str, actor_user_id: UUID) -> dict:
    headers = {"actor-source": "organizer", "actor-user-id": str(actor_user_id)}
    async with self._http() as c:
        resp = await c.post(f"{self._base_url}/api/v1/bookings/{booking_id}/reschedule",
                            json={"start_time": start_time_iso}, headers=headers)
    return self._ok(resp)
```

Extend `_ok`: add `409 → ConflictError(self._detail(resp))` (before the generic
`UpstreamError`), so "slot not free" surfaces as a 409 with its message rather than
a 502 (mirrors the existing 422→ValidationError forwarding).

### BFF: routes (`routers/me.py`)

Both gate the booking id against the caller's own `get_bookings(me.user_id)`
(ownership by construction — 404 if not the organizer's).

- `GET /api/me/bookings/{booking_id}/slots?date=YYYY-MM-DD&time_zone=<IANA>` →
  find the owned booking row → its `event_type_id` → compute the UTC window for the
  local `date` in `time_zone` (`zoneinfo`: `[date 00:00, date+1 00:00)`) → call
  `scheduling.get_slots(...)` → return `BookingSlotsResponse{date, time_zone,
  slots: list[str]}` (the day's UTC-ISO slot starts, `slots.get(date, [])`).
- `POST /api/me/bookings/{booking_id}/reschedule` body `{start_time: str}` →
  ownership gate → `scheduling.reschedule_booking(booking_id, body.start_time,
  me.user_id)` → return the updated booking dict (200). `time_zone` for the window
  is the organizer's — passed by the client (it already has it from the profile),
  which is display-only, not an owner id.

Schemas (`schemas/me.py`): `BookingSlotsResponse{date: str, time_zone: str,
slots: list[str]}`, `RescheduleRequest{start_time: str}`.

### Frontend

`bookingsApi.ts`:
```ts
getBookingSlots(id, date, timeZone): Promise<{ date: string; time_zone: string; slots: string[] }>
rescheduleBooking(id, startTime): Promise<void>   // POST, returns void
```

`BookingDetailPanel`:
- Show a **«Перенести»** button only when `status === 'confirmed'` and
  `new Date(start_time) > now` (can't reschedule a past or cancelled booking — the
  API would 409/422 anyway). Pass an `onRescheduled` callback up.
- Clicking opens `RescheduleModal`.

New `RescheduleModal.tsx` (reuses the design-system `.modal-overlay`/`.modal-content`
from the leave modal):
- A date `<input type="date">` (default: the booking's current local date).
- On date change, `getBookingSlots(id, date, organizerTz)` → render the returned
  slots as selectable chips, each labelled with `formatDateTime(slotIso, organizerTz)`
  (time-only is enough). States: loading, empty («Нет свободных слотов на эту дату»),
  error.
- Footer: «Отмена» (secondary) + «Перенести» (primary, disabled until a slot is
  picked). On confirm → `rescheduleBooking(id, pickedIso)` → on success close +
  `onRescheduled()`; on `ApiError` (409/422) show the message inline and keep the
  modal open.

`BookingsPage`: give `BookingDetailPanel` an `onRescheduled` that (a) re-fetches
`getBookings()` to refresh the list times, and (b) bumps a `refreshKey` passed into
the panel's `key` so it remounts and re-fetches the detail. (Keeping the panel keyed
by `selectedId` + `refreshKey` gives a clean refresh.)

## Data flow

Open modal → pick date → BFF resolves the owned booking's event type, windows the
date in the organizer tz, proxies `/api/v1/slots` → chips. Confirm → BFF proxies
`/api/v1/bookings/{id}/reschedule` with the organizer as actor → event-scheduling
re-validates + moves + publishes `booking.rescheduled` → BFF 200 → SPA refreshes
list + detail.

## Error handling

- `409` (slot taken concurrently / not free / cancelled / host off event type) →
  ConflictError → the modal shows the upstream message, stays open.
- `422` (past time / bad tz) → ValidationError → same inline surfacing.
- Empty slots for a date → «Нет свободных слотов на эту дату» (not an error).
- The slots endpoint does not exclude the booking's own time; that only hides the
  current slot from the options, which is fine when moving to a different time.

## Reassign — deferred (documented, pending decision)

Not built here. To implement «Переназначить» later we must first pick its meaning
(reassign **host** vs **client**), then add to `event-scheduling`: a service method +
a `BookingWriteAdapter` host/client update (guarded by the `EXCLUDE`/no-double-book
constraint and a fresh availability check for the new host), a `reassigned`
change-log row, a new outbox event type + a downstream reaction in event-booking,
and a router endpoint; then a BFF proxy + a target-picker UI. This is a separate
slice with its own spec once the semantics are chosen.

## Testing

- **BFF** (`test_scheduling_client.py`): `get_slots` issues the right GET with all
  four params; `reschedule_booking` POSTs `{start_time}` with `actor-source`/
  `actor-user-id` headers; a `409` from either raises `ConflictError` carrying the
  detail. (`test_me_api.py`): `GET /api/me/bookings/b1/slots?...` returns the day's
  slots for an owned booking, `404` for an unknown id; `POST /api/me/bookings/b1/
  reschedule` forwards `start_time` + returns 200, `404` for an unknown id.
- **Frontend**: `RescheduleModal.test.tsx` — picking a date fetches + renders slot
  chips; selecting a chip enables «Перенести»; confirm calls `rescheduleBooking`
  with the chosen iso and fires `onRescheduled`; a rejected reschedule shows the
  error and keeps the modal open. `BookingDetailPanel.test.tsx` — the «Перенести»
  button shows only for a confirmed future booking, not for a cancelled/past one.

## Decision Log

- **Reschedule now, reassign deferred** — reschedule is API-ready and unambiguous;
  reassign is unsupported + ambiguous + a large cross-service addition. Building it
  on a guess is high-regret. This is the explicitly-offered "reschedule first"
  option. Chosen while the user was away.
- **Slot-picker UX** (not a raw time input) — the slots API exists and the public
  Booker already establishes slot-picking; a guess-and-check time that 409s is poor
  UX and below this project's bar.
- **Actor = organizer** — reschedule passes `actor-source=organizer`,
  `actor-user-id=me.user_id` so the change log attributes the move to the organizer.
- **Ownership by construction** — both endpoints gate the booking id against the
  caller's own `get_bookings(me.user_id)`, consistent with the detail endpoint.
