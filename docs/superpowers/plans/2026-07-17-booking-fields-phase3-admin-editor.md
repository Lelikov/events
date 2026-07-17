# Configurable Booking Fields — Phase 3 (Admin editor) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give admins a UI to configure per-event-type booking fields — `event-admin` proxies to `event-scheduling` (list event types, GET/PUT a type's booking-fields), and `event-admin-frontend` gets a "Поля записи" editor (add/remove/reorder fields; set type/label/placeholder/required/options; Save = PUT the ordered list).

**Architecture:** `event-admin` has no `event-scheduling` client today. Add one (`SchedulingClient`) exactly mirroring the existing `NotifierClient` proxy (shared DI-injected `httpx.AsyncClient`, `Authorization: Bearer <SCHEDULING_API_KEY>`, `raise_for_status()`), expose 3 authenticated admin routes under `/api/scheduling/*`, and map upstream errors through the existing structured-error contract. The frontend reaches only `event-admin` via `apiRequest`; a new `bookingFields` module holds the API client, pure editor helpers, a `FieldRow` component, and the `BookingFieldsPage` container, wired into the manual router + sidebar.

**Tech Stack:** Python 3.14, FastAPI, Dishka, httpx, pytest (event-admin) · React 19, Vite, TypeScript, `events-design-system`, vitest + happy-dom (event-admin-frontend).

## Global Constraints

- **Backend code style:** No `elif`; avoid `else` — early returns / guard clauses / mapping dicts. Ruff line length 120.
- **event-scheduling is the sole authoritative validator.** The `event-admin` proxy forwards verbatim and never re-validates booking-field payloads; it only maps upstream HTTP errors.
- **Structured error contract (event-admin):** every proxy error is `http_error(upstream_status, "scheduling_service_error", message)` → `HTTPException(status_code=<upstream>, detail={"code","message"})`, preserving the upstream status code (so `404`/`422` reach the frontend distinctly).
- **Admin auth:** every new route sits on a router with `route_class=DishkaRoute, dependencies=[Depends(require_admin)]` (admin JWT + role check → `401` no token, `403` non-admin).
- **event-scheduling endpoints (upstream, all gated by `require_api_key`, `Authorization: Bearer <key>`):**
  - `GET /api/v1/event-types` → `{"items": [EventTypeResponse, …]}` (each has `id, slug, title, …`).
  - `GET /api/v1/event-types/{id}/booking-fields` → `{"items": [BookingFieldModel, …]}`.
  - `PUT /api/v1/event-types/{id}/booking-fields`, body `{"items": [UpsertBookingFieldModel, …]}` → `{"items": [BookingFieldModel, …]}`.
  - `BookingFieldModel` = `{field_key, field_type, label, placeholder: str|null, required: bool, options: [{value,label}], position: int}`.
  - `UpsertBookingFieldModel` (PUT body item) = `{field_type, label, placeholder?, required?, options?}` — **no `field_key`, no `position`** (server slugifies the key and assigns position).
  - Field types: `text | textarea | select | radio | checkbox | boolean`; `select`/`radio`/`checkbox` are option types (carry `options`), the others must omit them.
- **Frontend conventions:** no router library (manual `parseRoute`/`navigateTo`); all backend calls go through `apiRequest` to `event-admin` only; Russian UI copy; consume `events-design-system` (`Icon`, `Switch`) + its CSS classes (`.card`, `.field`, `.inline-actions`, `.icon-button`, `.error-text`, `.page-header`, `.breadcrumb`, `.stack`, `secondary`/`small` buttons). **There is NO `.field-error` class — use `.error-text`.** `<Switch>` `onChange` receives the new **boolean**, not an event. Valid `IconName`s used here: `plus`, `trash`, `chevron-left`, `edit`. No `else` / avoid `else` here too.
- **Tests:** backend via the in-process `FakeProvider` swap (no respx/MockTransport for routes; adapter unit test may use `httpx.MockTransport`); frontend via `createRoot` + `act` (no testing-library), mocking the API module with `vi.mock`.

---

## File Structure

**event-admin (backend):**
- Create `event_admin/interfaces/scheduling.py` — `ISchedulingClient` Protocol.
- Create `event_admin/adapters/scheduling_client.py` — `SchedulingClient` (mirrors `NotifierClient`).
- Modify `event_admin/config.py` — add `event_scheduling_url` + `scheduling_api_key`.
- Modify `event_admin/ioc.py` — `provide_scheduling_client` provider + imports.
- Modify `event_admin/routes.py` — `_scheduling_proxy_error` + `scheduling_router` (3 handlers) + `root_router.include_router(scheduling_router)`.
- Modify `.env.example`, `CLAUDE.md`, and root `docker-compose.yml` (event-admin service env).
- Modify `tests/conftest.py` — `FakeSchedulingClient` + `Fakes`/`FakeProvider`/`make_settings` wiring.
- Create `tests/test_scheduling_proxy.py`.

**event-admin-frontend:**
- Create `src/modules/bookingFields/bookingFieldsApi.ts` — types + 3 API fns.
- Create `src/modules/bookingFields/fields.ts` — `EditorField`, constants, pure helpers (`isOptionType`, `toEditorField`, `newEditorField`, then in Task 5 `validateFields`, `buildUpsertItems`).
- Create `src/modules/bookingFields/FieldRow.tsx` — single-field editor.
- Create `src/modules/bookingFields/BookingFieldsPage.tsx` — container.
- Modify `src/modules/shared/routing.ts`, `src/App.tsx`, `src/modules/app/AdminLayout.tsx` — routing + nav.
- Modify `src/app.css` — a few editor layout classes.
- Create test files alongside each module.

---

## Task 1: event-admin → event-scheduling proxy

**Files:**
- Create: `event_admin/interfaces/scheduling.py`, `event_admin/adapters/scheduling_client.py`, `tests/test_scheduling_proxy.py`
- Modify: `event_admin/config.py`, `event_admin/ioc.py`, `event_admin/routes.py`, `tests/conftest.py`, `.env.example`, `CLAUDE.md`, root `../docker-compose.yml`
- Test: `tests/test_scheduling_proxy.py` (+ adapter unit test in the same file)

