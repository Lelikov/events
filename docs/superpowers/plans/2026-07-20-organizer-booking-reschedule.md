# Organizer Booking Reschedule Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a «Перенести» action to a booking in the organizer cabinet — pick a new available slot; the booking moves in place (same host). Defer «Переназначить».

**Architecture:** Two owner-scoped BFF endpoints (available slots for a booking on a date; reschedule proxy) over event-scheduling's existing `/slots` + `/reschedule`. A detail-panel button opens a reschedule modal that lists available slots and confirms.

**Tech Stack:** Python 3.14 FastAPI, pytest; React 19 + Vite + TS, plain CSS, vitest + happy-dom.

## Global Constraints

- No `else if`; avoid `else`. Russian UI copy. Design-system tokens/classes.
- event-scheduling is NOT modified. Ownership by construction: booking id gated against the caller's own `get_bookings(me.user_id)`.
- BFF tests run against Postgres: `TEST_POSTGRES_DSN=postgresql+asyncpg://postgres:postgres@localhost:5432/event_organizer_test` (create the throwaway DB first).
- Frontend `tsc -b` typechecks tests.
- Reschedule actor headers: `actor-source: organizer`, `actor-user-id: <me.user_id>`.

---

### Task 1: BFF scheduling-client — get_slots, reschedule_booking, 409 forwarding

**Files:**
- Modify: `event-organizer/event_organizer/adapters/interfaces.py`
- Modify: `event-organizer/event_organizer/adapters/scheduling_client.py`
- Test: `event-organizer/tests/test_scheduling_client.py`

- [ ] **Step 1: Failing tests** — append to `test_scheduling_client.py` (imports already have `NotFoundError, UpstreamError, ValidationError`; add `ConflictError`):

```python
@pytest.mark.asyncio
async def test_get_slots_params() -> None:
    et = str(uuid4())

    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/slots"
        assert req.url.params["event_type_id"] == et
        assert req.url.params["time_zone"] == "Europe/Moscow"
        return httpx.Response(200, json={"event_type_id": et, "time_zone": "Europe/Moscow", "slots": {"2026-10-01": ["2026-10-01T09:00:00Z"]}})

    out = await _c(h).get_slots(et, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", "Europe/Moscow")
    assert out["slots"]["2026-10-01"] == ["2026-10-01T09:00:00Z"]


@pytest.mark.asyncio
async def test_reschedule_sends_body_and_actor_headers() -> None:
    bid, uid = str(uuid4()), uuid4()

    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == f"/api/v1/bookings/{bid}/reschedule"
        assert req.headers["actor-source"] == "organizer"
        assert req.headers["actor-user-id"] == str(uid)
        import json
        assert json.loads(req.content)["start_time"] == "2026-10-01T09:00:00Z"
        return httpx.Response(200, json={"id": bid, "status": "confirmed"})

    out = await _c(h).reschedule_booking(bid, "2026-10-01T09:00:00Z", uid)
    assert out["status"] == "confirmed"


@pytest.mark.asyncio
async def test_409_raises_conflict_with_detail() -> None:
    def h(_req: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "host is not available at the new time"})

    with pytest.raises(ConflictError) as ei:
        await _c(h).reschedule_booking(str(uuid4()), "2026-10-01T09:00:00Z", uuid4())
    assert "not available" in str(ei.value)
```

- [ ] **Step 2: Run → fail.** `cd event-organizer && TEST_POSTGRES_DSN=... uv run pytest tests/test_scheduling_client.py -q` (create DB first: `docker compose exec -T postgres psql -U postgres -c "CREATE DATABASE event_organizer_test;"`).

- [ ] **Step 3: Implement.** In `interfaces.py` add to `ISchedulingClient`:

```python
    async def get_slots(self, event_type_id: str, start_iso: str, end_iso: str, time_zone: str) -> dict: ...
    async def reschedule_booking(self, booking_id: str, start_time_iso: str, actor_user_id: UUID) -> dict: ...
```

In `scheduling_client.py`: add `ConflictError` to the errors import; add a 409 branch to `_ok` (after the 422 branch, before the generic `UpstreamError`):

```python
        if resp.status_code == httpx.codes.CONFLICT:
            raise ConflictError(cls._detail(resp))
```

Add the two methods (after `get_booking_detail`):

