# event-booking reacts to event-scheduling bookings (срез 4a.2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `event-booking` create chat/Jitsi/notifications for `event-scheduling` bookings (absent from cal.com) by adding a composite `IBookingDatabaseAdapter` that falls back to a new enriched `GET /api/v1/bookings/{id}/detail` endpoint on `event-scheduling`.

**Architecture:** `event-scheduling` gains a detail endpoint that resolves participant names/locale from event-users. `event-booking` gains a `SchedulingBookingSource` (HTTP → that endpoint) and a `CompositeBookingDatabaseAdapter` (cal.com first → scheduling fallback) behind the EXISTING `IBookingDatabaseAdapter` Protocol; the controller skips the blacklist/reject sub-flow for `source="scheduling"`. Additive — cal.com path, reminders scheduler, event-receiver/event-saver untouched.

**Tech Stack:** Python 3.14, FastAPI/FastStream, Dishka, SQLAlchemy async, httpx, pytest — across TWO services (`event-scheduling`, `event-booking`).

**Spec:** `docs/superpowers/specs/2026-07-13-event-booking-scheduling-source-design.md`
**Read the internals map facts in the spec §0 before editing event-booking — every referenced file:line was verified.**

## Global Constraints

- **Python `>=3.14`**; deps via `uv`; each service has its own venv/tests. Ruff 120/py314; **NO `elif`; avoid `else`** (guard clauses / mapping dicts).
- Frozen-dataclass DTOs; Pydantic only in `schemas/`. Raw SQL via `SqlExecutor` `:param` (event-scheduling).
- **Additive:** do NOT modify cal.com path, `scheduler.py` (reminders = слайс 4a.3), event-receiver, event-saver, or the cal.com DB adapter's existing behavior.
- **Reminders are OUT of scope** (deferred to 4a.3). Do not touch `scheduler.py` beyond leaving it on the concrete cal.com adapter.
- event-scheduling `/detail` under existing `require_api_key` (`Authorization: Bearer <SCHEDULING_API_KEY>`). event-booking sends that Bearer.
- **Local initdb is BROKEN** — event-scheduling DB tests use Docker: `docker run -d --rm --name sched-testpg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=event_scheduling -p 5599:5432 postgres:16` + `TEST_POSTGRES_DSN=postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling`, stop after.
- Branch: `feat/booking-scheduling-source-impl`.

---

## File Structure

```
event-scheduling/
├── event_scheduling/publishing/dto.py           # ParticipantInfo += name, locale (modify)
├── event_scheduling/publishing/users_client.py  # _parse maps name/locale (modify)
├── event_scheduling/booking/dto.py              # BookingDetailDTO, ParticipantDetail (modify/add)
├── event_scheduling/booking/detail_service.py   # BookingDetailService (NEW)
├── event_scheduling/booking/interfaces.py       # IBookingDetailService (modify)
├── event_scheduling/booking/read_adapter.py     # + event_type title read (modify)
├── event_scheduling/routers/booking.py          # + GET /{id}/detail (modify)
├── event_scheduling/schemas/booking.py          # BookingDetailResponse (modify)
├── event_scheduling/ioc.py                      # provide BookingDetailService (modify)
event-booking/
├── event_booking/dtos.py                        # BookingDTO += source (modify)
├── event_booking/adapters/scheduling_source.py  # SchedulingBookingSource (NEW)
├── event_booking/adapters/composite_db.py       # CompositeBookingDatabaseAdapter (NEW)
├── event_booking/interfaces/scheduling.py       # ISchedulingBookingSource (NEW)
├── event_booking/controllers/booking.py         # skip reject for source="scheduling" (modify)
├── event_booking/config.py                      # EVENT_SCHEDULING_URL, SCHEDULING_API_KEY (modify)
├── event_booking/ioc.py                          # bind IBookingDatabaseAdapter→Composite (modify)
├── event_booking/consumer.py                     # .get(IBookingDatabaseAdapter) (modify)
```

---

## Task 1: event-scheduling — UsersClient resolves name + locale

**Files:**
- Modify: `event-scheduling/event_scheduling/publishing/dto.py`, `event-scheduling/event_scheduling/publishing/users_client.py`
- Test: `event-scheduling/tests/test_publishing_clients.py` (extend)

