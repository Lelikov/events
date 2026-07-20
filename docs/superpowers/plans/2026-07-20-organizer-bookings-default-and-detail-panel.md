# Organizer Cabinet: Bookings Default + Detail Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Bookings the default/top cabinet page and add a right-side panel showing a booking's full info on click.

**Architecture:** Frontend routing default flips to Bookings and the menu reorders. A new owner-scoped BFF endpoint `GET /api/me/bookings/{id}` merges event-scheduling's `/detail` (title + client name/email) with the list row (guest field answers, tz, created), gated against the organizer's own bookings. The Bookings page becomes two-column master-detail with a `BookingDetailPanel`.

**Tech Stack:** React 19 + Vite + TS, plain CSS, vitest + happy-dom; Python 3.14 FastAPI, pytest.

## Global Constraints

- No `else if`; avoid `else` — early returns / guard clauses / mappings (both codebases).
- Frontend: plain CSS, Russian copy, no router lib, design-system tokens.
- BFF ownership-by-construction: `/api/me/*` never accepts a caller-supplied owner id; the detail endpoint gates the booking id against `get_bookings(me.user_id)`.
- event-scheduling is NOT modified — reuse `GET /api/v1/bookings/{id}/detail`.
- BFF tests run against Postgres; use `TEST_POSTGRES_DSN=postgresql+asyncpg://postgres:postgres@localhost:5432/event_organizer_test` (create the throwaway DB first).
- Frontend build (`tsc -b`) typechecks test files — keep test types clean.

---

### Task 1: BFF scheduling-client `get_booking_detail`

**Files:**
- Modify: `event-organizer/event_organizer/adapters/interfaces.py`
- Modify: `event-organizer/event_organizer/adapters/scheduling_client.py`
- Test: `event-organizer/tests/test_scheduling_client.py`

**Interfaces:**
- Produces: `SchedulingClient.get_booking_detail(booking_id: UUID) -> dict` and the same method on the `ISchedulingClient` Protocol.

- [ ] **Step 1: Write the failing test**

Append to `tests/test_scheduling_client.py`:

```python
@pytest.mark.asyncio
async def test_get_booking_detail_ok_and_path() -> None:
    bid = uuid4()

    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/api/v1/bookings/{bid}/detail"
        return httpx.Response(200, json={"uid": str(bid), "title": "Консультация", "status": "confirmed"})

    out = await _c(h).get_booking_detail(bid)
    assert out["title"] == "Консультация"


@pytest.mark.asyncio
async def test_get_booking_detail_404_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await _c(lambda _req: httpx.Response(404)).get_booking_detail(uuid4())
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd event-organizer && TEST_POSTGRES_DSN=postgresql+asyncpg://postgres:postgres@localhost:5432/event_organizer_test uv run pytest tests/test_scheduling_client.py -q`
Expected: FAIL — `get_booking_detail` not defined. (Create the DB first: `docker compose exec -T postgres psql -U postgres -c "CREATE DATABASE event_organizer_test;"` — ignore "already exists".)

- [ ] **Step 3: Implement**

In `interfaces.py` add to `ISchedulingClient`:

```python
    async def get_booking_detail(self, booking_id: UUID) -> dict: ...
```

In `scheduling_client.py` add (after `get_bookings`):

```python
    async def get_booking_detail(self, booking_id: UUID) -> dict:
        async with self._http() as c:
            resp = await c.get(f"{self._base_url}/api/v1/bookings/{booking_id}/detail")
        return self._ok(resp)
```

- [ ] **Step 4: Run to verify it passes**

Run: `cd event-organizer && TEST_POSTGRES_DSN=... uv run pytest tests/test_scheduling_client.py -q`
Expected: PASS.

- [ ] **Step 5: Lint + commit**

```bash
cd event-organizer && uv run ruff check event_organizer/adapters/scheduling_client.py event_organizer/adapters/interfaces.py
cd .. && git add event-organizer/event_organizer/adapters/scheduling_client.py event-organizer/event_organizer/adapters/interfaces.py event-organizer/tests/test_scheduling_client.py
git commit -m "feat(organizer): scheduling client get_booking_detail"
```

---

### Task 2: BFF `GET /api/me/bookings/{id}` (owner-scoped, merged)

**Files:**
- Modify: `event-organizer/event_organizer/schemas/me.py`
- Modify: `event-organizer/event_organizer/routers/me.py`
- Test: `event-organizer/tests/test_me_api.py`