```python
    async def get_slots(self, event_type_id: str, start_iso: str, end_iso: str, time_zone: str) -> dict:
        async with self._http() as c:
            resp = await c.get(
                f"{self._base_url}/api/v1/slots",
                params={"event_type_id": event_type_id, "start": start_iso, "end": end_iso, "time_zone": time_zone},
            )
        return self._ok(resp)

    async def reschedule_booking(self, booking_id: str, start_time_iso: str, actor_user_id: UUID) -> dict:
        headers = {"actor-source": "organizer", "actor-user-id": str(actor_user_id)}
        async with self._http() as c:
            resp = await c.post(
                f"{self._base_url}/api/v1/bookings/{booking_id}/reschedule",
                json={"start_time": start_time_iso},
                headers=headers,
            )
        return self._ok(resp)
```

- [ ] **Step 4: Run → pass.** Same command. Lint: `uv run ruff check event_organizer/adapters/`.

- [ ] **Step 5: Commit.**

```bash
git add event-organizer/event_organizer/adapters/scheduling_client.py event-organizer/event_organizer/adapters/interfaces.py event-organizer/tests/test_scheduling_client.py
git commit -m "feat(organizer): scheduling client get_slots + reschedule_booking (409 forwarding)"
```

---

### Task 2: BFF routes — GET slots + POST reschedule

**Files:**
- Modify: `event-organizer/event_organizer/schemas/me.py`
- Modify: `event-organizer/event_organizer/routers/me.py`
- Test: `event-organizer/tests/test_me_api.py`

- [ ] **Step 1: Extend the fake + failing tests.** In `test_me_api.py` `_FakeScheduling`, add:

```python
    async def get_slots(self, event_type_id, start_iso, end_iso, time_zone):
        self.seen_slots = (event_type_id, start_iso, end_iso, time_zone)
        return {"event_type_id": event_type_id, "time_zone": time_zone, "slots": {"2026-10-01": ["2026-10-01T09:00:00Z"]}}

    async def reschedule_booking(self, booking_id, start_time_iso, actor_user_id):
        self.rescheduled = (booking_id, start_time_iso, actor_user_id)
        return {"id": booking_id, "status": "confirmed", "start_time": start_time_iso}
```

Also add `"event_type_id": "et1"` to the fake `get_bookings` row (so the slots route can resolve it).

Add tests (TestClient style, `sessionmaker_fixture`, `_auth`):

```python
@pytest.mark.asyncio
async def test_booking_slots_owned(sessionmaker_fixture) -> None:
    from starlette.testclient import TestClient
    app, sched, _ = _app_and_fakes()
    with TestClient(app) as c:
        r = c.get("/api/me/bookings/b1/slots?date=2026-10-01&time_zone=Europe/Moscow", headers=_auth(uuid4()))
        assert r.status_code == 200
        body = r.json()
        assert body["slots"] == ["2026-10-01T09:00:00Z"]
        assert sched.seen_slots[0] == "et1"  # resolved event_type_id


@pytest.mark.asyncio
async def test_booking_slots_unknown_id_404(sessionmaker_fixture) -> None:
    from starlette.testclient import TestClient
    app, _, _ = _app_and_fakes()
    with TestClient(app) as c:
        r = c.get("/api/me/bookings/nope/slots?date=2026-10-01&time_zone=Europe/Moscow", headers=_auth(uuid4()))
        assert r.status_code == 404


@pytest.mark.asyncio
async def test_reschedule_owned_forwards(sessionmaker_fixture) -> None:
    from starlette.testclient import TestClient
    app, sched, _ = _app_and_fakes()
    uid = uuid4()
    with TestClient(app) as c:
        r = c.post("/api/me/bookings/b1/reschedule", headers=_auth(uid), json={"start_time": "2026-10-01T09:00:00Z"})
        assert r.status_code == 200
        assert sched.rescheduled[0] == "b1"
        assert sched.rescheduled[1] == "2026-10-01T09:00:00Z"
        assert sched.rescheduled[2] == uid  # actor = organizer


@pytest.mark.asyncio
async def test_reschedule_unknown_id_404(sessionmaker_fixture) -> None:
    from starlette.testclient import TestClient
    app, _, _ = _app_and_fakes()
    with TestClient(app) as c:
        r = c.post("/api/me/bookings/nope/reschedule", headers=_auth(uuid4()), json={"start_time": "2026-10-01T09:00:00Z"})
        assert r.status_code == 404
```

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Schemas.** In `schemas/me.py`:

```python
class BookingSlotsResponse(BaseModel):
    date: str
    time_zone: str
    slots: list[str]


class RescheduleRequest(BaseModel):
    start_time: str
```

- [ ] **Step 4: Routes.** In `routers/me.py`, add imports (`from datetime import datetime, timedelta`, `from zoneinfo import ZoneInfo`, the two schemas) and a helper + routes:

```python
def _day_window_utc(date_str: str, time_zone: str) -> tuple[str, str]:
    tz = ZoneInfo(time_zone)
    day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=tz)
    start = day.astimezone(ZoneInfo("UTC"))
    end = (day + timedelta(days=1)).astimezone(ZoneInfo("UTC"))
    return start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _owned_row(scheduling: ISchedulingClient, user_id, booking_id: str) -> dict:
    rows = await scheduling.get_bookings(user_id)
    row = next((r for r in rows if r["id"] == booking_id), None)
    if row is None:
        raise NotFoundError("booking not found")
    return row


@me_router.get("/bookings/{booking_id}/slots", response_model=BookingSlotsResponse)
async def get_booking_slots(
    booking_id: str, date: str, time_zone: str, scheduling: FromDishka[ISchedulingClient], me: RequireOrganizer
) -> BookingSlotsResponse:
    row = await _owned_row(scheduling, me.user_id, booking_id)
    start_iso, end_iso = _day_window_utc(date, time_zone)
    result = await scheduling.get_slots(row["event_type_id"], start_iso, end_iso, time_zone)
    slots = result.get("slots", {}).get(date, [])
    return BookingSlotsResponse(date=date, time_zone=time_zone, slots=slots)


@me_router.post("/bookings/{booking_id}/reschedule")
async def reschedule_booking(
    booking_id: str, body: RescheduleRequest, scheduling: FromDishka[ISchedulingClient], me: RequireOrganizer
) -> dict:
    await _owned_row(scheduling, me.user_id, booking_id)
    return await scheduling.reschedule_booking(booking_id, body.start_time, me.user_id)
```

Refactor the existing `get_booking_detail`'s ownership block to reuse `_owned_row`
(optional; keep behaviour identical). Ensure `NotFoundError` and `ISchedulingClient`
are imported (they are).

- [ ] **Step 5: Run → pass.** Lint `uv run ruff check event_organizer/routers/me.py event_organizer/schemas/me.py`.

- [ ] **Step 6: Commit.**

```bash
git add event-organizer/event_organizer/routers/me.py event-organizer/event_organizer/schemas/me.py event-organizer/tests/test_me_api.py
git commit -m "feat(organizer): GET /api/me/bookings/{id}/slots + POST .../reschedule (owner-scoped)"
```

---

### Task 3: Frontend api + RescheduleModal

**Files:**
- Modify: `event-organizer-frontend/src/modules/bookings/bookingsApi.ts`
- Create: `event-organizer-frontend/src/modules/bookings/RescheduleModal.tsx`
- Test: `event-organizer-frontend/src/modules/bookings/RescheduleModal.test.tsx`
- Modify: `event-organizer-frontend/src/index.css`

- [ ] **Step 1: Api.** In `bookingsApi.ts`:

```ts
export async function getBookingSlots(id: string, date: string, timeZone: string): Promise<{ date: string; time_zone: string; slots: string[] }> {
  const q = new URLSearchParams({ date, time_zone: timeZone })
  return apiRequest(`/api/me/bookings/${id}/slots?${q.toString()}`)
}

export async function rescheduleBooking(id: string, startTime: string): Promise<void> {
  await apiRequest(`/api/me/bookings/${id}/reschedule`, { method: 'POST', body: { start_time: startTime } })
}
```

- [ ] **Step 2: Failing modal test.** Create `RescheduleModal.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { RescheduleModal } from './RescheduleModal.tsx'
import * as api from './bookingsApi.ts'

let container: HTMLDivElement
let root: Root
async function mount(onClose = vi.fn(), onDone = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () =>
    root.render(<RescheduleModal bookingId="b1" currentStart="2026-10-01T09:00:00Z" organizerTz="Europe/Moscow" onClose={onClose} onRescheduled={onDone} />),
  )
  await act(async () => {})
  return { onClose, onDone }
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

describe('RescheduleModal', () => {
  it('loads slots for the default date and confirms a pick', async () => {
    vi.spyOn(api, 'getBookingSlots').mockResolvedValue({ date: '2026-10-01', time_zone: 'Europe/Moscow', slots: ['2026-10-01T09:00:00Z', '2026-10-01T10:00:00Z'] })
    const resch = vi.spyOn(api, 'rescheduleBooking').mockResolvedValue()
    const { onDone } = await mount()
    const chips = [...container.querySelectorAll('.slot-chip')] as HTMLButtonElement[]
    expect(chips.length).toBe(2)
    const confirm = [...container.querySelectorAll('.modal-actions button')].find((b) => b.textContent === 'Перенести') as HTMLButtonElement
    expect(confirm.disabled).toBe(true)
    await act(async () => chips[1].click())
    expect(confirm.disabled).toBe(false)
    await act(async () => confirm.click())
    await act(async () => {})
    expect(resch).toHaveBeenCalledWith('b1', '2026-10-01T10:00:00Z')
    expect(onDone).toHaveBeenCalled()
  })

  it('shows the error and stays open when reschedule fails', async () => {
    vi.spyOn(api, 'getBookingSlots').mockResolvedValue({ date: '2026-10-01', time_zone: 'Europe/Moscow', slots: ['2026-10-01T10:00:00Z'] })
    const { ApiError } = await import('../shared/api.ts')
    vi.spyOn(api, 'rescheduleBooking').mockRejectedValue(new ApiError('Слот занят', 409, null))
    const { onDone } = await mount()
    await act(async () => (container.querySelector('.slot-chip') as HTMLButtonElement).click())
    await act(async () => (([...container.querySelectorAll('.modal-actions button')].find((b) => b.textContent === 'Перенести')) as HTMLButtonElement).click())
    await act(async () => {})
    expect(container.querySelector('.error-text')?.textContent).toContain('Слот занят')
    expect(onDone).not.toHaveBeenCalled()
    expect(container.querySelector('.modal-overlay')).not.toBeNull()
  })
})
```