**Interfaces:**
- Produces: `ParticipantInfo(email: str, time_zone: str | None, name: str | None, locale: str | None)`; `UsersClient.by_ids` populates all four.

- [ ] **Step 1: Verify the real event-users `/api/users/by-ids` response fields.** Read `event-users` source (`event-users/event_users/schemas/users.py` `GetUsersByIdsResponse` / the item model) to confirm each item carries `name` and `locale` (alongside `id`/`email`/`time_zone`). If a field is absent from the response, extend `event-users`'s by-ids item schema + query to include it (that is a legitimate part of this task — event-scheduling's detail endpoint needs names). Record the actual field names in the report.

- [ ] **Step 2: Failing test (extend `tests/test_publishing_clients.py`)** — assert `by_ids` now maps name+locale:

```python
@pytest.mark.asyncio
async def test_users_by_ids_maps_name_and_locale() -> None:
    a = uuid4()

    def handler(req: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"items": [
            {"id": str(a), "email": "a@x.io", "time_zone": "Europe/Berlin", "name": "Alice", "locale": "en"}]})

    client = UsersClient("http://users:8888", "tok", transport=httpx.MockTransport(handler))
    out = await client.by_ids([a])
    assert out[a].name == "Alice"
    assert out[a].locale == "en"
    assert out[a].email == "a@x.io"
```
> Match the field names to what event-users actually returns (Step 1). If names differ (e.g. camelCase), adjust the handler + parser consistently.

- [ ] **Step 3: Run — FAIL.** `cd event-scheduling && uv run pytest tests/test_publishing_clients.py -k name_and_locale -v` (no DB).

- [ ] **Step 4: Extend `ParticipantInfo`** in `publishing/dto.py`:

```python
@dataclass(frozen=True)
class ParticipantInfo:
    email: str
    time_zone: str | None
    name: str | None = None
    locale: str | None = None
```
(Defaults keep existing `ParticipantInfo(email, tz)` call sites — the dispatcher's `build_cloudevent` — working unchanged.)

- [ ] **Step 5: Extend `UsersClient._parse`** in `publishing/users_client.py` to map name/locale (use the real field names from Step 1):

```python
    @staticmethod
    def _parse(data: dict) -> dict[UUID, ParticipantInfo]:
        return {
            UUID(r["id"]): ParticipantInfo(r["email"], r.get("time_zone"), r.get("name"), r.get("locale"))
            for r in data["items"]
        }
```

- [ ] **Step 6: Run — PASS.** `uv run pytest tests/test_publishing_clients.py -v`.

- [ ] **Step 7: Commit** (include any event-users change)
```bash
git add event-scheduling/event_scheduling/publishing event-scheduling/tests/test_publishing_clients.py
# + event-users files if the by-ids schema needed name/locale
git commit -m "feat(scheduling): UsersClient resolves participant name + locale"
```

---

## Task 2: event-scheduling — BookingDetailService + GET /{id}/detail

**Files:**
- Create: `event-scheduling/event_scheduling/booking/detail_service.py`
- Modify: `event-scheduling/event_scheduling/booking/dto.py`, `booking/interfaces.py`, `booking/read_adapter.py`, `routers/booking.py`, `schemas/booking.py`, `ioc.py`
- Test: `event-scheduling/tests/test_booking_api.py` (extend)

**Interfaces:**
- Consumes: `IBookingReadAdapter` (booking `get`, + a new `event_type_title(event_type_id)`), `IUsersClient.by_ids`, `ParticipantInfo`.
- Produces: DTO `ParticipantDetail(email, name, time_zone, locale)`, `BookingDetailDTO(uid, title, start_time, end_time, status, host, client)`; `IBookingDetailService.detail(booking_id) -> BookingDetailDTO | None`; route `GET /api/v1/bookings/{id}/detail`.

- [ ] **Step 1: Failing integration test (extend `tests/test_booking_api.py`)** — seed a booking (via the existing create flow) with a fake users resolution, GET /detail:

```python
def test_booking_detail_returns_enriched(client) -> None:
    # create a booking through the existing flow (reuse helpers), capture its id + host/client uuids
    et, owner = _seed_single_host_et(client)  # existing helper
    bid = client.post("/api/v1/bookings", headers={"actor-source": "api"}, json={
        "event_type_id": et, "client_user_id": str(uuid4()),
        "start_time": "2026-10-01T09:00:00Z", "attendee_time_zone": "Europe/Moscow"}).json()["id"]
    resp = client.get(f"/api/v1/bookings/{bid}/detail")
    assert resp.status_code == 200
    body = resp.json()
    assert body["uid"] == bid
    assert body["title"]  # event_type title present
    assert body["host"]["email"]      # resolved via users source
    assert body["client"]["time_zone"] == "Europe/Moscow"  # from attendee_time_zone
    assert client.get(f"/api/v1/bookings/{uuid4()}/detail").status_code == 404
```
> This test runs with the app's real `IUsersClient`. Because a live event-users isn't in the test harness, either (a) override the `IUsersClient` provider in the test app/container with a fake that returns `ParticipantInfo(email=f"{uid}@x", tz, name="N", locale="en")` for any id, or (b) assert only fields resolvable without the real service. Prefer (a): add a fake-users override to the test `app`/container so `host.email`/`client.email` are deterministic. Keep the fake consistent with `IUsersClient.by_ids` signature.

- [ ] **Step 2: Run — FAIL.** Scratch Postgres per Global Constraints.

- [ ] **Step 3: DTOs in `booking/dto.py`**

```python
@dataclass(frozen=True)
class ParticipantDetail:
    email: str
    name: str | None
    time_zone: str | None
    locale: str | None


@dataclass(frozen=True)
class BookingDetailDTO:
    uid: str
    title: str
    start_time: datetime
    end_time: datetime
    status: str
    host: ParticipantDetail
    client: ParticipantDetail
```

- [ ] **Step 4: `read_adapter` — event_type title read.** Add to `BookingReadAdapter` (+ `IBookingReadAdapter`) a method:

```python
    async def event_type_title(self, event_type_id: UUID) -> str | None:
        row = await self._sql.fetch_one("SELECT title FROM event_type WHERE id = :id", {"id": event_type_id})
        if row is None:
            return None
        return row["title"]
```

- [ ] **Step 5: `booking/detail_service.py`**

```python
from uuid import UUID

from event_scheduling.booking.dto import BookingDetailDTO, ParticipantDetail
from event_scheduling.booking.interfaces import IBookingReadAdapter
from event_scheduling.publishing.interfaces import IUsersClient


class BookingDetailService:
    def __init__(self, read: IBookingReadAdapter, users: IUsersClient) -> None:
        self._read = read
        self._users = users

    async def detail(self, booking_id: UUID) -> BookingDetailDTO | None:
        booking = await self._read.get(booking_id)
        if booking is None:
            return None
        title = await self._read.event_type_title(booking.event_type_id) or ""
        resolved = await self._users.by_ids([booking.host_user_id, booking.client_user_id])
        host = resolved.get(booking.host_user_id)
        client = resolved.get(booking.client_user_id)
        return BookingDetailDTO(
            uid=str(booking.id), title=title, start_time=booking.start_time, end_time=booking.end_time,
            status=booking.status,
            host=ParticipantDetail(
                email=host.email if host else "", name=host.name if host else None,
                time_zone=host.time_zone if host else None, locale=host.locale if host else None),
            client=ParticipantDetail(
                email=client.email if client else "", name=client.name if client else None,
                time_zone=booking.attendee_time_zone, locale=client.locale if client else None),
        )
```
> `IBookingDetailService` Protocol (`booking/interfaces.py`): `async def detail(self, booking_id: UUID) -> BookingDetailDTO | None: ...`.

- [ ] **Step 6: Schema + route.** `schemas/booking.py`: `ParticipantModel(email, name, time_zone, locale)`, `BookingDetailResponse(uid, title, start_time, end_time, status, host, client)` + `from_dto`. `routers/booking.py`:

```python
@booking_router.get("/{booking_id}/detail", response_model=BookingDetailResponse)
async def booking_detail(booking_id: UUID, service: FromDishka[IBookingDetailService]) -> BookingDetailResponse:
    detail = await service.detail(booking_id)
    if detail is None:
        raise NotFoundError(f"booking {booking_id} not found")
    return BookingDetailResponse.from_dto(detail)
```
Use a `@field_serializer` for `...Z` UTC on datetimes (mirror the existing `BookingResponse` serializer).

- [ ] **Step 7: DI (`ioc.py`).** REQUEST-scope `provide_booking_detail_service(read: IBookingReadAdapter, users: IUsersClient) -> IBookingDetailService: return BookingDetailService(read, users)`. (`IUsersClient` is already APP-scope from slice 4a.)

- [ ] **Step 8: Run — PASS.** Full `event-scheduling` suite green.

- [ ] **Step 9: Commit**
```bash
git add event-scheduling/event_scheduling event-scheduling/tests/test_booking_api.py
git commit -m "feat(scheduling): GET /bookings/{id}/detail — enriched booking for event-booking"
```

---

## Task 3: event-booking — BookingDTO.source field

**Files:**
- Modify: `event-booking/event_booking/dtos.py`
- Test: `event-booking/tests/` (a small dto test)

**Interfaces:**
- Produces: `BookingDTO.source: str = "calcom"` (default keeps every existing construction valid).

- [ ] **Step 1: Read `event_booking/dtos.py`** — note `BookingDTO`'s exact field order/defaults (map: `id, uid, title, status, start_time, end_time, created_at, metadata, event_type_slug, from_reschedule, user, client`).

- [ ] **Step 2: Failing test** (`event-booking/tests/test_dtos_source.py`): construct a `BookingDTO` the existing way and assert `.source == "calcom"`; construct with `source="scheduling"` and assert it round-trips. (Copy a valid construction from an existing test.)

- [ ] **Step 3: Run — FAIL.** `cd event-booking && uv run pytest tests/test_dtos_source.py -v`.

- [ ] **Step 4: Add the field.** In `BookingDTO`, add `source: str = "calcom"` as the LAST field (so all positional constructions in existing tests/adapters keep working; it defaults to calcom). If `BookingDTO` is a frozen dataclass with required fields before optionals, place `source` after the last defaulted field.

- [ ] **Step 5: Run — PASS + full suite.** `uv run pytest` (existing tests green — new field defaults to calcom everywhere).

- [ ] **Step 6: Commit**
```bash
git add event-booking/event_booking/dtos.py event-booking/tests/test_dtos_source.py
git commit -m "feat(booking): BookingDTO.source marker (default calcom)"
```

---

## Task 4: event-booking — SchedulingBookingSource

**Files:**
- Create: `event-booking/event_booking/adapters/scheduling_source.py`, `event-booking/event_booking/interfaces/scheduling.py`
- Modify: `event-booking/event_booking/config.py`
- Test: `event-booking/tests/test_scheduling_source.py`

**Interfaces:**
- Consumes: `BookingDTO`, `UserDTO`, `BookingClientDTO` (read their exact fields from `dtos.py`).
- Produces: `ISchedulingBookingSource.get(uid: str) -> BookingDTO | None`; `SchedulingBookingSource(base_url, api_key, *, transport=None)`.

- [ ] **Step 1: Read `event_booking/dtos.py`** for the exact `UserDTO`/`BookingClientDTO` constructors (map: `UserDTO(id, name, email, locked, time_zone, telegram_chat_id, locale)`; `BookingClientDTO(name, email, time_zone, locale)`). Read `config.py` for the settings pattern.

- [ ] **Step 2: Failing test `tests/test_scheduling_source.py`**

```python
import httpx
import pytest

from event_booking.adapters.scheduling_source import SchedulingBookingSource


@pytest.mark.asyncio
async def test_maps_detail_to_booking_dto() -> None:
    def handler(req: httpx.Request) -> httpx.Response:
        assert req.headers.get("authorization") == "Bearer KEY"
        assert req.url.path == "/api/v1/bookings/bk-1/detail"
        return httpx.Response(200, json={
            "uid": "bk-1", "title": "Intro", "start_time": "2026-10-01T09:00:00Z",
            "end_time": "2026-10-01T10:00:00Z", "status": "confirmed",
            "host": {"email": "h@x.io", "name": "Host", "time_zone": "Europe/Berlin", "locale": "en"},
            "client": {"email": "c@x.io", "name": None, "time_zone": "Europe/Moscow", "locale": "ru"}})

    src = SchedulingBookingSource("http://sched:8888", "KEY", transport=httpx.MockTransport(handler))
    b = await src.get("bk-1")
    assert b.source == "scheduling"
    assert b.uid == "bk-1"
    assert b.title == "Intro"
    assert b.user.email == "h@x.io"
    assert b.user.name == "Host"
    assert b.client.email == "c@x.io"
    assert b.client.name == "c@x.io"  # name falls back to email when null


@pytest.mark.asyncio
async def test_404_returns_none() -> None:
    src = SchedulingBookingSource("http://sched:8888", "KEY",
                                  transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    assert await src.get("nope") is None


@pytest.mark.asyncio
async def test_5xx_raises() -> None:
    src = SchedulingBookingSource("http://sched:8888", "KEY",
                                  transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(Exception):  # noqa: B017
        await src.get("x")
```

- [ ] **Step 3: Run — FAIL.** `cd event-booking && uv run pytest tests/test_scheduling_source.py -v`.

- [ ] **Step 4: `interfaces/scheduling.py`**

```python
from typing import Protocol

from event_booking.dtos import BookingDTO


class ISchedulingBookingSource(Protocol):
    async def get(self, uid: str) -> BookingDTO | None: ...
```

- [ ] **Step 5: `adapters/scheduling_source.py`** (adapt the `UserDTO`/`BookingClientDTO`/`BookingDTO` construction to the REAL fields you read in Step 1 — this is the canonical shape from the map):

```python
from datetime import datetime

import httpx

from event_booking.dtos import BookingClientDTO, BookingDTO, UserDTO


def _dt(v: str) -> datetime:
    return datetime.fromisoformat(v.replace("Z", "+00:00"))


class SchedulingBookingSource:
    def __init__(self, base_url: str, api_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport

    async def get(self, uid: str) -> BookingDTO | None:
        headers = {"authorization": f"Bearer {self._api_key}"}
        async with httpx.AsyncClient(transport=self._transport, timeout=10.0) as client:
            resp = await client.get(f"{self._base_url}/api/v1/bookings/{uid}/detail", headers=headers)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()
        return self._to_dto(resp.json())

    @staticmethod
    def _to_dto(d: dict) -> BookingDTO:
        host, client = d["host"], d["client"]
        return BookingDTO(
            id=0, uid=d["uid"], title=d["title"], status=d["status"],
            start_time=_dt(d["start_time"]), end_time=_dt(d["end_time"]), created_at=_dt(d["start_time"]),
            metadata=None, event_type_slug=None, from_reschedule=None,
            user=UserDTO(id=0, name=host.get("name") or host["email"], email=host["email"], locked=False,
                         time_zone=host.get("time_zone"), telegram_chat_id=None, locale=host.get("locale")),
            client=BookingClientDTO(name=client.get("name") or client["email"], email=client["email"],
                                    time_zone=client.get("time_zone"), locale=client.get("locale")),
            source="scheduling",
        )
```
> The keyword args above mirror the mapped DTO shapes; correct any field-name/order mismatch against the real `dtos.py` (Step 1). `id=0` is a sentinel never used on the scheduling path (constraints/reject are skipped in Task 6).

- [ ] **Step 6: Config.** In `config.py` add `event_scheduling_url: str` (dev default `"http://event-scheduling:8888"`) and `scheduling_api_key: str` (dev default matching event-scheduling's `SCHEDULING_API_KEY`). Match the existing pydantic-settings style.

- [ ] **Step 7: Run — PASS.** `uv run pytest tests/test_scheduling_source.py -v`.

- [ ] **Step 8: Commit**
```bash
git add event-booking/event_booking/adapters/scheduling_source.py event-booking/event_booking/interfaces/scheduling.py event-booking/event_booking/config.py event-booking/tests/test_scheduling_source.py
git commit -m "feat(booking): SchedulingBookingSource — fetch event-scheduling booking detail"
```

---

## Task 5: event-booking — CompositeBookingDatabaseAdapter

**Files:**
- Create: `event-booking/event_booking/adapters/composite_db.py`
- Test: `event-booking/tests/test_composite_db.py`

**Interfaces:**
- Consumes: `IBookingDatabaseAdapter` (read its full method list in `interfaces/db.py`: `get_booking`, `get_bookings`, `get_attendee_bookings_by_email`, `update_booking_video_url`, `mark_reminder_sent`, `reject_booking`, `update_attendee_email`), `ISchedulingBookingSource`.
- Produces: `CompositeBookingDatabaseAdapter(calcom: IBookingDatabaseAdapter, scheduling: ISchedulingBookingSource)` implementing `IBookingDatabaseAdapter`.

- [ ] **Step 1: Read `interfaces/db.py`** for the EXACT `IBookingDatabaseAdapter` method signatures (names, params, returns). Every method must be delegated.

- [ ] **Step 2: Failing test `tests/test_composite_db.py`**

```python
import pytest

from event_booking.adapters.composite_db import CompositeBookingDatabaseAdapter
# import BookingDTO + build a minimal one from an existing test helper


class _Calcom:
    def __init__(self, booking): self._b = booking; self.video_calls = []  # noqa: E702, ANN001
    async def get_booking(self, uid): return self._b  # noqa: ANN001, ANN201
    async def update_booking_video_url(self, uid, url): self.video_calls.append((uid, url))  # noqa: ANN001, ANN201
    # stub the other IBookingDatabaseAdapter methods to record/return defaults


class _Sched:
    def __init__(self, booking): self._b = booking  # noqa: ANN001, E702
    async def get(self, uid): return self._b  # noqa: ANN001, ANN201


@pytest.mark.asyncio
async def test_get_booking_prefers_calcom(sample_calcom_booking) -> None:
    comp = CompositeBookingDatabaseAdapter(_Calcom(sample_calcom_booking), _Sched(None))
    b = await comp.get_booking("x")
    assert b is sample_calcom_booking  # cal.com hit wins


@pytest.mark.asyncio
async def test_get_booking_falls_back_to_scheduling(sample_scheduling_booking) -> None:
    comp = CompositeBookingDatabaseAdapter(_Calcom(None), _Sched(sample_scheduling_booking))
    b = await comp.get_booking("x")
    assert b.source == "scheduling"


@pytest.mark.asyncio
async def test_get_booking_none_when_both_miss() -> None:
    comp = CompositeBookingDatabaseAdapter(_Calcom(None), _Sched(None))
    assert await comp.get_booking("x") is None


@pytest.mark.asyncio
async def test_writes_delegate_to_calcom(sample_calcom_booking) -> None:
    cal = _Calcom(sample_calcom_booking)
    comp = CompositeBookingDatabaseAdapter(cal, _Sched(None))
    await comp.update_booking_video_url("uid", "http://url")
    assert cal.video_calls == [("uid", "http://url")]
```
> Provide `sample_calcom_booking`/`sample_scheduling_booking` fixtures building real `BookingDTO`s (source calcom/scheduling). Stub ALL `IBookingDatabaseAdapter` methods on `_Calcom` so the composite's delegations resolve.

- [ ] **Step 3: Run — FAIL.**

- [ ] **Step 4: `adapters/composite_db.py`**

```python
from event_booking.dtos import BookingDTO
from event_booking.interfaces.db import IBookingDatabaseAdapter
from event_booking.interfaces.scheduling import ISchedulingBookingSource


class CompositeBookingDatabaseAdapter:
    def __init__(self, calcom: IBookingDatabaseAdapter, scheduling: ISchedulingBookingSource) -> None:
        self._calcom = calcom
        self._scheduling = scheduling

    async def get_booking(self, booking_uid: str) -> BookingDTO | None:
        booking = await self._calcom.get_booking(booking_uid)
        if booking is not None:
            return booking
        return await self._scheduling.get(booking_uid)

    # every other IBookingDatabaseAdapter method delegates verbatim to cal.com:
    async def get_bookings(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201 — signature per interfaces/db.py
        return await self._calcom.get_bookings(*args, **kwargs)

    async def get_attendee_bookings_by_email(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return await self._calcom.get_attendee_bookings_by_email(*args, **kwargs)

    async def update_booking_video_url(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return await self._calcom.update_booking_video_url(*args, **kwargs)

    async def mark_reminder_sent(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return await self._calcom.mark_reminder_sent(*args, **kwargs)

    async def reject_booking(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return await self._calcom.reject_booking(*args, **kwargs)

    async def update_attendee_email(self, *args, **kwargs):  # noqa: ANN002, ANN003, ANN201
        return await self._calcom.update_attendee_email(*args, **kwargs)
```
> Replace the `*args, **kwargs` delegations with the EXACT typed signatures from `interfaces/db.py` (Step 1) so the class satisfies the Protocol precisely and ruff/type-check pass — do not leave `*args` in the final code; write each method's real params. The write-method delegations to cal.com are naturally 0-row no-ops for scheduling uids (no cal.com row).

- [ ] **Step 5: Run — PASS.**

- [ ] **Step 6: Commit**
```bash
git add event-booking/event_booking/adapters/composite_db.py event-booking/tests/test_composite_db.py
git commit -m "feat(booking): CompositeBookingDatabaseAdapter (cal.com → scheduling fallback)"
```

---

## Task 6: event-booking — controller skip-reject + DI wiring

**Files:**
- Modify: `event-booking/event_booking/controllers/booking.py`, `event-booking/event_booking/ioc.py`, `event-booking/event_booking/consumer.py`
- Test: `event-booking/tests/test_booking_controller_scheduling.py`

**Interfaces:**
- Consumes: `CompositeBookingDatabaseAdapter`, `ISchedulingBookingSource`, `SchedulingBookingSource`, `BookingDTO.source`.
- Produces: `handle_created` skips blacklist/constraints/reject when `booking.source == "scheduling"`; DI binds `IBookingDatabaseAdapter` → composite.

- [ ] **Step 1: Read `controllers/booking.py::handle_created`** and identify the blacklist/constraints/reject sub-flow (map: blacklist check on `booking.client.email` ~:80, constraints, `_reject_booking` ~:328) versus the chat/Jitsi/notification flow.

- [ ] **Step 2: Failing test `tests/test_booking_controller_scheduling.py`** — a `handle_created` on a `source="scheduling"` booking does NOT call reject even if the client is blacklisted, but DOES create chat / meeting / notifications; a `source="calcom"` blacklisted booking still rejects. Use fakes for the db adapter (returns the scheduling/calcom booking), blacklist adapter (returns blacklisted=True), chat/meeting/publisher (record calls). Assert reject not called for scheduling; chat/meeting/notify called; reject still called for calcom.

- [ ] **Step 3: Run — FAIL.**

- [ ] **Step 4: Guard the reject sub-flow.** In `handle_created`, wrap the blacklist/constraints/reject block so it runs only for cal.com bookings:

```python
        booking = await self._db.get_booking(booking_uid)
        if booking is None:
            return
        if booking.source == "calcom":
            # existing blacklist / constraints / reject sub-flow, unchanged
            ...
            # (if rejected, return early as today)
        # chat + meeting + notifications run for all sources (unchanged)
        ...
```
> Keep the existing reject/blacklist logic verbatim inside the `if booking.source == "calcom":` guard. Do NOT duplicate the chat/meeting/notification code — it stays after the guard, shared by both sources. No `elif`/`else`.

- [ ] **Step 5: DI + consumer.** In `ioc.py`: keep the concrete `BookingDatabaseAdapter` provider (cal.com); add a `SchedulingBookingSource` provider (`ISchedulingBookingSource`, APP scope, from settings `event_scheduling_url`/`scheduling_api_key`); add a provider that binds `IBookingDatabaseAdapter` → `CompositeBookingDatabaseAdapter(calcom_adapter, scheduling_source)` (REQUEST scope — it wraps the request-scoped cal.com adapter). Update `BookingController`'s DI to receive `IBookingDatabaseAdapter` (it already depends on the Protocol). In `consumer.py`, change the direct `.get(BookingDatabaseAdapter)` (map: `consumer.py:106` — that's the user-email flow; the booking flow resolves `BookingController` at `:221-223`, which now gets the composite via the Protocol) — verify the booking path uses the composite; leave the user-email `.get` as-is. In `scheduler.py`, leave the concrete cal.com adapter (reminders = 4a.3).

- [ ] **Step 6: Run — PASS + full suite.** `cd event-booking && uv run pytest` (cal.com path unaffected; scheduling path skips reject).

- [ ] **Step 7: Commit**
```bash
git add event-booking/event_booking/controllers/booking.py event-booking/event_booking/ioc.py event-booking/event_booking/consumer.py event-booking/tests/test_booking_controller_scheduling.py
git commit -m "feat(booking): composite DB adapter DI + skip reject for scheduling bookings"
```

---

## Task 7: end-to-end + docs + compose + final

**Files:**
- Test: `event-booking/tests/` (an end-to-end handle_created → follow-ups test with a stub scheduling source + stub publisher)
- Modify: `event-booking/CLAUDE.md`, `event-scheduling/CLAUDE.md` (+ its API_CONTRACTS.md), `docs/architecture/MESSAGE_CONTRACTS.md`, `docker-compose.services.yml`

- [ ] **Step 1: End-to-end test (event-booking).** Drive `handle_created(uid)` where the composite's cal.com returns None and the scheduling source (stub returning a `BookingDTO(source="scheduling")`) returns the booking; assert the publisher recorded `chat.created`, `meeting.url_created` (per participant), and `notification.send_requested` follow-ups. Reuse existing controller test fakes for chat/meeting/publisher.

- [ ] **Step 2: Run — PASS.**

- [ ] **Step 3: docker-compose env.** In `docker-compose.services.yml`, add to `event-booking` env: `EVENT_SCHEDULING_URL: http://event-scheduling:8888`, `SCHEDULING_API_KEY: ${SCHEDULING_API_KEY:-<match event-scheduling>}` (reuse the SAME value `event-scheduling`'s `SCHEDULING_API_KEY` is set to, so the Bearer matches its `require_api_key`).

- [ ] **Step 4: Docs.** `event-booking/CLAUDE.md`: composite adapter + scheduling fallback + skip-reject-for-scheduling; note reminders still cal.com-only (4a.3). `event-scheduling/CLAUDE.md` + `docs/API_CONTRACTS.md`: the new `GET /api/v1/bookings/{id}/detail` (enriched, for event-booking). `docs/architecture/MESSAGE_CONTRACTS.md`: event-booking now serves chat/Jitsi/notifications for event-scheduling bookings via the detail pull; reminders deferred.

- [ ] **Step 5: Full test + lint (both services).** `cd event-scheduling && uv run pytest && ruff check . && ruff format --check .` (Docker Postgres) AND `cd event-booking && uv run pytest && ruff check . && ruff format --check .` — all green.

- [ ] **Step 6: Compose smoke (best-effort).** Bring up `postgres rabbitmq event-receiver event-users event-saver event-scheduling event-booking`; create a booking via event-scheduling `POST /api/v1/bookings`; after the outbox dispatch + consume, verify event-booking published a `chat.created`/`meeting.url_created` (check the mocks journal `http://localhost:8089/__admin/requests` or event-booking logs). If impractical, note downstream as unverified — pytest is the hard gate.

- [ ] **Step 7: Commit**
```bash
git add event-booking/CLAUDE.md event-scheduling/CLAUDE.md event-scheduling/docs/API_CONTRACTS.md docs/architecture/MESSAGE_CONTRACTS.md docker-compose.services.yml event-booking/tests
git commit -m "docs(booking): document event-scheduling booking reactions (slice 4a.2) + compose env + e2e"
```

---

## Self-Review (проведён при написании плана)

**1. Покрытие спека:** §1 composite → Tasks 4/5/6; §2 detail endpoint → Tasks 1/2; §3 event-booking source/adapters/DI/controller → Tasks 3/4/5/6; §4 поток → Task 7 e2e; §5 ошибки (404→None, 5xx→raise) → Task 4 tests; §6 тесты — распределены; §7 напоминания OUT (scheduler.py не трогаем — Task 6 Step 5); §8 DoR → Task 7.

**2. Плейсхолдеры:** новые файлы кодированы полностью. Помечены verify-at-impl: event-users by-ids name/locale (Task 1 Step 1), точные `dtos.py`/`interfaces/db.py`/`booking.py` сигнатуры (Tasks 3/4/5/6 Step 1 — «read the real file»), fake-users override в detail-тесте (Task 2 Step 1). Composite `*args` delegations ЯВНО помечены «replace with real typed signatures» — не оставлять `*args` в финале. Это интеграция в существующий сервис: инструкции «прочитать файл и внести точечное изменение» — не заглушки.

**3. Согласованность типов:** `ParticipantInfo`(+name,locale) Task 1 → detail_service Task 2. `BookingDetailDTO`/`ParticipantDetail` Task 2 → detail response + event-booking JSON contract (Task 4 `_to_dto` reads uid/title/start/end/status/host/client). `BookingDTO.source` Task 3 → SchedulingBookingSource (Task 4) + composite (Task 5) + controller guard (Task 6). `ISchedulingBookingSource.get(uid)` Task 4 → composite Task 5 + DI Task 6. `CompositeBookingDatabaseAdapter` Task 5 → DI bind Task 6. Detail JSON field names in Task 2 (schema) MUST match Task 4's `_to_dto` reader — both use `{uid,title,start_time,end_time,status,host:{email,name,time_zone,locale},client:{...}}`.