**Interfaces:**
- Consumes: `ISchedulingClient.get_booking_detail` (Task 1), `get_bookings`.
- Produces: `BookingDetailItem` schema; route `GET /api/me/bookings/{booking_id}`.

- [ ] **Step 1: Write the failing test**

In `tests/test_me_api.py`, add `get_booking_detail` to `_FakeScheduling` and make its `get_bookings` row carry the extra fields:

```python
    async def get_bookings(self, host_user_id):
        return [
            {
                "id": "b1",
                "start_time": "2026-10-01T09:00:00Z",
                "end_time": "2026-10-01T09:30:00Z",
                "status": "confirmed",
                "client_user_id": str(uuid4()),
                "host_user_id": str(host_user_id),
                "attendee_time_zone": "Europe/Berlin",
                "created_at": "2026-09-01T08:00:00Z",
                "field_answers": [{"key": "note", "label": "Комментарий", "type": "text", "value": "привет"}],
            }
        ]

    async def get_booking_detail(self, booking_id):
        return {
            "uid": str(booking_id),
            "title": "Консультация",
            "start_time": "2026-10-01T09:00:00Z",
            "end_time": "2026-10-01T09:30:00Z",
            "status": "confirmed",
            "host": {"email": "org@x.io", "name": "Org", "time_zone": "UTC", "locale": None},
            "client": {"email": "anna@x.io", "name": "Анна", "time_zone": "Europe/Berlin", "locale": None},
        }
```

Add tests (mirror the existing `_auth(uid)` helper the file already uses):

```python
@pytest.mark.asyncio
async def test_booking_detail_merges_row_and_detail() -> None:
    from httpx import ASGITransport, AsyncClient

    app, _, _ = _app_and_fakes()
    uid = uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/me/bookings/b1", headers=_auth(uid))
    assert r.status_code == 200
    body = r.json()
    assert body["title"] == "Консультация"
    assert body["client_name"] == "Анна"
    assert body["client_email"] == "anna@x.io"
    assert body["client_time_zone"] == "Europe/Berlin"
    assert body["field_answers"] == [{"label": "Комментарий", "value": "привет"}]


@pytest.mark.asyncio
async def test_booking_detail_unknown_id_is_404() -> None:
    from httpx import ASGITransport, AsyncClient

    app, _, _ = _app_and_fakes()
    uid = uuid4()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://t") as c:
        r = await c.get("/api/me/bookings/does-not-exist", headers=_auth(uid))
    assert r.status_code == 404
```