- [ ] **Step 3: Run → fail** (module missing).

- [ ] **Step 4: Implement `RescheduleModal.tsx`:**

```tsx
import { useEffect, useState } from 'react'
import { ApiError } from '../shared/api.ts'
import { formatDateTime } from '../shared/format.ts'
import { getBookingSlots, rescheduleBooking } from './bookingsApi.ts'

function localDate(iso: string, tz: string): string {
  // YYYY-MM-DD of the iso instant in the organizer's tz, for the date input.
  const parts = new Intl.DateTimeFormat('en-CA', { timeZone: tz, year: 'numeric', month: '2-digit', day: '2-digit' }).format(new Date(iso))
  return parts // en-CA gives YYYY-MM-DD
}

type Props = { bookingId: string; currentStart: string; organizerTz: string | undefined; onClose: () => void; onRescheduled: () => void }

export function RescheduleModal({ bookingId, currentStart, organizerTz, onClose, onRescheduled }: Props) {
  const tz = organizerTz ?? 'UTC'
  const [date, setDate] = useState(() => localDate(currentStart, tz))
  const [slots, setSlots] = useState<string[] | null>(null)
  const [loadError, setLoadError] = useState(false)
  const [picked, setPicked] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    setSlots(null)
    setLoadError(false)
    setPicked(null)
    getBookingSlots(bookingId, date, tz)
      .then((r) => {
        if (!cancelled) setSlots(r.slots)
      })
      .catch(() => {
        if (!cancelled) setLoadError(true)
      })
    return () => {
      cancelled = true
    }
  }, [bookingId, date, tz])

  async function confirm() {
    if (!picked) return
    setSaving(true)
    setSubmitError(null)
    try {
      await rescheduleBooking(bookingId, picked)
      onRescheduled()
    } catch (err) {
      setSubmitError(err instanceof ApiError ? err.message : 'Не удалось перенести. Попробуйте ещё раз.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="modal-overlay" onMouseDown={onClose}>
      <div className="modal-content leave-modal" role="dialog" aria-modal="true" onMouseDown={(e) => e.stopPropagation()}>
        <div className="modal-header">
          <h2>Перенести бронь</h2>
        </div>
        <label className="field">
          <span>Дата</span>
          <input type="date" className="field-control" value={date} onChange={(e) => setDate(e.target.value)} />
        </label>
        <div className="slot-grid">
          {slots === null && !loadError && <span className="muted">Загрузка…</span>}
          {loadError && <span className="error-text">Не удалось загрузить слоты</span>}
          {slots !== null && slots.length === 0 && <span className="muted">Нет свободных слотов на эту дату</span>}
          {slots?.map((s) => (
            <button
              type="button"
              key={s}
              className={`slot-chip${picked === s ? ' is-selected' : ''}`}
              onClick={() => setPicked(s)}
            >
              {formatDateTime(s, tz)}
            </button>
          ))}
        </div>
        {submitError && <p className="error-text">{submitError}</p>}
        <div className="modal-actions">
          <button type="button" className="secondary" onClick={onClose}>
            Отмена
          </button>
          <button type="button" onClick={confirm} disabled={!picked || saving}>
            Перенести
          </button>
        </div>
      </div>
    </div>
  )
}
```

- [ ] **Step 5: CSS** in `index.css`:

