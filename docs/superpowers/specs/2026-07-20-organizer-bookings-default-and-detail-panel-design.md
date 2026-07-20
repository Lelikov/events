# Organizer cabinet: Bookings default + right-side detail panel

**Date:** 2026-07-20
**Status:** design locked with the conservative options (user stepped away mid-brainstorm; see Decision Log)

## Problem

Three asks for the organizer cabinet (`event-organizer-frontend`):

1. The default page should be **Bookings**, not Schedule.
2. **Bookings** should be the top menu item.
3. Clicking a booking should reveal **all its information** in a panel on the right.

Today `/` → Schedule; the menu is Расписание / Брони / Профиль; the Bookings page
is a flat upcoming/past list of `{time, status}` with no per-booking detail, and
the BFF list endpoint discards everything except `{id, start_time, end_time, status}`.

## Scope

- `event-organizer-frontend` — routing default, menu order, Bookings master-detail
  UI + detail panel + api/types.
- `event-organizer` (BFF) — one new owner-scoped endpoint `GET /api/me/bookings/{id}`
  and a `get_booking_detail` scheduling-client method.

Out of scope: `event-scheduling` (reuses its existing `GET /api/v1/bookings/{id}/detail`);
booking mutation (cancel/reschedule) from the cabinet; the Schedule/Profile pages.

## Design

### 1. Default route + menu order (frontend)

- `parseRoute('/')` → `{ name: 'bookings' }` (was `schedule`). `'/schedule'` still →
  `schedule`; `'/bookings'` → `bookings`. `App` renders `BookingsPage` for `bookings`.
- `NAV_ITEMS` reordered to **Брони, Расписание, Профиль**. Брони's `path` is `'/'`
  and it matches `'/'` or `'/bookings'`; Расписание's `path` is `'/schedule'`.
- App's post-login redirect already targets `'/'` → now lands on Bookings.

### 2. BFF booking-detail endpoint (owner-scoped)

`event-scheduling` already exposes `GET /api/v1/bookings/{id}/detail` →
`{uid, title, start_time, end_time, status, host{email,name,time_zone,locale},
client{email,name,time_zone,locale}}` (names resolved via event-users). Its list
(`GET /api/v1/bookings?host_user_id=`) additionally carries `attendee_time_zone`,
`created_at`, and `field_answers` (`[{key,label,type,value}]`).

New scheduling-client method (`event_organizer/adapters/scheduling_client.py`):

```python
async def get_booking_detail(self, booking_id: UUID) -> dict:
    async with self._http() as c:
        resp = await c.get(f"{self._base_url}/api/v1/bookings/{booking_id}/detail")
    return self._ok(resp)   # 404→NotFoundError, 422→ValidationError, else→UpstreamError
```
(added to the `ISchedulingClient` Protocol as well.)

New BFF route `GET /api/me/bookings/{booking_id}` (`routers/me.py`), owner-scoped by
construction — the caller supplies only a booking id, never an owner:

```
rows = await scheduling.get_bookings(me.user_id)
row = first row whose id == str(booking_id), else raise NotFoundError  # ownership gate
detail = await scheduling.get_booking_detail(booking_id)
return BookingDetailItem(
    id=detail["uid"], title=detail["title"],
    start_time=detail["start_time"], end_time=detail["end_time"], status=detail["status"],
    client_name=detail["client"]["name"], client_email=detail["client"]["email"],
    client_time_zone=row.get("attendee_time_zone"),
    created_at=row.get("created_at"),
    field_answers=[{"label": a["label"], "value": _stringify(a["value"])} for a in row.get("field_answers", [])],
)
```

The ownership gate reuses the same `get_bookings(me.user_id)` the list already calls,
so a booking id that is not one of the organizer's own returns 404 — no cross-organizer
read. `_stringify(value: str | list[str] | bool)`: `bool → "Да"/"Нет"`,
`list → ", ".join`, else `str(value)`.

Response schema (`schemas/me.py`):

