# Organizer Booking Reassign Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans. Steps use checkbox (`- [ ]`) syntax.

**Goal:** Reassign a booking to another host of the same event type — the missing producer for the already-wired `booking.reassigned` contract, plus a BFF proxy and a host-picker UI.

**Architecture:** event-scheduling gains a reassign domain write + `booking.reassigned` outbox emit + router; the BFF adds owner-scoped reassign-targets + reassign endpoints; the SPA adds a «Переназначить» button + modal. Downstream (event-receiver normalize, event-saver project, event-booking react) is unchanged.

**Tech Stack:** Python 3.14 FastAPI, pytest; React 19 + Vite + TS, vitest.

## Global Constraints

- No `else if`; avoid `else`. Russian UI copy. Ownership by construction in the BFF.
- BFF tests: `TEST_POSTGRES_DSN=postgresql+asyncpg://postgres:postgres@localhost:5432/event_organizer_test` (create DB first). event-scheduling tests need Postgres too (auto ephemeral or `TEST_POSTGRES_DSN`).
- The `booking.reassigned` contract/consumers already exist — only emit the producer side; do not touch event-schemas/event-receiver/event-saver/event-booking.
- Reassign actor headers: `actor-source: organizer`, `actor-user-id: <me.user_id>`.

---

### Task 1: event-scheduling — reassign domain (write + service)

**Files:**
- Modify: `event-scheduling/event_scheduling/booking/service.py`, `booking/write_adapter.py`, `booking/interfaces.py`
- Test: `event-scheduling/tests/test_booking_service.py` (or the existing booking-service test module)