(If the file's existing bookings test uses `TestClient` instead of `AsyncClient`, match that style — check the current `test_get_bookings` in the file and reuse its client + `_auth` exactly.)

- [ ] **Step 2: Run to verify it fails**

Run: `cd event-organizer && TEST_POSTGRES_DSN=... uv run pytest tests/test_me_api.py -q`
Expected: FAIL — route missing (404 for a valid id too, or import error on `BookingDetailItem`).

- [ ] **Step 3: Implement the schema**

In `schemas/me.py` add:

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

- [ ] **Step 4: Implement the route**

In `routers/me.py`, import `BookingDetailItem`, `BookingFieldAnswer`, `NotFoundError`, and `UUID`; add a `_stringify` helper and the route:

```python
def _stringify(value: object) -> str:
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


@me_router.get("/bookings/{booking_id}", response_model=BookingDetailItem)
async def get_booking_detail(
    booking_id: UUID, scheduling: FromDishka[ISchedulingClient], me: RequireOrganizer
) -> BookingDetailItem:
    rows = await scheduling.get_bookings(me.user_id)
    row = next((r for r in rows if r["id"] == str(booking_id)), None)
    if row is None:
        raise NotFoundError("booking not found")
    detail = await scheduling.get_booking_detail(booking_id)
    client = detail.get("client") or {}
    return BookingDetailItem(
        id=detail["uid"],
        title=detail["title"],
        start_time=detail["start_time"],
        end_time=detail["end_time"],
        status=detail["status"],
        client_name=client.get("name"),
        client_email=client.get("email"),
        client_time_zone=row.get("attendee_time_zone"),
        created_at=row.get("created_at"),
        field_answers=[BookingFieldAnswer(label=a["label"], value=_stringify(a["value"])) for a in row.get("field_answers", [])],
    )
```

Import `NotFoundError` from `event_organizer.errors` and `UUID` from `uuid`.
Register the new route **after** the existing `GET /bookings` (FastAPI matches the
static `/bookings` before `/bookings/{booking_id}`; order does not actually matter
since paths differ, but keep them adjacent for readability).

- [ ] **Step 5: Run to verify it passes**

Run: `cd event-organizer && TEST_POSTGRES_DSN=... uv run pytest tests/test_me_api.py -q`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

```bash
cd event-organizer && uv run ruff check event_organizer/routers/me.py event_organizer/schemas/me.py
cd .. && git add event-organizer/event_organizer/routers/me.py event-organizer/event_organizer/schemas/me.py event-organizer/tests/test_me_api.py
git commit -m "feat(organizer): GET /api/me/bookings/{id} owner-scoped detail"
```

---

### Task 3: Frontend default route + menu order

**Files:**
- Modify: `event-organizer-frontend/src/modules/shared/routing.ts`
- Modify: `event-organizer-frontend/src/modules/shared/routing.test.ts`
- Modify: `event-organizer-frontend/src/modules/app/OrganizerLayout.tsx`
- Modify: `event-organizer-frontend/src/modules/app/OrganizerLayout.test.tsx`
- Modify: `event-organizer-frontend/src/App.tsx`

- [ ] **Step 1: Update the routing test**

In `routing.test.ts`, change the `parseRoute` expectation:

```ts
    expect(parseRoute('/')).toEqual({ name: 'bookings' })
    expect(parseRoute('/schedule')).toEqual({ name: 'schedule' })
    expect(parseRoute('/bookings')).toEqual({ name: 'bookings' })
```

- [ ] **Step 2: Run to verify it fails**

Run: `cd event-organizer-frontend && npx vitest run src/modules/shared/routing.test.ts`
Expected: FAIL — `parseRoute('/')` still returns `schedule`.

- [ ] **Step 3: Flip the default in `parseRoute`**

In `routing.ts`:

```ts
export function parseRoute(pathname: string): AppRoute {
  if (pathname === '/login') {
    return { name: 'login' }
  }
  if (pathname === '/schedule') {
    return { name: 'schedule' }
  }
  if (pathname === '/' || pathname === '/bookings') {
    return { name: 'bookings' }
  }
  if (pathname === '/profile') {
    return { name: 'profile' }
  }
  return { name: 'not-found' }
}
```

- [ ] **Step 4: Reorder the menu**

In `OrganizerLayout.tsx`, reorder `NAV_ITEMS` and fix paths/matchers:

```tsx
const NAV_ITEMS: NavItem[] = [
  {
    label: 'Брони',
    path: '/',
    icon: 'dashboard',
    match: (pathname) => pathname === '/' || pathname === '/bookings',
  },
  {
    label: 'Расписание',
    path: '/schedule',
    icon: 'bookings',
    match: (pathname) => pathname === '/schedule',
  },
  {
    label: 'Профиль',
    path: '/profile',
    icon: 'users',
    match: (pathname) => pathname === '/profile',
  },
]
```

- [ ] **Step 5: Update the OrganizerLayout nav test + not-found button**

In `OrganizerLayout.test.tsx`, the nav-order assertion becomes:

```ts
    expect(labels).toEqual(['Брони', 'Расписание', 'Профиль'])
```

and the "marks the active item by pathname" test: mounting `'/bookings'` → active
label `'Брони'` (was `'Брони'` already; keep whichever path the test used — if it
mounted `'/'` expecting `'Расписание'`, update the expected label to `'Брони'`).

In `App.tsx`, the not-found "Вернуться" button now lands on Bookings; relabel it and
keep the redirect:

```tsx
          <button type="button" onClick={() => navigateTo('/', { replace: true })}>
            На главную
          </button>
```

- [ ] **Step 6: Run the affected suites + build**

Run: `cd event-organizer-frontend && npx vitest run src/modules/shared/routing.test.ts src/modules/app/OrganizerLayout.test.tsx && npm run build`
Expected: PASS, tsc clean.

- [ ] **Step 7: Commit**

```bash
git add event-organizer-frontend/src/modules/shared/routing.ts event-organizer-frontend/src/modules/shared/routing.test.ts event-organizer-frontend/src/modules/app/OrganizerLayout.tsx event-organizer-frontend/src/modules/app/OrganizerLayout.test.tsx event-organizer-frontend/src/App.tsx
git commit -m "feat(organizer-fe): default to Bookings, move it to the top of the menu"
```

---

### Task 4: Frontend detail api/types + BookingDetailPanel

**Files:**
- Modify: `event-organizer-frontend/src/modules/bookings/types.ts`
- Modify: `event-organizer-frontend/src/modules/bookings/bookingsApi.ts`
- Create: `event-organizer-frontend/src/modules/bookings/BookingDetailPanel.tsx`
- Test: `event-organizer-frontend/src/modules/bookings/BookingDetailPanel.test.tsx`

**Interfaces:**
- Produces: `getBookingDetail(id: string): Promise<BookingDetail>`; `BookingDetail`, `BookingFieldAnswer` types; `BookingDetailPanel({ bookingId, organizerTz })`.

- [ ] **Step 1: Add types**

Append to `types.ts`:

```ts
export type BookingFieldAnswer = { label: string; value: string }

export type BookingDetail = {
  id: string
  title: string
  start_time: string
  end_time: string
  status: string
  client_name: string | null
  client_email: string | null
  client_time_zone: string | null
  created_at: string | null
  field_answers: BookingFieldAnswer[]
}
```

- [ ] **Step 2: Add the api call**

Append to `bookingsApi.ts`:

```ts
import type { BookingDetail, BookingRow } from './types.ts'
// ...existing getBookings...
export async function getBookingDetail(id: string): Promise<BookingDetail> {
  return apiRequest<BookingDetail>(`/api/me/bookings/${id}`)
}
```

(Merge the import line with the existing `BookingRow` import.)

- [ ] **Step 3: Write the failing panel test**

Create `BookingDetailPanel.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { BookingDetailPanel } from './BookingDetailPanel.tsx'
import * as api from './bookingsApi.ts'
import type { BookingDetail } from './types.ts'

const detail: BookingDetail = {
  id: 'b1',
  title: 'Консультация',
  start_time: '2026-10-01T09:00:00Z',
  end_time: '2026-10-01T09:30:00Z',
  status: 'confirmed',
  client_name: 'Анна',
  client_email: 'anna@x.io',
  client_time_zone: 'Europe/Berlin',
  created_at: '2026-09-01T08:00:00Z',
  field_answers: [{ label: 'Комментарий', value: 'привет' }],
}

let container: HTMLDivElement
let root: Root
async function mount(bookingId: string | null) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<BookingDetailPanel bookingId={bookingId} organizerTz="Europe/Moscow" />))
  await act(async () => {})
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

describe('BookingDetailPanel', () => {
  it('shows a placeholder when nothing is selected', async () => {
    await mount(null)
    expect(container.querySelector('.detail-empty')).not.toBeNull()
  })

  it('renders the fetched booking detail', async () => {
    vi.spyOn(api, 'getBookingDetail').mockResolvedValue(detail)
    await mount('b1')
    expect(container.textContent).toContain('Консультация')
    expect(container.textContent).toContain('Анна')
    expect(container.textContent).toContain('anna@x.io')
    expect(container.textContent).toContain('Комментарий')
  })

  it('shows an error when the fetch fails', async () => {
    vi.spyOn(api, 'getBookingDetail').mockRejectedValue(new Error('nope'))
    await mount('b1')
    expect(container.querySelector('.error-text')).not.toBeNull()
  })
})
```

- [ ] **Step 4: Run to verify it fails**

Run: `cd event-organizer-frontend && npx vitest run src/modules/bookings/BookingDetailPanel.test.tsx`
Expected: FAIL — component missing.

- [ ] **Step 5: Implement `BookingDetailPanel.tsx`**

```tsx
import { useEffect, useState } from 'react'
import { formatRange } from '../shared/format.ts'
import { getBookingDetail } from './bookingsApi.ts'
import type { BookingDetail } from './types.ts'

const STATUS_LABEL: Record<string, string> = { confirmed: 'Подтверждена', cancelled: 'Отменена' }
const STATUS_VARIANT: Record<string, string> = { confirmed: 'badge--confirmed', cancelled: 'badge--cancelled' }

function Field({ label, value }: { label: string; value: string }) {
  return (
    <div className="detail-field">
      <span className="detail-label">{label}</span>
      <span className="detail-value">{value}</span>
    </div>
  )
}

export function BookingDetailPanel({ bookingId, organizerTz }: { bookingId: string | null; organizerTz: string | undefined }) {
  const [detail, setDetail] = useState<BookingDetail | null>(null)
  const [error, setError] = useState(false)
  const [loading, setLoading] = useState(false)

  useEffect(() => {
    if (!bookingId) {
      setDetail(null)
      setError(false)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(false)
    getBookingDetail(bookingId)
      .then((d) => {
        if (!cancelled) setDetail(d)
      })
      .catch(() => {
        if (!cancelled) setError(true)
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [bookingId])

  if (!bookingId) return <div className="detail-panel detail-empty">Выберите бронь, чтобы увидеть детали</div>
  if (loading) return <div className="detail-panel">Загрузка…</div>
  if (error) return <div className="detail-panel error-text">Не удалось загрузить бронь</div>
  if (!detail) return <div className="detail-panel" />

  return (
    <div className="detail-panel">
      <div className="detail-head">
        <h2>{detail.title}</h2>
        <span className={`badge ${STATUS_VARIANT[detail.status] ?? ''}`}>{STATUS_LABEL[detail.status] ?? detail.status}</span>
      </div>
      <Field label="Дата и время" value={formatRange(detail.start_time, detail.end_time, organizerTz)} />
      {detail.client_name && <Field label="Клиент" value={detail.client_name} />}
      {detail.client_email && <Field label="Email" value={detail.client_email} />}
      {detail.client_time_zone && <Field label="Часовой пояс клиента" value={detail.client_time_zone} />}
      {detail.created_at && <Field label="Создана" value={formatRange(detail.created_at, detail.created_at, organizerTz)} />}
      {detail.field_answers.length > 0 && (
        <div className="detail-answers">
          <h3>Анкета</h3>
          {detail.field_answers.map((a) => (
            <Field key={a.label} label={a.label} value={a.value} />
          ))}
        </div>
      )}
    </div>
  )
}
```

(Note: `formatRange(created_at, created_at, tz)` renders a single date/time — the
existing helper formats a start==end range as one timestamp; if it renders "—",
add a `formatDateTime` helper to `shared/format.ts` in this step and use it for
`Создана`. Check `formatRange`'s behaviour before finalizing.)

- [ ] **Step 6: Run to verify it passes**

Run: `cd event-organizer-frontend && npx vitest run src/modules/bookings/BookingDetailPanel.test.tsx`
Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add event-organizer-frontend/src/modules/bookings/types.ts event-organizer-frontend/src/modules/bookings/bookingsApi.ts event-organizer-frontend/src/modules/bookings/BookingDetailPanel.tsx event-organizer-frontend/src/modules/bookings/BookingDetailPanel.test.tsx
git commit -m "feat(organizer-fe): booking detail api + BookingDetailPanel"
```

---

### Task 5: Master-detail BookingsPage + CSS

**Files:**
- Modify: `event-organizer-frontend/src/modules/bookings/BookingsPage.tsx`
- Modify: `event-organizer-frontend/src/index.css`
- Test: `event-organizer-frontend/src/modules/bookings/BookingsPage.test.tsx`

**Interfaces:**
- Consumes: `BookingDetailPanel` (Task 4).

- [ ] **Step 1: Read the current BookingsPage test to preserve its mocks**

Read `BookingsPage.test.tsx` (mocks `getBookings`/`getProfile`) so new assertions extend it.

- [ ] **Step 2: Make rows selectable + add the two-column layout**

In `BookingsPage.tsx`: add `const [selectedId, setSelectedId] = useState<string | null>(null)`.
Change `BookingList` rows from `<div className="booking-row">` to
`<button type="button" className={`booking-row${sel === b.id ? ' is-selected' : ''}`} onClick={() => onSelect(b.id)}>`,
threading `onSelect` and `sel` props down. Wrap the page body in:

```tsx
<div className="bookings-layout">
  <div className="bookings-list">
    {/* existing Предстоящие / Прошедшие groups, now passing onSelect + selectedId */}
  </div>
  <BookingDetailPanel bookingId={selectedId} organizerTz={timeZone} />
</div>
```

Keep the `page-head` (`<h1>Брони</h1>`) above the layout. Keep the empty state
("У вас пока нет броней") for zero bookings (render it without the two-column split).

- [ ] **Step 3: Add CSS**

In `index.css`:

```css
.bookings-layout {
  display: grid;
  grid-template-columns: minmax(240px, 320px) 1fr;
  gap: 20px;
  align-items: start;
}
@media (max-width: 720px) {
  .bookings-layout { grid-template-columns: 1fr; }
}
.bookings-list { display: grid; gap: 20px; }
button.booking-row {
  width: 100%;
  text-align: left;
  background: var(--card);
  color: var(--text);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 14px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  cursor: pointer;
}
button.booking-row.is-selected {
  border-color: var(--primary);
  box-shadow: inset 3px 0 0 var(--primary);
}
.detail-panel {
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--card);
  padding: 20px;
  display: grid;
  gap: 12px;
}
.detail-empty { color: var(--muted); text-align: center; padding: 40px 20px; }
.detail-head { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.detail-head h2 { font-size: 16px; font-weight: 600; }
.detail-field { display: grid; gap: 2px; }
.detail-label { font-size: 12px; color: var(--text-2); }
.detail-value { font-size: 14px; color: var(--text); }
.detail-answers { display: grid; gap: 10px; border-top: 1px solid var(--border); padding-top: 12px; }
.detail-answers h3 { font-size: 13px; font-weight: 600; }
```

(The existing `.booking-row` div rule can stay or be removed; the new
`button.booking-row` is more specific. Remove the old `.booking-row { ... }` block
if it conflicts visually.)

- [ ] **Step 4: Extend the BookingsPage test**

Keep the existing load/empty tests. Add:

```tsx
it('selects a booking and shows its detail panel', async () => {
  vi.spyOn(bookingsApi, 'getBookings').mockResolvedValue([
    { id: 'b1', start_time: '2026-10-01T09:00:00Z', end_time: '2026-10-01T09:30:00Z', status: 'confirmed' },
  ])
  vi.spyOn(profileApi, 'getProfile').mockResolvedValue({ name: 'Org', email: 'o@x.io', time_zone: 'Europe/Moscow' })
  vi.spyOn(detailApi, 'getBookingDetail').mockResolvedValue(detail /* the BookingDetail fixture */)
  await mount()
  expect(container.querySelector('.detail-empty')).not.toBeNull()
  const row = container.querySelector('button.booking-row') as HTMLButtonElement
  await act(async () => row.click())
  await act(async () => {})
  expect(container.querySelector('.booking-row.is-selected')).not.toBeNull()
  expect(container.textContent).toContain('Консультация')
})
```

(Import `getBookingDetail` from `./bookingsApi.ts` — it and `getBookings` are the
same module, so `vi.spyOn(bookingsApi, 'getBookingDetail')`. Reuse the file's
existing `getProfile` mock path.)

- [ ] **Step 5: Run the full suite + build + lint**

Run: `cd event-organizer-frontend && npx vitest run && npm run build && npm run lint`
Expected: all PASS, tsc clean, eslint clean.

- [ ] **Step 6: Commit**

```bash
git add event-organizer-frontend/src/modules/bookings/BookingsPage.tsx event-organizer-frontend/src/index.css event-organizer-frontend/src/modules/bookings/BookingsPage.test.tsx
git commit -m "feat(organizer-fe): master-detail bookings page with right-side detail panel"
```

---

### Task 6: Docs

**Files:**
- Modify: `event-organizer-frontend/CLAUDE.md`
- Modify: `event-organizer/CLAUDE.md`

- [ ] **Step 1: Update both CLAUDE.md files**

- `event-organizer-frontend/CLAUDE.md`: default route is now Bookings; menu order
  Брони/Расписание/Профиль; the bookings screen is master-detail with a
  `BookingDetailPanel` fed by `GET /api/me/bookings/{id}`.
- `event-organizer/CLAUDE.md`: add the `GET /api/me/bookings/{id}` endpoint
  (owner-scoped: gated against the caller's own `get_bookings`; merges
  event-scheduling `/detail` with the list row's answers/tz/created) to the
  endpoints table and the `scheduling_client` bullet (`get_booking_detail`).

- [ ] **Step 2: Commit**

```bash
git add event-organizer-frontend/CLAUDE.md event-organizer/CLAUDE.md
git commit -m "docs: organizer bookings default + detail endpoint"
```

---

## Notes for the executor

- Everything is in the **root** `events` repo (event-organizer + event-organizer-frontend + docs are all tracked by it). Commit from the repo root; never `git add -A`.
- Create the throwaway BFF test DB once: `docker compose exec -T postgres psql -U postgres -c "CREATE DATABASE event_organizer_test;"` (ignore "already exists"); drop it at the end.
- Verify `formatRange`'s single-timestamp behaviour before using it for `Создана`; add a `formatDateTime` to `shared/format.ts` if it doesn't render a lone timestamp cleanly.
- `tsc -b` typechecks tests — keep `BookingDetail` fixtures fully typed.