```python
class BookingFieldAnswer(BaseModel):
    label: str
    value: str

class BookingDetailItem(BaseModel):
    id: str
    title: str
    start_time: str
    end_time: str
    status: str
    client_name: str | None
    client_email: str | None
    client_time_zone: str | None
    created_at: str | None
    field_answers: list[BookingFieldAnswer]
```

### 3. Frontend master-detail Bookings page

Two-column layout (`.bookings-layout`): the upcoming/past list on the left, a detail
panel on the right. Selecting a row (`selectedId` state) loads its detail; nothing
selected → a placeholder «Выберите бронь».

- `bookingsApi.ts`: add `getBookingDetail(id: string): Promise<BookingDetail>` →
  `GET /api/me/bookings/${id}`.
- `types.ts`: add `BookingDetail` (mirrors `BookingDetailItem`) and
  `BookingFieldAnswer`.
- `BookingsPage.tsx`: rows become buttons; the selected row gets `is-selected`;
  the right column renders `<BookingDetailPanel bookingId={selectedId} organizerTz={timeZone} />`.
- New `BookingDetailPanel.tsx`: on `bookingId` change, fetch detail (loading /
  error / empty states); render title (heading) + status badge + date/time
  (`formatRange(start,end, organizerTz)`) + Клиент (name, email) + Часовой пояс
  клиента (`client_time_zone`) + Создана (`created_at`) + Ответы формы
  (`field_answers`, only if non-empty). Reuses the `.badge`/`statusVariant`
  helper already in the bookings module.

CSS (`index.css`): `.bookings-layout` grid (`minmax(240px, 320px) 1fr`, gap),
`.booking-row.is-selected` accent, `.detail-panel` card, `.detail-field` rows,
`.detail-empty` placeholder. On narrow widths the grid collapses to one column.

## Data flow

Load: `getBookings()` (list) + `getProfile()` (organizer tz) as today → left list.
Click: `setSelectedId(id)` → `BookingDetailPanel` effect calls `getBookingDetail(id)`
→ BFF verifies ownership + merges scheduling detail with the list row → panel renders.

## Error handling

- `getBookingDetail` failure → the panel shows «Не удалось загрузить бронь» (the list
  stays intact). A 404 (unknown/for-another-organizer id) surfaces the same way.
- The BFF's existing `ApiError`/`_ok` mapping is reused; no new error types.

## Testing

- **BFF** (`tests/test_me_api.py`): extend `_FakeScheduling` with `get_booking_detail`;
  `GET /api/me/bookings/b1` (id present in the fake list) → 200 with `title`,
  `client_name`, merged `field_answers`; `GET /api/me/bookings/unknown` → 404
  (ownership gate). **scheduling_client** (`tests/test_scheduling_client.py`):
  `get_booking_detail` issues the right GET and returns the body; a 404 → `NotFoundError`.
  `_stringify` unit cases (bool/list/str).
- **Frontend**: `routing.test.ts` — `parseRoute('/')` → `bookings`.
  `OrganizerLayout.test.tsx` — nav order is Брони/Расписание/Профиль.
  `BookingDetailPanel.test.tsx` — placeholder when no id; renders the fetched
  fields; error state on rejection (api mocked). `BookingsPage.test.tsx` — clicking
  a row selects it and renders the panel with the detail; list still shows
  upcoming/past.

## Decision Log

- **Layout = two-column master-detail** (not a slide-in drawer): matches «справа
  появляется», no overlay, and the booking list is short so a narrow left column is
  fine. Chosen while the user was away; low-regret (a drawer is a later restyle if
  wanted).
- **Full info** — event type title, client name/email, time, client tz, created,
  and guest field answers — merged from `/detail` (title + client names) and the
  list row (answers/tz/created). Everything reachable is shown; empty
  `field_answers` simply renders nothing.
- **Ownership by construction** — the detail endpoint takes only a booking id and
  gates it against the organizer's own `get_bookings(me.user_id)`, so it cannot read
  another organizer's booking. Consistent with the rest of the `/api/me/*` BFF.
- **Brони path `'/'`** — the default page, so its nav item points at `'/'` and
  matches `'/'`/`'/bookings'`; Расписание moves to `'/schedule'`.