```css
.slot-grid { display: flex; flex-wrap: wrap; gap: 8px; min-height: 40px; align-content: start; }
button.slot-chip {
  background: var(--bg-soft);
  border: 1px solid var(--border);
  color: var(--text);
  border-radius: 8px;
  padding: 6px 12px;
  font-size: 13px;
  cursor: pointer;
}
button.slot-chip.is-selected { border-color: var(--primary); background: var(--primary-pale, rgba(79,110,242,0.1)); color: var(--primary); }
.leave-modal .field + .slot-grid { margin-top: 4px; }
```

- [ ] **Step 6: Run modal tests → pass.**

- [ ] **Step 7: Commit.**

```bash
git add event-organizer-frontend/src/modules/bookings/bookingsApi.ts event-organizer-frontend/src/modules/bookings/RescheduleModal.tsx event-organizer-frontend/src/modules/bookings/RescheduleModal.test.tsx event-organizer-frontend/src/index.css
git commit -m "feat(organizer-fe): RescheduleModal (slot picker) + api"
```

---

### Task 4: «Перенести» button in the panel + list/detail refresh

**Files:**
- Modify: `event-organizer-frontend/src/modules/bookings/BookingDetailPanel.tsx`
- Modify: `event-organizer-frontend/src/modules/bookings/BookingsPage.tsx`
- Test: `event-organizer-frontend/src/modules/bookings/BookingDetailPanel.test.tsx`

- [ ] **Step 1: Panel button + modal.** In `BookingDetailPanel.tsx`, add an `onRescheduled?: () => void` prop and, after the fields (inside the loaded return), a «Перенести» button shown only for a confirmed future booking, wired to open `RescheduleModal`:

```tsx
  const [rescheduling, setRescheduling] = useState(false)
  const canReschedule = detail.status === 'confirmed' && new Date(detail.start_time).getTime() > Date.now()
  // ...in JSX after .detail-fields / .detail-answers:
  {canReschedule && (
    <div className="detail-actions">
      <button type="button" onClick={() => setRescheduling(true)}>Перенести</button>
    </div>
  )}
  {rescheduling && (
    <RescheduleModal
      bookingId={detail.id}
      currentStart={detail.start_time}
      organizerTz={organizerTz}
      onClose={() => setRescheduling(false)}
      onRescheduled={() => {
        setRescheduling(false)
        onRescheduled?.()
      }}
    />
  )}
```

Add `.detail-actions { display: flex; gap: 8px; border-top: 1px solid var(--border); padding-top: 12px; }` to `index.css`.

- [ ] **Step 2: Panel test.** Add to `BookingDetailPanel.test.tsx`: with the confirmed future `detail` fixture (change its `start_time` to a future ISO), a «Перенести» button renders; with `status:'cancelled'` (or a past start), it does not. (Use `new Date(Date.now()+86400000).toISOString()` for the future start.)

- [ ] **Step 3: Wire refresh in BookingsPage.** Add a `refreshKey` state; pass `onRescheduled={() => { setRefreshKey((k) => k + 1); reload() }}` to the panel, where `reload()` re-fetches `getBookings()` and updates `rows`/`now`; change the panel `key` to `${selectedId ?? 'none'}:${refreshKey}` so it remounts and re-fetches detail. Extract the load effect body into a `reload()` callback the effect and `onRescheduled` share.

- [ ] **Step 4: Run full suite + build + lint.** `cd event-organizer-frontend && npx vitest run && npm run build && npm run lint`.

- [ ] **Step 5: Commit.**

```bash
git add event-organizer-frontend/src/modules/bookings/BookingDetailPanel.tsx event-organizer-frontend/src/modules/bookings/BookingsPage.tsx event-organizer-frontend/src/modules/bookings/BookingDetailPanel.test.tsx event-organizer-frontend/src/index.css
git commit -m "feat(organizer-fe): Перенести button on a booking + list/detail refresh"
```

---

### Task 5: Docs

**Files:**
- Modify: `event-organizer/CLAUDE.md` (endpoints table: the two new `/api/me/bookings/{id}/slots` + `/reschedule` rows; scheduling_client bullet: `get_slots`/`reschedule_booking`, 409→ConflictError)
- Modify: `event-organizer-frontend/CLAUDE.md` (bookings screen: «Перенести» via RescheduleModal slot picker; note «Переназначить» is not yet built)

- [ ] Commit: `docs: organizer booking reschedule endpoints + UI`

---

## Notes for the executor

- All in the **root** `events` repo. Commit exact files; never `git add -A`.
- Create the throwaway BFF test DB once; drop it at the end.
- `_day_window_utc` uses `zoneinfo` (stdlib); DST is handled by `astimezone`.
- The slots endpoint does not exclude the booking's own time — acceptable (moving to a different slot).