**Interfaces:**
- Consumes: event-scheduling's `GET /api/v1/event-types`, `GET`/`PUT /api/v1/event-types/{id}/booking-fields` (see Global Constraints).
- Produces: `ISchedulingClient` with `list_event_types() -> dict`, `get_booking_fields(event_type_id: str) -> dict`, `replace_booking_fields(event_type_id: str, body: dict) -> dict`; three admin routes `GET /api/scheduling/event-types`, `GET`/`PUT /api/scheduling/event-types/{id}/booking-fields` that pass upstream JSON through and map errors to `scheduling_service_error`.

- [ ] **Step 1: Write the failing proxy tests**

Create `tests/test_scheduling_proxy.py` (mirrors `tests/test_notifications_proxy.py`; uses the existing `client`, `admin_headers`, `user_headers`, `fakes` fixtures):

```python
import httpx
import pytest


def _make_upstream_error(status_code: int) -> httpx.HTTPStatusError:
    request = httpx.Request("GET", "http://scheduling.test/api/v1/event-types")
    response = httpx.Response(status_code, request=request)
    return httpx.HTTPStatusError("upstream error", request=request, response=response)


async def test_list_event_types_without_token_returns_401(client) -> None:
    response = await client.get("/api/scheduling/event-types")
    assert response.status_code == 401


async def test_user_role_cannot_access_scheduling(client, user_headers) -> None:
    response = await client.get("/api/scheduling/event-types", headers=user_headers)
    assert response.status_code == 403


async def test_list_event_types_returns_upstream_response(client, admin_headers, fakes) -> None:
    fakes.scheduling_client.event_types_response = {"items": [{"id": "e1", "slug": "s", "title": "T"}]}
    response = await client.get("/api/scheduling/event-types", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == {"items": [{"id": "e1", "slug": "s", "title": "T"}]}


async def test_get_booking_fields_returns_upstream_response(client, admin_headers, fakes) -> None:
    fakes.scheduling_client.booking_fields_response = {"items": []}
    response = await client.get("/api/scheduling/event-types/e1/booking-fields", headers=admin_headers)
    assert response.status_code == 200
    assert response.json() == {"items": []}


async def test_put_booking_fields_forwards_body_and_returns_upstream(client, admin_headers, fakes) -> None:
    fakes.scheduling_client.booking_fields_response = {"items": [{"field_key": "reason"}]}
    body = {"items": [{"field_type": "text", "label": "Reason"}]}
    response = await client.put(
        "/api/scheduling/event-types/e1/booking-fields", headers=admin_headers, json=body
    )
    assert response.status_code == 200
    assert fakes.scheduling_client.last_put == ("e1", body)
    assert response.json() == {"items": [{"field_key": "reason"}]}


@pytest.mark.parametrize("status_code", [400, 404, 422, 500, 503])
async def test_upstream_error_is_mapped(client, admin_headers, fakes, status_code: int) -> None:
    fakes.scheduling_client.error = _make_upstream_error(status_code)
    response = await client.get("/api/scheduling/event-types", headers=admin_headers)
    assert response.status_code == status_code
    detail = response.json()["detail"]
    assert detail["code"] == "scheduling_service_error"


async def test_scheduling_client_sends_bearer_and_parses_json() -> None:
    # Adapter unit test with an in-memory transport (independent of DI).
    from event_admin.adapters.scheduling_client import SchedulingClient

    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization", "")
        seen["url"] = str(request.url)
        return httpx.Response(200, json={"items": [{"id": "e1"}]})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(base_url="http://scheduling.test", transport=transport) as http_client:
        client = SchedulingClient(http_client=http_client, api_key="secret-key")
        result = await client.list_event_types()

    assert result == {"items": [{"id": "e1"}]}
    assert seen["auth"] == "Bearer secret-key"
    assert seen["url"] == "http://scheduling.test/api/v1/event-types"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin && uv run pytest tests/test_scheduling_proxy.py -v`