- [ ] **Step 1: Failing tests** — add reassign cases (mirror existing reschedule tests' harness: fake read/write/busy/outbox). Assert: happy path updates host + writes outbox `booking.reassigned` with `previous_host_user_id`; cancelled → ConflictError; same host → ValidationError; non-host target → ValidationError; busy new host → ConflictError. Add a `_free_host` test that a soon-start slot passes with `check_notice=False`.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement.**
  - `booking/interfaces.py` `IBookingWriteAdapter`: add `async def update_host(self, booking_id: UUID, new_host_user_id: UUID) -> BookingDTO: ...`.
  - `write_adapter.py`: add `update_host` (SAVEPOINT + IntegrityError→ConflictError; UPDATE host_user_id, updated_at; **do not** reset reminder_sent_at) per the spec.
  - `service.py`: add `check_notice: bool = True` to `_free_host` (guard `start < now + notice` only when true); add `reassign(...)` per the spec (`from event_scheduling.errors import ... ValidationError` already imported).
  - `OutboxWriter.write` gets `previous_host_user_id: UUID | None = None` (see Task 2 — do it there; `service.reassign` calls `self._outbox.write("booking.reassigned", updated, previous_host_user_id=previous_host)`).

- [ ] **Step 4: Run → pass. Lint.**

- [ ] **Step 5: Commit** `feat(scheduling): booking reassign domain (update_host + service)`.

---

### Task 2: event-scheduling — outbox emit for booking.reassigned

**Files:**
- Modify: `event-scheduling/event_scheduling/publishing/outbox_writer.py`, `publishing/payload.py`, `publishing/dispatcher.py`
- Test: `event-scheduling/tests/test_publishing.py` (payload/dispatcher tests)

- [ ] **Step 1: Failing tests** — `build_cloudevent("booking.reassigned", uid, ce, payload, host, client, now, previous_host=prev)` → body `users` has 3 entries with roles organizer/client/previous_organizer and `previous_organizer_email == prev.email`. Dispatcher: a payload with `previous_host_user_id` resolves all three ids via the fake users client and passes `previous_host` through.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** per the spec:
  - `outbox_writer.py`: `write(..., previous_host_user_id: UUID | None = None)`; add `payload["previous_host_user_id"] = str(previous_host_user_id)` when set.
  - `payload.py`: `_users(host, client, attendee_tz, previous_host=None)` appends the `previous_organizer` entry; `_reassigned_body(booking_uid, payload, users)` returns `{users, booking_uid, previous_organizer_email}`; register `"booking.reassigned"`; `build_cloudevent(..., previous_host=None)` → `_users(..., previous_host)`.
  - `dispatcher.py`: `_resolve_participants` adds `previous_host_user_id` to the `by_ids` batch when present and returns `(host, client, previous_or_None)`; `_dispatch_row` unpacks the 3-tuple and passes `previous_host` to `build_cloudevent`.

- [ ] **Step 4: Run → pass. Lint.**

- [ ] **Step 5: Commit** `feat(scheduling): emit booking.reassigned from the outbox`.

---

### Task 3: event-scheduling — reassign router

**Files:**
- Modify: `event-scheduling/event_scheduling/routers/booking.py`, `schemas/booking.py`, `booking/interfaces.py` (`IBookingService.reassign`)
- Test: `event-scheduling/tests/test_booking_api.py`

- [ ] **Step 1: Failing test** — `POST /api/v1/bookings/{id}/reassign` `{new_host_user_id}` with actor headers returns 200 and the service saw the new host + actor.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — `schemas/booking.py`: `class ReassignRequest(BaseModel): new_host_user_id: UUID`. `interfaces.py`: `IBookingService.reassign(...)`. `routers/booking.py`: endpoint mirroring `reschedule_booking` (actor headers) calling `service.reassign(booking_id, body.new_host_user_id, _actor(...))`.

- [ ] **Step 4: Run → pass. Lint.**

- [ ] **Step 5: Commit** `feat(scheduling): POST /api/v1/bookings/{id}/reassign`.

---

### Task 4: BFF — scheduling client get_event_type + reassign_booking

**Files:**
- Modify: `event-organizer/event_organizer/adapters/interfaces.py`, `adapters/scheduling_client.py`
- Test: `event-organizer/tests/test_scheduling_client.py`

- [ ] **Step 1: Failing tests** — `get_event_type` GETs `/api/v1/event-types/{id}`; `reassign_booking` POSTs `/api/v1/bookings/{id}/reassign` `{new_host_user_id}` with `actor-source: organizer` / `actor-user-id` headers; a 422 → ValidationError.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement**:

```python
async def get_event_type(self, event_type_id: str) -> dict:
    async with self._http() as c:
        resp = await c.get(f"{self._base_url}/api/v1/event-types/{event_type_id}")
    return self._ok(resp)

async def reassign_booking(self, booking_id: str, new_host_user_id: str, actor_user_id: UUID) -> dict:
    headers = {"actor-source": "organizer", "actor-user-id": str(actor_user_id)}
    async with self._http() as c:
        resp = await c.post(
            f"{self._base_url}/api/v1/bookings/{booking_id}/reassign",
            json={"new_host_user_id": new_host_user_id},
            headers=headers,
        )
    return self._ok(resp)
```
(+ both on `ISchedulingClient`.)

- [ ] **Step 4: Run → pass. Lint. Commit** `feat(organizer): scheduling client get_event_type + reassign_booking`.

---

### Task 5: BFF — reassign-targets + reassign routes

**Files:**
- Modify: `event-organizer/event_organizer/routers/me.py`, `schemas/me.py`, `tests/test_me_api.py`

**Interfaces:** uses `IUsersClient.get_user(uid)` (existing) for host names.

- [ ] **Step 1: Extend the fakes + failing tests.** In `test_me_api.py`, extend `_FakeScheduling` with `get_event_type` (return `{"id": "et1", "hosts": [{"user_id": <me>, "schedule_id": "s"}, {"user_id": <other>, "schedule_id": "s"}]}` — include the caller's own id + one other) and `reassign_booking` (record args). `_FakeUsers.get_user` already returns a user. Tests:
  - `GET /api/me/bookings/b1/reassign-targets` → 200, lists the **other** host (not the caller), with name/email; `404` for an unknown id.
  - `POST /api/me/bookings/b1/reassign {new_host_user_id}` → 200, forwards new_host_user_id + actor = the token's uid; `404` for an unknown id.

  Note: the owned booking row's `host_user_id` must equal the token's uid for the "exclude self" filter — set `_FakeScheduling.get_bookings` row `host_user_id=str(host_user_id)` (already true) and make `get_event_type` return that same id plus another. Since the token uid varies per test, have `get_event_type` echo a stored "other" id and read the caller from the row; simplest: targets = event-type hosts minus `row["host_user_id"]`.

- [ ] **Step 2: Run → fail.**

- [ ] **Step 3: Implement** — `schemas/me.py`: `class ReassignTarget(BaseModel): user_id: str; name: str | None; email: str` and `class ReassignRequest(BaseModel): new_host_user_id: str`. `routers/me.py`:

```python
@me_router.get("/bookings/{booking_id}/reassign-targets", response_model=list[ReassignTarget])
async def reassign_targets(
    booking_id: str, scheduling: FromDishka[ISchedulingClient], users: FromDishka[IUsersClient], me: RequireOrganizer
) -> list[ReassignTarget]:
    row = await _owned_row(scheduling, me.user_id, booking_id)
    et = await scheduling.get_event_type(row["event_type_id"])
    current = row["host_user_id"]
    targets = []
    for h in et.get("hosts", []):
        if h["user_id"] == current:
            continue
        u = await users.get_user(UUID(h["user_id"]))
        targets.append(ReassignTarget(user_id=h["user_id"], name=u.get("name"), email=u["email"]))
    return targets


@me_router.post("/bookings/{booking_id}/reassign")
async def reassign_booking(
    booking_id: str, body: ReassignRequest, scheduling: FromDishka[ISchedulingClient], me: RequireOrganizer
) -> dict:
    await _owned_row(scheduling, me.user_id, booking_id)
    return await scheduling.reassign_booking(booking_id, body.new_host_user_id, me.user_id)
```
Add `IUsersClient` + `UUID` imports; add the two schemas to the import block.

- [ ] **Step 4: Run → pass. Lint. Commit** `feat(organizer): reassign-targets + reassign routes (owner-scoped)`.

---

### Task 6: Frontend — api/types + ReassignModal

**Files:**
- Modify: `event-organizer-frontend/src/modules/bookings/bookingsApi.ts`, `types.ts`, `index.css`
- Create: `event-organizer-frontend/src/modules/bookings/ReassignModal.tsx`, `ReassignModal.test.tsx`

- [ ] **Step 1: types + api.** `types.ts`: `export type ReassignTarget = { user_id: string; name: string | null; email: string }`. `bookingsApi.ts`: `getReassignTargets(id): Promise<ReassignTarget[]>` (GET `/api/me/bookings/${id}/reassign-targets`), `reassignBooking(id, newHostUserId): Promise<void>` (POST `/api/me/bookings/${id}/reassign` `{new_host_user_id}`).

- [ ] **Step 2: Failing modal test** (mirror RescheduleModal.test): loads targets → renders rows; a pick enables «Переназначить»; confirm calls `reassignBooking(id, picked)` + `onDone`; a rejected reassign shows `.error-text` and stays open; empty targets shows «Нет других хостов…».

- [ ] **Step 3: Implement `ReassignModal.tsx`** (structure like RescheduleModal): props `{ bookingId, onClose, onReassigned }`; on mount `getReassignTargets(bookingId)`; render each target as a `<button className="target-row is-selected?">{name ?? email} — {email}</button>`; footer «Отмена» + «Переназначить» (disabled until picked); confirm → `reassignBooking` → `onReassigned()` / error inline.

- [ ] **Step 4: CSS** — `.target-row` (full-width selectable row like `.slot-chip` but block): border, radius, padding, `.is-selected` accent.

- [ ] **Step 5: Run → pass. Commit** `feat(organizer-fe): ReassignModal (host picker) + api`.

---

### Task 7: Frontend — «Переназначить» button + shared refresh

**Files:**
- Modify: `event-organizer-frontend/src/modules/bookings/BookingDetailPanel.tsx`, `BookingsPage.tsx`, `BookingDetailPanel.test.tsx`

- [ ] **Step 1:** Rename the panel's `onRescheduled` prop to `onChanged` (covers reschedule + reassign); update `BookingsPage` (`onChanged={() => setRefreshKey(k => k+1)}`) and the RescheduleModal wiring (`onRescheduled={() => { setRescheduling(false); onChanged?.() }}`).

- [ ] **Step 2:** Add a `reassigning` state + a «Переназначить» button beside «Перенести» in `.detail-actions` (same `canReschedule` guard — reuse it, rename to `canModify`), opening `ReassignModal` with `onReassigned={() => { setReassigning(false); onChanged?.() }}`.

- [ ] **Step 3:** Panel test: «Переназначить» shows for a confirmed future booking, hidden for cancelled/past (extend the existing `rescheduleButton`-style helper).

- [ ] **Step 4: Run full suite + build + lint. Commit** `feat(organizer-fe): Переназначить button on a booking`.

---

### Task 8: Docs

- [ ] `event-scheduling/CLAUDE.md` (endpoints table: `POST /api/v1/bookings/{id}/reassign`; note the outbox now emits `booking.reassigned`), `event-organizer/CLAUDE.md` (the two `/reassign-targets` + `/reassign` endpoints; scheduling_client `get_event_type`/`reassign_booking`), `event-organizer-frontend/CLAUDE.md` (Переназначить via ReassignModal; remove the "not built" note). Commit `docs: organizer booking reassign`.

---

## Notes for the executor

- All in the **root** `events` repo. Commit exact files; never `git add -A`.
- Study each service's existing test harness before writing tests (fakes, fixtures) and mirror it — do not invent a new harness.
- The `booking.reassigned` end-to-end (event-saver projection + event-booking reaction) already has coverage; this plan only tests the new producer/BFF/UI.