Expected: FAIL — collection/attribute errors (`ISchedulingClient`, `SchedulingClient`, `fakes.scheduling_client`, and the routes don't exist yet).

- [ ] **Step 3: Add config vars**

In `event_admin/config.py`, next to `notifier_service_url` / `notifier_admin_token`, add:

```python
    event_scheduling_url: AnyHttpUrl = Field(strict=True)
    scheduling_api_key: str = Field(strict=True)
```

Do **not** add `scheduling_api_key` to the `validate_secret_strength` `secrets` dict (consistent with `notifier_admin_token` / `shortener_api_key`, which are also excluded).

- [ ] **Step 4: Create the interface**

Create `event_admin/interfaces/scheduling.py`:

```python
from typing import Any, Protocol


class ISchedulingClient(Protocol):
    async def list_event_types(self) -> dict[str, Any]: ...

    async def get_booking_fields(self, event_type_id: str) -> dict[str, Any]: ...

    async def replace_booking_fields(self, event_type_id: str, body: dict[str, Any]) -> dict[str, Any]: ...
```

- [ ] **Step 5: Create the adapter**

Create `event_admin/adapters/scheduling_client.py` (mirror `adapters/notifier_client.py`):

```python
"""HTTP client for the event-scheduling API (admin proxy)."""

from typing import Any

import structlog
from httpx import AsyncClient


logger = structlog.get_logger(__name__)


class SchedulingClient:
    def __init__(self, *, http_client: AsyncClient, api_key: str) -> None:
        self._client = http_client
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def list_event_types(self) -> dict[str, Any]:
        response = await self._client.get("/api/v1/event-types", headers=self._headers)
        response.raise_for_status()
        return response.json()

    async def get_booking_fields(self, event_type_id: str) -> dict[str, Any]:
        response = await self._client.get(
            f"/api/v1/event-types/{event_type_id}/booking-fields", headers=self._headers
        )
        response.raise_for_status()
        return response.json()

    async def replace_booking_fields(self, event_type_id: str, body: dict[str, Any]) -> dict[str, Any]:
        response = await self._client.put(
            f"/api/v1/event-types/{event_type_id}/booking-fields", json=body, headers=self._headers
        )
        response.raise_for_status()
        return response.json()
```

- [ ] **Step 6: Register the DI provider**

In `event_admin/ioc.py`, add imports near the existing adapter/interface imports:

```python
from event_admin.adapters.scheduling_client import SchedulingClient
from event_admin.interfaces.scheduling import ISchedulingClient
```

And add a provider next to `provide_notifier_client` (APP scope, its own `AsyncClient`):

```python
    @provide(scope=Scope.APP)
    async def provide_scheduling_client(self, settings: Settings) -> AsyncGenerator[ISchedulingClient]:
        async with AsyncClient(base_url=str(settings.event_scheduling_url)) as http_client:
            yield SchedulingClient(
                http_client=http_client,
                api_key=settings.scheduling_api_key,
            )
```

- [ ] **Step 7: Add the routes**

In `event_admin/routes.py`, next to `_notifier_proxy_error` add:

```python
def _scheduling_proxy_error(exc: httpx.HTTPStatusError) -> HTTPException:
    """Map an upstream event-scheduling error to a structured response, preserving the status code."""
    upstream_status = exc.response.status_code
    message = f"Scheduling service returned an error (status {upstream_status})"
    return http_error(upstream_status, "scheduling_service_error", message)
```

Import the interface at the top alongside the other interface imports:

```python
from event_admin.interfaces.scheduling import ISchedulingClient
```

Near the `notifications_router` block add:

```python
scheduling_router = APIRouter(
    prefix="/api/scheduling",
    route_class=DishkaRoute,
    dependencies=[Depends(require_admin)],
)


@scheduling_router.get("/event-types")
async def proxy_list_event_types(client: FromDishka[ISchedulingClient]) -> dict:
    try:
        return await client.list_event_types()
    except httpx.HTTPStatusError as exc:
        raise _scheduling_proxy_error(exc) from exc


@scheduling_router.get("/event-types/{event_type_id}/booking-fields")
async def proxy_get_booking_fields(event_type_id: str, client: FromDishka[ISchedulingClient]) -> dict:
    try:
        return await client.get_booking_fields(event_type_id)
    except httpx.HTTPStatusError as exc:
        raise _scheduling_proxy_error(exc) from exc


@scheduling_router.put("/event-types/{event_type_id}/booking-fields")
async def proxy_put_booking_fields(
    event_type_id: str, body: dict, client: FromDishka[ISchedulingClient]
) -> dict:
    try:
        return await client.replace_booking_fields(event_type_id, body)
    except httpx.HTTPStatusError as exc:
        raise _scheduling_proxy_error(exc) from exc
```

At the bottom of `routes.py`, next to `root_router.include_router(notifications_router)`:

```python
root_router.include_router(scheduling_router)
```

- [ ] **Step 8: Wire the test fakes**

In `tests/conftest.py`, add a fake mirroring `FakeNotifierClient`:

```python
class FakeSchedulingClient:
    def __init__(self) -> None:
        self.event_types_response: dict = {"items": []}
        self.booking_fields_response: dict = {"items": []}
        self.error: httpx.HTTPStatusError | None = None
        self.last_put: tuple[str, dict] | None = None

    async def list_event_types(self) -> dict:
        if self.error is not None:
            raise self.error
        return self.event_types_response

    async def get_booking_fields(self, event_type_id: str) -> dict:
        if self.error is not None:
            raise self.error
        return self.booking_fields_response

    async def replace_booking_fields(self, event_type_id: str, body: dict) -> dict:
        if self.error is not None:
            raise self.error
        self.last_put = (event_type_id, body)
        return self.booking_fields_response
```

Add `self.scheduling_client = FakeSchedulingClient()` in `Fakes.__init__`. In `FakeProvider`, add:

```python
    @provide(scope=Scope.APP)
    def provide_scheduling_client(self) -> ISchedulingClient:
        return self._fakes.scheduling_client
```

(import `ISchedulingClient` in conftest). In `make_settings` defaults add:

```python
        "event_scheduling_url": "http://scheduling.test",
        "scheduling_api_key": "scheduling-key-0123456789abcdef",
```

- [ ] **Step 9: Run tests to verify they pass**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin && uv run pytest tests/test_scheduling_proxy.py -v`
Expected: PASS (all 8+ tests).

- [ ] **Step 10: Lint**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin && ruff check --fix . && ruff format .`
Expected: clean.

- [ ] **Step 11: Update env/docs/compose**

- `.env.example`: append (mirror the notifier block)
  ```
  # event-scheduling API proxy (must match event-scheduling SCHEDULING_API_KEY)
  EVENT_SCHEDULING_URL=http://127.0.0.1:8004
  SCHEDULING_API_KEY=
  ```
- `CLAUDE.md`: add `EVENT_SCHEDULING_URL` + `SCHEDULING_API_KEY` to the required-env-vars list.
- Root `../docker-compose.yml`: in the **event-admin** service's `environment:`, add `EVENT_SCHEDULING_URL` and `SCHEDULING_API_KEY`. **Read the event-booker (or event-organizer) service block first and copy the exact same `SCHEDULING_API_KEY` value and the internal scheduling URL (`http://event-scheduling:8004`) they already use** — do not invent a new key.

- [ ] **Step 12: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-admin
git add -A && git commit -m "feat(admin): event-scheduling proxy for booking-fields (list types, GET/PUT fields)"
```

---

## Task 2: Frontend API client (`bookingFieldsApi.ts`)

**Files:**
- Create: `event-admin-frontend/src/modules/bookingFields/bookingFieldsApi.ts`
- Test: `event-admin-frontend/src/modules/bookingFields/bookingFieldsApi.test.ts`

**Interfaces:**
- Consumes: `apiRequest` from `../shared/api.ts`; the 3 `event-admin` routes from Task 1.
- Produces: types `FieldType`, `FieldOption`, `BookingField`, `EventTypeSummary`, `UpsertBookingField`; fns `listEventTypes(): Promise<EventTypeSummary[]>`, `getBookingFields(id): Promise<BookingField[]>`, `putBookingFields(id, items): Promise<BookingField[]>`.

- [ ] **Step 1: Write the failing test**

Create `src/modules/bookingFields/bookingFieldsApi.test.ts`:

```ts
import { beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('../shared/api.ts', () => ({ apiRequest: vi.fn() }))
import { apiRequest } from '../shared/api.ts'
import { getBookingFields, listEventTypes, putBookingFields } from './bookingFieldsApi.ts'

describe('bookingFieldsApi', () => {
  beforeEach(() => vi.clearAllMocks())

  it('lists event types and unwraps items', async () => {
    vi.mocked(apiRequest).mockResolvedValue({ items: [{ id: 'e1', slug: 's', title: 'T' }] })
    const res = await listEventTypes()
    expect(apiRequest).toHaveBeenCalledWith('/api/scheduling/event-types')
    expect(res).toEqual([{ id: 'e1', slug: 's', title: 'T' }])
  })

  it('gets booking fields, encoding the id', async () => {
    vi.mocked(apiRequest).mockResolvedValue({ items: [] })
    await getBookingFields('e 1')
    expect(apiRequest).toHaveBeenCalledWith('/api/scheduling/event-types/e%201/booking-fields')
  })

  it('puts booking fields wrapped in an items body', async () => {
    vi.mocked(apiRequest).mockResolvedValue({ items: [] })
    const items = [{ field_type: 'text' as const, label: 'Q' }]
    await putBookingFields('e1', items)
    expect(apiRequest).toHaveBeenCalledWith('/api/scheduling/event-types/e1/booking-fields', {
      method: 'PUT',
      body: { items },
    })
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend && npx vitest run src/modules/bookingFields/bookingFieldsApi.test.ts`
Expected: FAIL — module not found.

- [ ] **Step 3: Implement the client**

Create `src/modules/bookingFields/bookingFieldsApi.ts`:

```ts
import { apiRequest } from '../shared/api.ts'

export type FieldType = 'text' | 'textarea' | 'select' | 'radio' | 'checkbox' | 'boolean'

export interface FieldOption {
  value: string
  label: string
}

export interface BookingField {
  field_key: string
  field_type: FieldType
  label: string
  placeholder: string | null
  required: boolean
  options: FieldOption[]
  position: number
}

export interface EventTypeSummary {
  id: string
  slug: string
  title: string
}

export interface UpsertBookingField {
  field_type: FieldType
  label: string
  placeholder?: string | null
  required?: boolean
  options?: FieldOption[]
}

export async function listEventTypes(): Promise<EventTypeSummary[]> {
  const res = await apiRequest<{ items: EventTypeSummary[] }>('/api/scheduling/event-types')
  return res.items
}

export async function getBookingFields(eventTypeId: string): Promise<BookingField[]> {
  const res = await apiRequest<{ items: BookingField[] }>(
    `/api/scheduling/event-types/${encodeURIComponent(eventTypeId)}/booking-fields`,
  )
  return res.items
}

export async function putBookingFields(
  eventTypeId: string,
  items: UpsertBookingField[],
): Promise<BookingField[]> {
  const res = await apiRequest<{ items: BookingField[] }>(
    `/api/scheduling/event-types/${encodeURIComponent(eventTypeId)}/booking-fields`,
    { method: 'PUT', body: { items } },
  )
  return res.items
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend && npx vitest run src/modules/bookingFields/bookingFieldsApi.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend
git add src/modules/bookingFields/bookingFieldsApi.ts src/modules/bookingFields/bookingFieldsApi.test.ts
git commit -m "feat(admin-fe): booking-fields API client"
```

---

## Task 3: Editor UI — routing, `fields.ts` helpers, `FieldRow`, `BookingFieldsPage` (local state, no save yet)

**Files:**
- Create: `src/modules/bookingFields/fields.ts`, `src/modules/bookingFields/FieldRow.tsx`, `src/modules/bookingFields/BookingFieldsPage.tsx`, `src/modules/bookingFields/BookingFieldsPage.test.tsx`
- Modify: `src/modules/shared/routing.ts`, `src/App.tsx`, `src/modules/app/AdminLayout.tsx`, `src/app.css`

**Interfaces:**
- Consumes: `bookingFieldsApi.ts` (Task 2); `events-design-system` `Icon`/`Switch`; `ApiError` from `../shared/api.ts`.
- Produces: `fields.ts` exports `EditorField`, `FIELD_TYPE_LABELS`, `isOptionType`, `toEditorField`, `newEditorField`; `FieldRow` component; `BookingFieldsPage` component; a `booking-fields` route (`/booking-fields`) reachable from the sidebar. **`validateFields`/`buildUpsertItems` and the Save button are added in Task 4** — this task ends with a fully editable-in-memory list that does not persist.

- [ ] **Step 1: Write the failing page test**

Create `src/modules/bookingFields/BookingFieldsPage.test.tsx`:

```tsx
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('./bookingFieldsApi.ts', () => ({
  listEventTypes: vi.fn(),
  getBookingFields: vi.fn(),
  putBookingFields: vi.fn(),
}))
import { getBookingFields, listEventTypes } from './bookingFieldsApi.ts'
import { BookingFieldsPage } from './BookingFieldsPage.tsx'

let container: HTMLDivElement
let root: Root

function mount() {
  root = createRoot(container)
  act(() => {
    root.render(<BookingFieldsPage />)
  })
}

async function flush() {
  await act(async () => {
    await Promise.resolve()
    await Promise.resolve()
  })
}

function setNativeValue(el: HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(Object.getPrototypeOf(el), 'value')?.set
  setter?.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
  el.dispatchEvent(new Event('change', { bubbles: true }))
}

function labelInputs(): HTMLInputElement[] {
  return Array.from(container.querySelectorAll<HTMLInputElement>('input[data-role="label"]'))
}

beforeEach(() => {
  container = document.createElement('div')
  document.body.appendChild(container)
  vi.mocked(listEventTypes).mockResolvedValue([{ id: 'e1', slug: 's', title: 'Тест' }])
  vi.mocked(getBookingFields).mockResolvedValue([
    { field_key: 'reason', field_type: 'text', label: 'Причина', placeholder: null, required: true, options: [], position: 0 },
  ])
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

async function selectFirstType() {
  const select = container.querySelector<HTMLSelectElement>('select[data-role="event-type"]')!
  await act(async () => {
    setNativeValue(select, 'e1')
    await Promise.resolve()
  })
  await flush()
}

describe('BookingFieldsPage', () => {
  it('lists event types after load', async () => {
    mount()
    await flush()
    const options = container.querySelectorAll('select[data-role="event-type"] option')
    expect(Array.from(options).some((o) => o.textContent === 'Тест')).toBe(true)
  })

  it('loads booking fields when a type is selected', async () => {
    mount()
    await flush()
    await selectFirstType()
    expect(vi.mocked(getBookingFields)).toHaveBeenCalledWith('e1')
    expect(labelInputs()[0].value).toBe('Причина')
  })

  it('adds and removes fields', async () => {
    mount()
    await flush()
    await selectFirstType()
    const addBtn = container.querySelector<HTMLButtonElement>('button[data-role="add-field"]')!
    await act(async () => {
      addBtn.click()
      await Promise.resolve()
    })
    expect(labelInputs()).toHaveLength(2)
    const removeBtn = container.querySelector<HTMLButtonElement>('button[aria-label="Удалить поле"]')!
    await act(async () => {
      removeBtn.click()
      await Promise.resolve()
    })
    expect(labelInputs()).toHaveLength(1)
  })

  it('moves a field down, swapping order', async () => {
    vi.mocked(getBookingFields).mockResolvedValue([
      { field_key: 'a', field_type: 'text', label: 'Первое', placeholder: null, required: false, options: [], position: 0 },
      { field_key: 'b', field_type: 'text', label: 'Второе', placeholder: null, required: false, options: [], position: 1 },
    ])
    mount()
    await flush()
    await selectFirstType()
    expect(labelInputs().map((i) => i.value)).toEqual(['Первое', 'Второе'])
    const down = container.querySelector<HTMLButtonElement>('button[aria-label="Переместить вниз"]')!
    await act(async () => {
      down.click()
      await Promise.resolve()
    })
    expect(labelInputs().map((i) => i.value)).toEqual(['Второе', 'Первое'])
  })

  it('shows the options editor when switching to an option type', async () => {
    mount()
    await flush()
    await selectFirstType()
    const typeSelect = container.querySelector<HTMLSelectElement>('select[data-role="field-type"]')!
    await act(async () => {
      setNativeValue(typeSelect, 'select')
      await Promise.resolve()
    })
    expect(container.querySelector('input[aria-label="Вариант 1"]')).not.toBeNull()
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend && npx vitest run src/modules/bookingFields/BookingFieldsPage.test.tsx`
Expected: FAIL — modules not found.

- [ ] **Step 3: Create `fields.ts` (editing helpers only)**

Create `src/modules/bookingFields/fields.ts`:

```ts
import type { BookingField, FieldType } from './bookingFieldsApi.ts'

export interface EditorField {
  uid: number
  fieldType: FieldType
  label: string
  placeholder: string
  required: boolean
  options: string[]
}

export const FIELD_TYPE_LABELS: Record<FieldType, string> = {
  text: 'Текст',
  textarea: 'Многострочный текст',
  select: 'Список (выбор одного)',
  radio: 'Переключатели (выбор одного)',
  checkbox: 'Флажки (выбор нескольких)',
  boolean: 'Согласие (да/нет)',
}

const OPTION_TYPES: FieldType[] = ['select', 'radio', 'checkbox']

export function isOptionType(fieldType: FieldType): boolean {
  return OPTION_TYPES.includes(fieldType)
}

export function toEditorField(field: BookingField, uid: number): EditorField {
  return {
    uid,
    fieldType: field.field_type,
    label: field.label,
    placeholder: field.placeholder ?? '',
    required: field.required,
    options: field.options.map((o) => o.value),
  }
}

export function newEditorField(uid: number): EditorField {
  return { uid, fieldType: 'text', label: '', placeholder: '', required: false, options: [] }
}
```

- [ ] **Step 4: Create `FieldRow.tsx`**

Create `src/modules/bookingFields/FieldRow.tsx`:

```tsx
import { Icon, Switch } from 'events-design-system'
import type { FieldType } from './bookingFieldsApi.ts'
import { FIELD_TYPE_LABELS, isOptionType, type EditorField } from './fields.ts'

interface FieldRowProps {
  field: EditorField
  index: number
  count: number
  onChange: (patch: Partial<EditorField>) => void
  onRemove: () => void
  onMove: (dir: -1 | 1) => void
}

export function FieldRow({ field, index, count, onChange, onRemove, onMove }: FieldRowProps) {
  const optionType = isOptionType(field.fieldType)

  function changeType(next: FieldType) {
    if (!isOptionType(next)) {
      onChange({ fieldType: next, options: [] })
      return
    }
    onChange({ fieldType: next, options: field.options.length > 0 ? field.options : [''] })
  }

  function updateOption(i: number, value: string) {
    onChange({ options: field.options.map((o, idx) => (idx === i ? value : o)) })
  }

  function addOption() {
    onChange({ options: [...field.options, ''] })
  }

  function removeOption(i: number) {
    onChange({ options: field.options.filter((_, idx) => idx !== i) })
  }

  return (
    <article className="card booking-field-row">
      <div className="inline-actions">
        <button
          type="button"
          className="icon-button"
          aria-label="Переместить вверх"
          disabled={index === 0}
          onClick={() => onMove(-1)}
        >
          <Icon name="chevron-left" style={{ transform: 'rotate(90deg)' }} />
        </button>
        <button
          type="button"
          className="icon-button"
          aria-label="Переместить вниз"
          disabled={index === count - 1}
          onClick={() => onMove(1)}
        >
          <Icon name="chevron-left" style={{ transform: 'rotate(-90deg)' }} />
        </button>
        <button type="button" className="icon-button" aria-label="Удалить поле" onClick={onRemove}>
          <Icon name="trash" />
        </button>
      </div>

      <label className="field">
        <span>Тип поля</span>
        <select
          data-role="field-type"
          value={field.fieldType}
          onChange={(e) => changeType(e.target.value as FieldType)}
        >
          {(Object.keys(FIELD_TYPE_LABELS) as FieldType[]).map((t) => (
            <option key={t} value={t}>
              {FIELD_TYPE_LABELS[t]}
            </option>
          ))}
        </select>
      </label>

      <label className="field">
        <span>Вопрос</span>
        <input
          type="text"
          data-role="label"
          value={field.label}
          maxLength={200}
          onChange={(e) => onChange({ label: e.target.value })}
        />
      </label>

      <label className="field">
        <span>Подсказка (необязательно)</span>
        <input
          type="text"
          value={field.placeholder}
          maxLength={500}
          onChange={(e) => onChange({ placeholder: e.target.value })}
        />
      </label>

      <Switch
        checked={field.required}
        showState
        label="Обязательное поле"
        onChange={(v) => onChange({ required: v })}
      />

      {optionType && (
        <div className="booking-field-options">
          <span className="field-options-title">Варианты ответа</span>
          {field.options.map((opt, i) => (
            <div key={i} className="inline-actions">
              <input
                type="text"
                aria-label={`Вариант ${i + 1}`}
                value={opt}
                maxLength={200}
                onChange={(e) => updateOption(i, e.target.value)}
              />
              <button
                type="button"
                className="icon-button"
                aria-label="Удалить вариант"
                disabled={field.options.length <= 1}
                onClick={() => removeOption(i)}
              >
                <Icon name="trash" />
              </button>
            </div>
          ))}
          <button type="button" className="secondary small" onClick={addOption}>
            <Icon name="plus" size={14} /> Добавить вариант
          </button>
        </div>
      )}
    </article>
  )
}
```

- [ ] **Step 5: Create `BookingFieldsPage.tsx` (no Save yet)**

Create `src/modules/bookingFields/BookingFieldsPage.tsx`:

```tsx
import { useEffect, useRef, useState } from 'react'
import { Icon } from 'events-design-system'
import { ApiError } from '../shared/api.ts'
import { getBookingFields, listEventTypes, type EventTypeSummary } from './bookingFieldsApi.ts'
import { newEditorField, toEditorField, type EditorField } from './fields.ts'
import { FieldRow } from './FieldRow.tsx'

export function BookingFieldsPage() {
  const [eventTypes, setEventTypes] = useState<EventTypeSummary[]>([])
  const [selectedId, setSelectedId] = useState('')
  const [fields, setFields] = useState<EditorField[]>([])
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState<string | null>(null)
  const [fieldsLoading, setFieldsLoading] = useState(false)
  const [fieldsError, setFieldsError] = useState<string | null>(null)
  const uidRef = useRef(0)

  function nextUid() {
    uidRef.current += 1
    return uidRef.current
  }

  useEffect(() => {
    async function loadTypes() {
      setLoading(true)
      setLoadError(null)
      try {
        setEventTypes(await listEventTypes())
      } catch (err) {
        setLoadError(err instanceof ApiError ? err.message : 'Не удалось загрузить типы встреч')
      } finally {
        setLoading(false)
      }
    }
    void loadTypes()
  }, [])

  async function selectEventType(id: string) {
    setSelectedId(id)
    setFields([])
    setFieldsError(null)
    if (id === '') {
      return
    }
    setFieldsLoading(true)
    try {
      const loaded = await getBookingFields(id)
      setFields(loaded.map((f) => toEditorField(f, nextUid())))
    } catch (err) {
      setFieldsError(err instanceof ApiError ? err.message : 'Не удалось загрузить поля')
    } finally {
      setFieldsLoading(false)
    }
  }

  function addField() {
    setFields((prev) => [...prev, newEditorField(nextUid())])
  }

  function removeField(uid: number) {
    setFields((prev) => prev.filter((f) => f.uid !== uid))
  }

  function moveField(uid: number, dir: -1 | 1) {
    setFields((prev) => {
      const index = prev.findIndex((f) => f.uid === uid)
      const target = index + dir
      if (index < 0 || target < 0 || target >= prev.length) {
        return prev
      }
      const next = [...prev]
      const [moved] = next.splice(index, 1)
      next.splice(target, 0, moved)
      return next
    })
  }

  function updateField(uid: number, patch: Partial<EditorField>) {
    setFields((prev) => prev.map((f) => (f.uid === uid ? { ...f, ...patch } : f)))
  }

  return (
    <section className="stack">
      <header className="page-header">
        <p className="breadcrumb">Настройки</p>
        <h1>Поля записи</h1>
      </header>

      {loading && (
        <article className="card">
          <p>Загрузка…</p>
        </article>
      )}
      {loadError && (
        <article className="card">
          <p className="error-text">{loadError}</p>
        </article>
      )}

      {!loading && !loadError && (
        <article className="card">
          <label className="field">
            <span>Тип встречи</span>
            <select
              data-role="event-type"
              value={selectedId}
              onChange={(e) => void selectEventType(e.target.value)}
            >
              <option value="">— выберите тип встречи —</option>
              {eventTypes.map((t) => (
                <option key={t.id} value={t.id}>
                  {t.title}
                </option>
              ))}
            </select>
          </label>
        </article>
      )}

      {fieldsError && <p className="error-text">{fieldsError}</p>}

      {selectedId !== '' && fieldsLoading && (
        <article className="card">
          <p>Загрузка полей…</p>
        </article>
      )}

      {selectedId !== '' && !fieldsLoading && (
        <>
          {fields.map((field, index) => (
            <FieldRow
              key={field.uid}
              field={field}
              index={index}
              count={fields.length}
              onChange={(patch) => updateField(field.uid, patch)}
              onRemove={() => removeField(field.uid)}
              onMove={(dir) => moveField(field.uid, dir)}
            />
          ))}
          <div className="inline-actions">
            <button type="button" data-role="add-field" className="secondary" onClick={addField}>
              <Icon name="plus" size={14} /> Добавить поле
            </button>
          </div>
        </>
      )}
    </section>
  )
}
```

- [ ] **Step 6: Wire routing + nav**

In `src/modules/shared/routing.ts`: add `| { name: 'booking-fields' }` to the `AppRoute` union, and in `parseRoute` before the `not-found` fallback:

```ts
  if (pathname === '/booking-fields') {
    return { name: 'booking-fields' }
  }
```

In `src/App.tsx`: `import { BookingFieldsPage } from './modules/bookingFields/BookingFieldsPage.tsx'` and add a render branch next to the others:

```tsx
{route.name === 'booking-fields' && <BookingFieldsPage />}
```

In `src/modules/app/AdminLayout.tsx`, add to `NAV_ITEMS` under the `'Настройки'` group:

```ts
  {
    label: 'Поля записи',
    path: '/booking-fields',
    icon: 'edit',
    group: 'Настройки',
    match: (pathname) => pathname === '/booking-fields',
  },
```

- [ ] **Step 7: Add editor CSS**

Append to `src/app.css`:

```css
.booking-field-row {
  display: grid;
  gap: 12px;
}

.booking-field-options {
  display: grid;
  gap: 8px;
}

.field-options-title {
  font-size: 12px;
  color: var(--muted);
}
```

- [ ] **Step 8: Run test to verify it passes**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend && npx vitest run src/modules/bookingFields/BookingFieldsPage.test.tsx`
Expected: PASS.

- [ ] **Step 9: Typecheck + lint**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend && npx tsc -b && npm run lint`
Expected: clean.

- [ ] **Step 10: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend
git add -A
git commit -m "feat(admin-fe): booking-fields editor UI + route (add/remove/reorder, local state)"
```

---

## Task 4: Validation + Save (PUT), success/error handling

**Files:**
- Modify: `src/modules/bookingFields/fields.ts`, `src/modules/bookingFields/BookingFieldsPage.tsx`
- Test: `src/modules/bookingFields/fields.test.ts` (new), `src/modules/bookingFields/BookingFieldsPage.test.tsx` (extend)

**Interfaces:**
- Consumes: `putBookingFields` from `bookingFieldsApi.ts`; `EditorField`/`isOptionType` from `fields.ts`.
- Produces: `fields.ts` gains `validateFields(fields: EditorField[]): string | null` and `buildUpsertItems(fields: EditorField[]): UpsertBookingField[]`; `BookingFieldsPage` gains a Save button that validates then PUTs, showing `saveOk`/`saveError` (including a surfaced upstream `422`).

- [ ] **Step 1: Write the failing pure-helper tests**

Create `src/modules/bookingFields/fields.test.ts`:

```ts
import { describe, expect, it } from 'vitest'
import type { EditorField } from './fields.ts'
import { buildUpsertItems, validateFields } from './fields.ts'

function mk(partial: Partial<EditorField>): EditorField {
  return { uid: 1, fieldType: 'text', label: 'Q', placeholder: '', required: false, options: [], ...partial }
}

describe('validateFields', () => {
  it('accepts an empty list', () => {
    expect(validateFields([])).toBeNull()
  })

  it('rejects a blank label', () => {
    expect(validateFields([mk({ label: '  ' })])).toMatch(/№1/)
  })

  it('rejects an option type with no non-empty options', () => {
    expect(validateFields([mk({ fieldType: 'select', options: ['', '  '] })])).toMatch(/вариант/i)
  })

  it('rejects duplicate option values', () => {
    expect(validateFields([mk({ fieldType: 'radio', options: ['a', 'a'] })])).toMatch(/повтор/i)
  })

  it('accepts a valid option field', () => {
    expect(validateFields([mk({ fieldType: 'checkbox', options: ['a', 'b'] })])).toBeNull()
  })
})

describe('buildUpsertItems', () => {
  it('maps a non-option field, trimming and nulling empty placeholder', () => {
    const items = buildUpsertItems([mk({ label: ' Reason ', placeholder: '  ', required: true })])
    expect(items).toEqual([{ field_type: 'text', label: 'Reason', placeholder: null, required: true }])
  })

  it('maps an option field to {value,label} pairs, dropping empties', () => {
    const items = buildUpsertItems([mk({ fieldType: 'select', label: 'Pick', options: [' a ', '', 'b'] })])
    expect(items).toEqual([
      {
        field_type: 'select',
        label: 'Pick',
        placeholder: null,
        required: false,
        options: [
          { value: 'a', label: 'a' },
          { value: 'b', label: 'b' },
        ],
      },
    ])
  })
})
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend && npx vitest run src/modules/bookingFields/fields.test.ts`
Expected: FAIL — `validateFields`/`buildUpsertItems` not exported.

- [ ] **Step 3: Add the helpers to `fields.ts`**

Append to `src/modules/bookingFields/fields.ts` (add the `UpsertBookingField` import to the existing type import line):

```ts
import type { BookingField, FieldType, UpsertBookingField } from './bookingFieldsApi.ts'
```

```ts
export function validateFields(fields: EditorField[]): string | null {
  for (let i = 0; i < fields.length; i += 1) {
    const field = fields[i]
    const pos = i + 1
    if (field.label.trim() === '') {
      return `Поле №${pos}: укажите вопрос`
    }
    if (!isOptionType(field.fieldType)) {
      continue
    }
    const values = field.options.map((o) => o.trim()).filter((o) => o !== '')
    if (values.length === 0) {
      return `Поле №${pos}: добавьте хотя бы один вариант`
    }
    if (new Set(values).size !== values.length) {
      return `Поле №${pos}: варианты не должны повторяться`
    }
  }
  return null
}

export function buildUpsertItems(fields: EditorField[]): UpsertBookingField[] {
  return fields.map((field) => {
    const placeholder = field.placeholder.trim() === '' ? null : field.placeholder.trim()
    const base = {
      field_type: field.fieldType,
      label: field.label.trim(),
      placeholder,
      required: field.required,
    }
    if (!isOptionType(field.fieldType)) {
      return base
    }
    const options = field.options
      .map((o) => o.trim())
      .filter((o) => o !== '')
      .map((o) => ({ value: o, label: o }))
    return { ...base, options }
  })
}
```

- [ ] **Step 4: Run pure tests to verify they pass**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend && npx vitest run src/modules/bookingFields/fields.test.ts`
Expected: PASS.

- [ ] **Step 5: Write the failing save tests (extend the page test)**

Append these cases to `src/modules/bookingFields/BookingFieldsPage.test.tsx` (add `putBookingFields` and `ApiError` imports; `putBookingFields` is already in the `vi.mock`):

```tsx
// add to the imports:
// import { getBookingFields, listEventTypes, putBookingFields } from './bookingFieldsApi.ts'
// import { ApiError } from '../shared/api.ts'

describe('BookingFieldsPage — save', () => {
  it('blocks save and shows an error when a field label is blank', async () => {
    vi.mocked(getBookingFields).mockResolvedValue([
      { field_key: 'a', field_type: 'text', label: '', placeholder: null, required: false, options: [], position: 0 },
    ])
    mount()
    await flush()
    await selectFirstType()
    const save = container.querySelector<HTMLButtonElement>('button[data-role="save"]')!
    await act(async () => {
      save.click()
      await Promise.resolve()
    })
    expect(vi.mocked(putBookingFields)).not.toHaveBeenCalled()
    expect(container.querySelector('.error-text')?.textContent).toMatch(/№1/)
  })

  it('PUTs the built payload and shows success', async () => {
    vi.mocked(getBookingFields).mockResolvedValue([
      { field_key: 'a', field_type: 'text', label: 'Причина', placeholder: null, required: true, options: [], position: 0 },
    ])
    vi.mocked(putBookingFields).mockResolvedValue([
      { field_key: 'prichina', field_type: 'text', label: 'Причина', placeholder: null, required: true, options: [], position: 0 },
    ])
    mount()
    await flush()
    await selectFirstType()
    const save = container.querySelector<HTMLButtonElement>('button[data-role="save"]')!
    await act(async () => {
      save.click()
      await Promise.resolve()
    })
    await flush()
    expect(vi.mocked(putBookingFields)).toHaveBeenCalledWith('e1', [
      { field_type: 'text', label: 'Причина', placeholder: null, required: true },
    ])
    expect(container.textContent).toContain('Сохранено')
  })

  it('surfaces an upstream 422 as an error', async () => {
    vi.mocked(getBookingFields).mockResolvedValue([
      { field_key: 'a', field_type: 'text', label: 'Причина', placeholder: null, required: false, options: [], position: 0 },
    ])
    vi.mocked(putBookingFields).mockRejectedValue(new ApiError('Некорректные поля', 422, null, 'scheduling_service_error'))
    mount()
    await flush()
    await selectFirstType()
    const save = container.querySelector<HTMLButtonElement>('button[data-role="save"]')!
    await act(async () => {
      save.click()
      await Promise.resolve()
    })
    await flush()
    expect(container.querySelector('.error-text')?.textContent).toContain('Некорректные поля')
  })
})
```

(Confirm the `ApiError` constructor signature in `src/modules/shared/api.ts` and match it — the message must be the first argument; adjust the extra args to the real shape if they differ.)

- [ ] **Step 6: Run to verify the save tests fail**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend && npx vitest run src/modules/bookingFields/BookingFieldsPage.test.tsx`
Expected: FAIL — no `data-role="save"` button / no save handler.

- [ ] **Step 7: Wire Save into the page**

In `src/modules/bookingFields/BookingFieldsPage.tsx`:
- extend imports: `import { putBookingFields, getBookingFields, listEventTypes, type EventTypeSummary } from './bookingFieldsApi.ts'` and `import { buildUpsertItems, newEditorField, toEditorField, validateFields, type EditorField } from './fields.ts'`.
- add state: `const [saving, setSaving] = useState(false)`, `const [saveOk, setSaveOk] = useState(false)`, `const [saveError, setSaveError] = useState<string | null>(null)`.
- reset `saveOk`/`saveError` on any edit: at the end of `addField`, `removeField`, `moveField`, `updateField`, and at the start of `selectEventType`, add `setSaveOk(false)` and `setSaveError(null)`.
- add the handler:

```tsx
  async function handleSave() {
    setSaveError(null)
    setSaveOk(false)
    const validationError = validateFields(fields)
    if (validationError !== null) {
      setSaveError(validationError)
      return
    }
    setSaving(true)
    try {
      const saved = await putBookingFields(selectedId, buildUpsertItems(fields))
      setFields(saved.map((f) => toEditorField(f, nextUid())))
      setSaveOk(true)
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : 'Не удалось сохранить поля')
    } finally {
      setSaving(false)
    }
  }
```

- replace the `inline-actions` block (the add-field row) with:

```tsx
          <div className="inline-actions">
            <button type="button" data-role="add-field" className="secondary" onClick={addField}>
              <Icon name="plus" size={14} /> Добавить поле
            </button>
            <button type="button" data-role="save" onClick={() => void handleSave()} disabled={saving}>
              {saving ? 'Сохранение…' : 'Сохранить'}
            </button>
          </div>
          {saveError && <p className="error-text">{saveError}</p>}
          {saveOk && <p style={{ color: 'var(--success)' }}>Сохранено</p>}
```

- [ ] **Step 8: Run the full module test suite to verify it passes**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend && npx vitest run src/modules/bookingFields/`
Expected: PASS (api, fields, page suites).

- [ ] **Step 9: Full frontend check**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend && npx tsc -b && npm run lint && npm test`
Expected: clean, all tests pass.

- [ ] **Step 10: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend
git add -A
git commit -m "feat(admin-fe): validate + save booking fields (PUT ordered list, 422 surfaced)"
```

---

## Task 5: Docs + end-to-end wiring verification

**Files:**
- Modify: `docs/ROADMAP.md`, `docs/superpowers/specs/2026-07-17-configurable-booking-fields-design.md` (status), `event-admin/docs/API_CONTRACTS.md` (new proxy routes)

- [ ] **Step 1: Update ROADMAP**

In `docs/ROADMAP.md`, move "Configurable booking fields — Phase 3" from "Next" to "What shipped (merged)" once merged; note the feature is now end-to-end (admin UI configures fields). Keep the post-Phase-3 follow-ups (viewing answers, anti-abuse) under deferred.

- [ ] **Step 2: Document the new event-admin routes**

In `event-admin/docs/API_CONTRACTS.md`, add the `/api/scheduling/*` proxy routes (auth: admin JWT; upstream: event-scheduling; error code `scheduling_service_error`, status preserved).

- [ ] **Step 3: Manual smoke (optional, if the stack is up)**

`docker compose up -d --build event-scheduling event-admin event-admin-frontend`, log into the admin UI, open **Поля записи**, pick an event type, add a required `textarea` "Почему нужна помощь", Save, then open that event type in the Booker and confirm the field renders and submits (this closes the loop Phases 1–2 already built).

- [ ] **Step 4: Commit**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add docs/ROADMAP.md docs/superpowers/specs/2026-07-17-configurable-booking-fields-design.md event-admin/docs/API_CONTRACTS.md
git commit -m "docs: booking fields Phase 3 (admin editor) shipped"
```

---

## Self-Review Notes

- **Spec coverage:** event-admin config + adapter + 3 routes (§Admin config Phase 3) → Task 1; admin-frontend editor with add/remove/reorder + type/label/placeholder/required/options + PUT (§Admin config Phase 3) → Tasks 2–4; error handling (§Error handling: validation inline, upstream 422) → Task 4; tests (§Testing: proxy client/routes mocked upstream; editor renders/edits/reorders/PUTs) → Tasks 1,3,4.
- **Authoritative validator:** the proxy forwards verbatim; frontend validation is UX-only mirroring; server (event-scheduling) remains the sole validator — its `422` is surfaced (Task 4 Step 5/7).
- **Type consistency:** `ISchedulingClient` methods (`list_event_types`, `get_booking_fields`, `replace_booking_fields`) match adapter, provider, fake, and routes. Frontend `UpsertBookingField` shape (no `field_key`/`position`) matches the server's `UpsertBookingFieldModel` and `buildUpsertItems` output; `BookingField` matches `BookingFieldModel`.
- **Verify at execution:** confirm the exact `ApiError` constructor signature in `src/modules/shared/api.ts` before Task 4 Step 5, and copy the real `SCHEDULING_API_KEY`/scheduling URL from the event-booker compose block in Task 1 Step 11.
