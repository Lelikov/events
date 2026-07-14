# event-booker-frontend public Booker SPA (срез 4b.2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new public React SPA `event-booker-frontend` that lets a guest pick an event type → slot → enter name+email → get a booking confirmation, talking only to the `event-booker` BFF.

**Architecture:** Vite/React SPA mirroring `event-admin-frontend` (plain CSS, hand-rolled router, vitest+happy-dom, gated Sentry, nginx same-origin proxy, `window._env_` runtime config). Feature module `src/modules/booking/` (API client + wizard components); `src/modules/shared/` (public fetch wrapper, runtimeEnv, routing, ErrorBoundary). Two routes: `/` (event-type list) and `/book/{id}` (3-step wizard).

**Tech Stack:** React 19, Vite 8, TypeScript, plain CSS, vitest + happy-dom, @sentry/react (gated), nginx (prod), node:22-alpine (build).

## Global Constraints

- New package `event-booker-frontend/`, tracked by the ROOT repo `/Users/alexandrlelikov/PycharmProjects/events`. Commit in ROOT on branch `feat/event-booker-frontend` (create off `main` before Task 1).
- Additive ONLY: do NOT modify event-booker (BFF) or any other service. This slice creates one new frontend package + docker-compose/docs edits.
- Mirror `event-admin-frontend` conventions EXACTLY: React 19 + Vite 8 + TS; **plain CSS** (no Tailwind/styled/emotion); **hand-rolled router** (`modules/shared/routing.ts` with `parseRoute`/`navigateTo` + `popstate`/`app:navigate`) — **NO react-router**; **vitest + happy-dom** (`vi.mock` the api module, mount via `createRoot`+`act`); gated Sentry (`observability/sentry.ts`, off unless `VITE_SENTRY_ENABLED==='true'` + DSN); `window._env_` runtime config via `getEnv`; `ErrorBoundary`.
- Public — NO auth/JWT anywhere (the BFF is the trust boundary). The fetch wrapper must NOT attach an Authorization header or redirect to /login.
- BFF contract (exact, from slice 4b.1): `GET /api/public/event-types`→`{items:[{id,slug,title,duration_minutes}]}`; `GET /api/public/event-types/{id}`→`{id,slug,title,duration_minutes}` (404 if absent); `GET /api/public/slots?event_type_id=&start=&end=&time_zone=`→`{event_type_id,time_zone,slots:{"<date>":["<iso>"]}}`; `POST /api/public/bookings` body `{event_type_id,name,email,start_time,time_zone}`→201 `{booking_id,event_type_title,start_time,end_time,status,time_zone}`; errors are `{"detail":"<message>"}` with status 409 (slot taken)/422 (validation)/404 (missing type)/502 (upstream).
- UI copy in **Russian**. Host port **3002** (container 80). Time zone auto-detected via `Intl.DateTimeFormat().resolvedOptions().timeZone`, editable.
- Slot window: default **14 days** from now; a "later" control advances the window by 14 days (scheduling caps at 62).

---

## File Structure

New package `event-booker-frontend/` (root-tracked). Files to CREATE:
- Config: `package.json`, `vite.config.ts`, `vitest.config.ts`, `tsconfig.json`, `tsconfig.app.json`, `tsconfig.node.json`, `eslint.config.js`, `index.html`, `.gitignore`, `.dockerignore`
- Deploy: `Dockerfile`, `nginx.conf`, `docker-entrypoint.d/40-env-config.sh`
- App: `src/main.tsx`, `src/App.tsx`, `src/index.css`, `src/App.css`
- Shared: `src/modules/shared/runtimeEnv.ts`, `src/modules/shared/routing.ts`, `src/modules/shared/api.ts`, `src/modules/shared/ErrorBoundary.tsx`
- Observability: `src/observability/sentry.ts`
- Booking feature: `src/modules/booking/types.ts`, `bookerApi.ts`, `datetime.ts`, `EventTypeListPage.tsx`, `SlotPicker.tsx`, `GuestForm.tsx`, `Confirmation.tsx`, `BookingFlowPage.tsx`
- Tests: `src/modules/shared/routing.test.ts`, `src/observability/sentry.test.ts`, `src/modules/booking/bookerApi.test.ts`, `EventTypeListPage.test.tsx`, `SlotPicker.test.tsx`, `GuestForm.test.tsx`, `BookingFlowPage.test.tsx`
- Modify: `docker-compose.services.yml`, root `CLAUDE.md`, `docs/architecture/ONBOARDING.md`; create `event-booker-frontend/CLAUDE.md`

Reference templates (COPY + adapt from `event-admin-frontend/`): `vitest.config.ts`, `eslint.config.js`, `tsconfig*.json`, `docker-entrypoint.d/40-env-config.sh`, `ErrorBoundary.tsx`, `runtimeEnv.ts`, `observability/sentry.ts` (+ `sentry.test.ts`) are near-identical — copy verbatim, adjust only the ErrorBoundary reload target (`/` instead of `/dashboard`).

---

## Task 1: Scaffold + shared infra + routing + Sentry (bootable app)

**Files:**
- Create: all Config files, Deploy files, `src/main.tsx`, `src/App.tsx`, `src/index.css`, `src/App.css`, `src/modules/shared/{runtimeEnv,routing,ErrorBoundary}.ts(x)`, `src/observability/sentry.ts`
- Test: `src/modules/shared/routing.test.ts`, `src/observability/sentry.test.ts`

**Interfaces:**
- Produces: `getEnv(key)`; `parseRoute(pathname) -> AppRoute`; `navigateTo(path, {replace?})`; `AppRoute = {name:'event-types'} | {name:'book'; eventTypeId:string} | {name:'not-found'}`; `initSentry()`; `ErrorBoundary`; a bootable `App` that route-switches to placeholder page components.

- [ ] **Step 1: `package.json`** (mirror event-admin-frontend; name changed, no extra deps):

```json
{
  "name": "event-booker-frontend",
  "private": true,
  "version": "0.0.0",
  "type": "module",
  "scripts": {
    "dev": "vite",
    "build": "tsc -b && vite build",
    "lint": "eslint .",
    "preview": "vite preview",
    "test": "vitest run"
  },
  "dependencies": {
    "@sentry/react": "^9.47.1",
    "react": "^19.2.4",
    "react-dom": "^19.2.4"
  },
  "devDependencies": {
    "@eslint/js": "^9.39.4",
    "@sentry/vite-plugin": "^3.6.1",
    "@types/node": "^24.12.0",
    "@types/react": "^19.2.14",
    "@types/react-dom": "^19.2.3",
    "@vitejs/plugin-react": "^6.0.0",
    "eslint": "^9.39.4",
    "eslint-plugin-react-hooks": "^7.0.1",
    "eslint-plugin-react-refresh": "^0.5.2",
    "globals": "^17.4.0",
    "happy-dom": "^20.10.2",
    "typescript": "~5.9.3",
    "typescript-eslint": "^8.56.1",
    "vite": "^8.0.0",
    "vitest": "^4.1.8"
  }
}
```
Then `cd event-booker-frontend && npm install` (creates `node_modules` + `package-lock.json`).

- [ ] **Step 2: Copy config verbatim** from `event-admin-frontend/`: `vitest.config.ts`, `eslint.config.js`, `tsconfig.json`, `tsconfig.app.json`, `tsconfig.node.json`, `docker-entrypoint.d/40-env-config.sh`. Create `.gitignore` (`node_modules`, `dist`, `*.local`, `.vite`) and `.dockerignore` (`node_modules`, `dist`, `.git`).

- [ ] **Step 3: `vite.config.ts`** (dev proxy → event-booker host port 8005):

```ts
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'
import { sentryVitePlugin } from '@sentry/vite-plugin'

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // All backend traffic goes to the event-booker BFF (the public trust boundary).
  const apiBaseUrl = env.VITE_API_BASE_URL || 'http://localhost:8005'
  const toBooker = { target: apiBaseUrl, changeOrigin: true }
  return {
    plugins: [
      react(),
      sentryVitePlugin({
        org: process.env.SENTRY_ORG,
        project: process.env.SENTRY_PROJECT,
        authToken: process.env.SENTRY_AUTH_TOKEN,
        release: { name: process.env.SENTRY_RELEASE },
        disable: !process.env.SENTRY_AUTH_TOKEN,
        sourcemaps: { filesToDeleteAfterUpload: ['./dist/**/*.map'] },
      }),
    ],
    build: { sourcemap: 'hidden' },
    server: { proxy: { '/api': toBooker, '/health': toBooker } },
  }
})
```

- [ ] **Step 4: `index.html`** (title + env-config script + main):

```html
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Запись на встречу</title>
  </head>
  <body>
    <div id="root"></div>
    <script src="/env-config.js"></script>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

- [ ] **Step 5: `src/modules/shared/runtimeEnv.ts`** — copy verbatim from event-admin-frontend (`getEnv` reading `window._env_` → `import.meta.env`).

- [ ] **Step 6: `src/modules/shared/routing.ts`**:

```ts
export type AppRoute =
  | { name: 'event-types' }
  | { name: 'book'; eventTypeId: string }
  | { name: 'not-found' }

export function parseRoute(pathname: string): AppRoute {
  if (pathname === '/' || pathname === '/event-types') {
    return { name: 'event-types' }
  }
  const bookMatch = pathname.match(/^\/book\/([^/]+)$/)
  if (bookMatch) {
    return { name: 'book', eventTypeId: decodeURIComponent(bookMatch[1]) }
  }
  return { name: 'not-found' }
}

export function navigateTo(path: string, options?: { replace?: boolean }): void {
  const method = options?.replace ? 'replaceState' : 'pushState'
  window.history[method](null, '', path)
  window.dispatchEvent(new Event('app:navigate'))
}
```

- [ ] **Step 7: Write `src/modules/shared/routing.test.ts`**:

```ts
import { describe, expect, it } from 'vitest'
import { parseRoute } from './routing'

describe('parseRoute', () => {
  it('maps / and /event-types to event-types', () => {
    expect(parseRoute('/')).toEqual({ name: 'event-types' })
    expect(parseRoute('/event-types')).toEqual({ name: 'event-types' })
  })
  it('maps /book/{id} to book with the id', () => {
    expect(parseRoute('/book/abc-123')).toEqual({ name: 'book', eventTypeId: 'abc-123' })
  })
  it('maps unknown paths to not-found', () => {
    expect(parseRoute('/whatever')).toEqual({ name: 'not-found' })
  })
})
```

- [ ] **Step 8: `src/modules/shared/ErrorBoundary.tsx`** — copy from event-admin-frontend, change the reload button target from `/dashboard` to `/` and its label to "На главную".

- [ ] **Step 9: `src/observability/sentry.ts`** — copy verbatim from event-admin-frontend. Also copy `src/observability/sentry.test.ts` verbatim.

- [ ] **Step 10: `src/App.tsx`** (route switch to placeholder pages — real pages arrive in Tasks 3/6):

```tsx
import { useEffect, useMemo, useState } from 'react'
import { parseRoute } from './modules/shared/routing.ts'

function EventTypesPlaceholder() {
  return <h1>Выберите тип встречи</h1>
}

function BookPlaceholder({ eventTypeId }: { eventTypeId: string }) {
  return <h1>Бронирование: {eventTypeId}</h1>
}

function NotFound() {
  return (
    <main>
      <h1>Страница не найдена</h1>
      <a href="/">На главную</a>
    </main>
  )
}

export default function App() {
  const [pathname, setPathname] = useState(window.location.pathname)
  useEffect(() => {
    const sync = () => setPathname(window.location.pathname)
    window.addEventListener('popstate', sync)
    window.addEventListener('app:navigate', sync)
    return () => {
      window.removeEventListener('popstate', sync)
      window.removeEventListener('app:navigate', sync)
    }
  }, [])
  const route = useMemo(() => parseRoute(pathname), [pathname])
  if (route.name === 'event-types') return <EventTypesPlaceholder />
  if (route.name === 'book') return <BookPlaceholder eventTypeId={route.eventTypeId} />
  return <NotFound />
}
```

- [ ] **Step 11: `src/main.tsx`**:

```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './index.css'
import App from './App.tsx'
import { ErrorBoundary } from './modules/shared/ErrorBoundary.tsx'
import { initSentry } from './observability/sentry'

initSentry()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
```

- [ ] **Step 12: `src/index.css` + `src/App.css`** — minimal booker styles. `index.css`: a base reset + font + `:root` + `body` centered container. `App.css`: `.booker-shell` (max-width 640px, centered, padding), `.event-type-card` (bordered clickable card), `.slot-grid`/`.slot-button`, `.field`/`.field-error`, `.banner-error`, `.muted`, `.inline-actions`, `.spinner`. Write concrete rules (real CSS, not placeholders) — e.g.:

```css
/* index.css */
:root { font-family: system-ui, -apple-system, Segoe UI, Roboto, sans-serif; color: #1a1a2e; }
* { box-sizing: border-box; }
body { margin: 0; background: #f5f6fa; }
button { cursor: pointer; font: inherit; }
a { color: #3b5bdb; }
```
```css
/* App.css */
.booker-shell { max-width: 640px; margin: 0 auto; padding: 24px 16px; }
.event-type-card { display: block; width: 100%; text-align: left; padding: 16px; margin: 8px 0; border: 1px solid #d0d3e0; border-radius: 10px; background: #fff; }
.event-type-card:hover { border-color: #3b5bdb; }
.slot-grid { display: flex; flex-wrap: wrap; gap: 8px; margin: 8px 0 16px; }
.slot-button { padding: 8px 12px; border: 1px solid #d0d3e0; border-radius: 8px; background: #fff; }
.slot-button.selected { border-color: #3b5bdb; background: #eaefff; }
.field { display: flex; flex-direction: column; gap: 4px; margin: 12px 0; }
.field input { padding: 8px; border: 1px solid #d0d3e0; border-radius: 8px; }
.field-error { color: #c92a2a; font-size: 0.85rem; }
.banner-error { background: #fff0f0; border: 1px solid #ffc9c9; color: #c92a2a; padding: 10px; border-radius: 8px; margin: 8px 0; }
.muted { color: #6b7280; }
.inline-actions { display: flex; gap: 8px; margin-top: 16px; }
```
Import `App.css` from `App.tsx` (add `import './App.css'` at the top of App.tsx in this step).

- [ ] **Step 13: Deploy files.** `Dockerfile` (copy event-admin-frontend's, drop the admin-only `VITE_ENABLE_DEV_BYPASS_LOGIN` ARG/ENV lines — keep the Sentry ARGs and `VITE_API_BASE_URL`):

```dockerfile
FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL=""
ARG SENTRY_ORG=""
ARG SENTRY_PROJECT=""
ARG SENTRY_AUTH_TOKEN=""
ARG SENTRY_RELEASE=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL} \
    SENTRY_ORG=${SENTRY_ORG} \
    SENTRY_PROJECT=${SENTRY_PROJECT} \
    SENTRY_AUTH_TOKEN=${SENTRY_AUTH_TOKEN} \
    SENTRY_RELEASE=${SENTRY_RELEASE} \
    VITE_SENTRY_RELEASE=${SENTRY_RELEASE}
RUN npm run build

FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.d/40-env-config.sh /docker-entrypoint.d/40-env-config.sh
RUN chmod +x /docker-entrypoint.d/40-env-config.sh
EXPOSE 80
```

`nginx.conf` (simpler than admin — no /auth, no /bookings dual-serve; SPA paths `/`,`/book/*` and API `/api/*` don't collide):

```nginx
# SPA + same-origin API proxy to the event-booker BFF.
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri /index.html;
    }

    location /api/ {
        proxy_pass http://event-booker:8888;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location = /health {
        default_type text/plain;
        return 200 "ok";
    }
}
```

- [ ] **Step 14: Run the gate.** `cd event-booker-frontend && npm run test && npm run lint && npm run build`. Expected: routing (3) + sentry (3) tests pass; eslint clean; tsc+vite build succeeds (produces `dist/`). If `@types/node`-related tsconfig node build complains, ensure `tsconfig.node.json` was copied verbatim.

- [ ] **Step 15: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker-frontend/
git commit -m "feat(booker-fe): scaffold event-booker-frontend SPA (shared, routing, sentry, boot) (slice 4b.2)"
```
> Verify `node_modules/` and `dist/` are NOT staged (`.gitignore` covers them); `package-lock.json` IS committed.

---

## Task 2: API client (`bookerApi`) + types + public fetch wrapper

**Files:**
- Create: `src/modules/shared/api.ts`, `src/modules/booking/types.ts`, `src/modules/booking/bookerApi.ts`
- Test: `src/modules/booking/bookerApi.test.ts`

**Interfaces:**
- Consumes: nothing from earlier tasks except the build setup.
- Produces:
  - `src/modules/shared/api.ts`: `class ApiError extends Error { status: number; details: unknown }`; `apiRequest<T>(path: string, options?: {method?, body?}) : Promise<T>` (NO auth, relative or `VITE_API_BASE_URL`-prefixed).
  - `types.ts`: `EventType`, `Slots`, `CreateBookingBody`, `BookingConfirmation`.
  - `bookerApi.ts`: `listEventTypes()`, `getEventType(id)`, `getSlots(eventTypeId, startISO, endISO, timeZone)`, `createBooking(body)`.

- [ ] **Step 1: `src/modules/shared/api.ts`** (public wrapper — adapted from event-admin-frontend's, JWT removed):

```ts
const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

export class ApiError extends Error {
  status: number
  details: unknown
  constructor(message: string, status: number, details: unknown) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.details = details
  }
}

function parseDetailMessage(payload: unknown): string | null {
  if (typeof payload !== 'object' || payload === null || !('detail' in payload)) {
    return null
  }
  const detail = (payload as { detail: unknown }).detail
  return typeof detail === 'string' ? detail : null
}

type RequestOptions = { method?: 'GET' | 'POST'; body?: unknown }

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body } = options
  const headers: Record<string, string> = { Accept: 'application/json' }
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }
  const response = await fetch(`${API_BASE_URL}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })
  const contentType = response.headers.get('content-type')
  const isJson = contentType?.includes('application/json')
  const payload = isJson ? await response.json() : await response.text()
  if (!response.ok) {
    const message = parseDetailMessage(payload) ?? `Ошибка запроса (${response.status})`
    throw new ApiError(message, response.status, payload)
  }
  return payload as T
}
```

- [ ] **Step 2: `src/modules/booking/types.ts`**:

```ts
export type EventType = {
  id: string
  slug: string
  title: string
  duration_minutes: number
}

export type Slots = {
  event_type_id: string
  time_zone: string
  slots: Record<string, string[]>
}

export type CreateBookingBody = {
  event_type_id: string
  name: string
  email: string
  start_time: string
  time_zone: string
}

export type BookingConfirmation = {
  booking_id: string
  event_type_title: string
  start_time: string
  end_time: string
  status: string
  time_zone: string
}
```

- [ ] **Step 3: Write the failing test** `src/modules/booking/bookerApi.test.ts`:

```ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ApiError } from '../shared/api.ts'
import { createBooking, getEventType, getSlots, listEventTypes } from './bookerApi.ts'

function mockFetch(status: number, jsonBody: unknown) {
  return vi.fn(async () =>
    new Response(JSON.stringify(jsonBody), {
      status,
      headers: { 'content-type': 'application/json' },
    }),
  )
}

afterEach(() => vi.restoreAllMocks())

describe('bookerApi', () => {
  it('listEventTypes unwraps items', async () => {
    const fetchMock = mockFetch(200, { items: [{ id: '1', slug: 's', title: 'T', duration_minutes: 30 }] })
    vi.stubGlobal('fetch', fetchMock)
    const out = await listEventTypes()
    expect(out).toEqual([{ id: '1', slug: 's', title: 'T', duration_minutes: 30 }])
    expect(fetchMock.mock.calls[0][0]).toBe('/api/public/event-types')
  })

  it('getEventType requests the id path', async () => {
    const fetchMock = mockFetch(200, { id: '42', slug: 's', title: 'T', duration_minutes: 60 })
    vi.stubGlobal('fetch', fetchMock)
    const out = await getEventType('42')
    expect(out.duration_minutes).toBe(60)
    expect(fetchMock.mock.calls[0][0]).toBe('/api/public/event-types/42')
  })

  it('getSlots builds the query and returns slots', async () => {
    const fetchMock = mockFetch(200, { event_type_id: '1', time_zone: 'Europe/Moscow', slots: { '2026-10-01': ['2026-10-01T09:00:00Z'] } })
    vi.stubGlobal('fetch', fetchMock)
    const out = await getSlots('1', '2026-10-01T00:00:00Z', '2026-10-15T00:00:00Z', 'Europe/Moscow')
    expect(out.slots['2026-10-01']).toEqual(['2026-10-01T09:00:00Z'])
    const url = fetchMock.mock.calls[0][0] as string
    expect(url).toContain('/api/public/slots?')
    expect(url).toContain('event_type_id=1')
    expect(url).toContain('time_zone=Europe%2FMoscow')
  })

  it('createBooking POSTs the body and returns confirmation', async () => {
    const fetchMock = mockFetch(201, { booking_id: 'b1', event_type_title: 'T', start_time: 'x', end_time: 'y', status: 'confirmed', time_zone: 'UTC' })
    vi.stubGlobal('fetch', fetchMock)
    const out = await createBooking({ event_type_id: '1', name: 'A', email: 'a@b.io', start_time: 'x', time_zone: 'UTC' })
    expect(out.booking_id).toBe('b1')
    const [, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).method).toBe('POST')
    expect(JSON.parse((init as RequestInit).body as string)).toMatchObject({ email: 'a@b.io' })
  })

  it('maps a 409 to ApiError with the detail message', async () => {
    vi.stubGlobal('fetch', mockFetch(409, { detail: 'slot no longer available' }))
    await expect(
      createBooking({ event_type_id: '1', name: 'A', email: 'a@b.io', start_time: 'x', time_zone: 'UTC' }),
    ).rejects.toMatchObject({ status: 409, message: 'slot no longer available' })
    expect(ApiError).toBeDefined()
  })
})
```

- [ ] **Step 4: Run — verify FAIL** (`Cannot find module './bookerApi.ts'`).

- [ ] **Step 5: `src/modules/booking/bookerApi.ts`**:

```ts
import { apiRequest } from '../shared/api.ts'
import type { BookingConfirmation, CreateBookingBody, EventType, Slots } from './types.ts'

export function listEventTypes(): Promise<EventType[]> {
  return apiRequest<{ items: EventType[] }>('/api/public/event-types').then((r) => r.items)
}

export function getEventType(id: string): Promise<EventType> {
  return apiRequest<EventType>(`/api/public/event-types/${encodeURIComponent(id)}`)
}

export function getSlots(
  eventTypeId: string,
  startISO: string,
  endISO: string,
  timeZone: string,
): Promise<Slots> {
  const params = new URLSearchParams({
    event_type_id: eventTypeId,
    start: startISO,
    end: endISO,
    time_zone: timeZone,
  })
  return apiRequest<Slots>(`/api/public/slots?${params.toString()}`)
}

export function createBooking(body: CreateBookingBody): Promise<BookingConfirmation> {
  return apiRequest<BookingConfirmation>('/api/public/bookings', { method: 'POST', body })
}
```

- [ ] **Step 6: Run — verify PASS.** `cd event-booker-frontend && npm run test -- bookerApi` — 5 pass. Then `npm run lint`.

- [ ] **Step 7: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker-frontend/src/modules/shared/api.ts event-booker-frontend/src/modules/booking/types.ts \
        event-booker-frontend/src/modules/booking/bookerApi.ts event-booker-frontend/src/modules/booking/bookerApi.test.ts
git commit -m "feat(booker-fe): public API client + types (slice 4b.2)"
```

---

## Task 3: EventTypeListPage (main screen) + datetime helper

**Files:**
- Create: `src/modules/booking/datetime.ts`, `src/modules/booking/EventTypeListPage.tsx`
- Modify: `src/App.tsx` (route `/` → EventTypeListPage instead of placeholder)
- Test: `src/modules/booking/EventTypeListPage.test.tsx`

**Interfaces:**
- Consumes: `listEventTypes()` (Task 2), `navigateTo` (Task 1), `EventType` type.
- Produces: `datetime.ts`: `formatTime(iso, timeZone)`, `formatDate(iso, timeZone)`, `formatRange(startIso, endIso, timeZone)`; `EventTypeListPage` component.

- [ ] **Step 1: `src/modules/booking/datetime.ts`**:

```ts
export function formatTime(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat('ru-RU', { hour: '2-digit', minute: '2-digit', timeZone }).format(new Date(iso))
}

export function formatDate(iso: string, timeZone: string): string {
  return new Intl.DateTimeFormat('ru-RU', { day: 'numeric', month: 'long', weekday: 'short', timeZone }).format(
    new Date(iso),
  )
}

export function formatRange(startIso: string, endIso: string, timeZone: string): string {
  return `${formatDate(startIso, timeZone)}, ${formatTime(startIso, timeZone)}–${formatTime(endIso, timeZone)}`
}
```

- [ ] **Step 2: Write the failing test** `src/modules/booking/EventTypeListPage.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { EventTypeListPage } from './EventTypeListPage.tsx'

vi.mock('./bookerApi.ts', () => ({ listEventTypes: vi.fn() }))
vi.mock('../shared/routing.ts', () => ({ navigateTo: vi.fn() }))

import { listEventTypes } from './bookerApi.ts'
import { navigateTo } from '../shared/routing.ts'

let container: HTMLDivElement
let root: Root

async function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(<EventTypeListPage />)
  })
}

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

describe('EventTypeListPage', () => {
  it('renders event-type cards and navigates on click', async () => {
    vi.mocked(listEventTypes).mockResolvedValue([
      { id: 'e1', slug: 'intro', title: 'Знакомство', duration_minutes: 30 },
    ])
    await mount()
    await act(async () => {})
    const card = container.querySelector('.event-type-card') as HTMLButtonElement
    expect(card.textContent).toContain('Знакомство')
    expect(card.textContent).toContain('30')
    await act(async () => card.click())
    expect(vi.mocked(navigateTo)).toHaveBeenCalledWith('/book/e1')
  })

  it('shows an error message when the fetch fails', async () => {
    vi.mocked(listEventTypes).mockRejectedValue(new Error('boom'))
    await mount()
    await act(async () => {})
    expect(container.textContent).toContain('Не удалось загрузить')
  })
})
```

- [ ] **Step 3: Run — verify FAIL.**

- [ ] **Step 4: `src/modules/booking/EventTypeListPage.tsx`**:

```tsx
import { useEffect, useState } from 'react'
import { listEventTypes } from './bookerApi.ts'
import { navigateTo } from '../shared/routing.ts'
import type { EventType } from './types.ts'

export function EventTypeListPage() {
  const [types, setTypes] = useState<EventType[] | null>(null)
  const [error, setError] = useState(false)

  useEffect(() => {
    let active = true
    setError(false)
    listEventTypes()
      .then((data) => active && setTypes(data))
      .catch(() => active && setError(true))
    return () => {
      active = false
    }
  }, [])

  if (error) {
    return (
      <main className="booker-shell">
        <p className="banner-error">Не удалось загрузить типы встреч. Обновите страницу.</p>
      </main>
    )
  }
  if (types === null) {
    return (
      <main className="booker-shell">
        <p className="muted">Загрузка…</p>
      </main>
    )
  }
  if (types.length === 0) {
    return (
      <main className="booker-shell">
        <h1>Запись на встречу</h1>
        <p className="muted">Сейчас нет доступных типов встреч.</p>
      </main>
    )
  }
  return (
    <main className="booker-shell">
      <h1>Выберите тип встречи</h1>
      {types.map((t) => (
        <button key={t.id} type="button" className="event-type-card" onClick={() => navigateTo(`/book/${t.id}`)}>
          <strong>{t.title}</strong>
          <div className="muted">{t.duration_minutes} мин</div>
        </button>
      ))}
    </main>
  )
}
```

- [ ] **Step 5: Wire the route** — in `src/App.tsx`, replace the `EventTypesPlaceholder` usage: add `import { EventTypeListPage } from './modules/booking/EventTypeListPage.tsx'` and return `<EventTypeListPage />` for `route.name === 'event-types'`. Delete the `EventTypesPlaceholder` function.

- [ ] **Step 6: Run — verify PASS.** `npm run test -- EventTypeListPage` — 2 pass; `npm run lint`; `npm run build`.

- [ ] **Step 7: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker-frontend/src/modules/booking/datetime.ts \
        event-booker-frontend/src/modules/booking/EventTypeListPage.tsx \
        event-booker-frontend/src/modules/booking/EventTypeListPage.test.tsx event-booker-frontend/src/App.tsx
git commit -m "feat(booker-fe): event-type list page + datetime helpers (slice 4b.2)"
```

---

## Task 4: SlotPicker (step 1)

**Files:**
- Create: `src/modules/booking/SlotPicker.tsx`
- Test: `src/modules/booking/SlotPicker.test.tsx`

**Interfaces:**
- Consumes: `getSlots()` (Task 2), `formatTime`/`formatDate` (Task 3), `Slots` type.
- Produces: `SlotPicker` component with props `{ eventTypeId: string; timeZone: string; onTimeZoneChange: (tz: string) => void; onSelect: (startTime: string) => void }`.

- [ ] **Step 1: Write the failing test** `src/modules/booking/SlotPicker.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { SlotPicker } from './SlotPicker.tsx'

vi.mock('./bookerApi.ts', () => ({ getSlots: vi.fn() }))
import { getSlots } from './bookerApi.ts'

let container: HTMLDivElement
let root: Root

async function mount(onSelect = vi.fn(), onTimeZoneChange = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(
      <SlotPicker eventTypeId="e1" timeZone="UTC" onTimeZoneChange={onTimeZoneChange} onSelect={onSelect} />,
    )
  })
  await act(async () => {})
  return { onSelect, onTimeZoneChange }
}

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

describe('SlotPicker', () => {
  it('renders slot times and reports the picked start_time', async () => {
    vi.mocked(getSlots).mockResolvedValue({
      event_type_id: 'e1',
      time_zone: 'UTC',
      slots: { '2026-10-01': ['2026-10-01T09:00:00Z', '2026-10-01T10:00:00Z'] },
    })
    const { onSelect } = await mount()
    const buttons = container.querySelectorAll('.slot-button')
    expect(buttons.length).toBe(2)
    await act(async () => (buttons[0] as HTMLButtonElement).click())
    expect(onSelect).toHaveBeenCalledWith('2026-10-01T09:00:00Z')
  })

  it('shows a message when there are no slots in the window', async () => {
    vi.mocked(getSlots).mockResolvedValue({ event_type_id: 'e1', time_zone: 'UTC', slots: {} })
    await mount()
    expect(container.textContent).toContain('Нет свободных слотов')
  })
})
```

- [ ] **Step 2: Run — verify FAIL.**

- [ ] **Step 3: `src/modules/booking/SlotPicker.tsx`**:

```tsx
import { useEffect, useMemo, useState } from 'react'
import { getSlots } from './bookerApi.ts'
import { formatDate, formatTime } from './datetime.ts'
import type { Slots } from './types.ts'

const WINDOW_DAYS = 14
const COMMON_ZONES = ['Europe/Moscow', 'Europe/Kaliningrad', 'Asia/Yekaterinburg', 'Asia/Novosibirsk', 'UTC']

type Props = {
  eventTypeId: string
  timeZone: string
  onTimeZoneChange: (tz: string) => void
  onSelect: (startTime: string) => void
}

export function SlotPicker({ eventTypeId, timeZone, onTimeZoneChange, onSelect }: Props) {
  const [offsetDays, setOffsetDays] = useState(0)
  const [data, setData] = useState<Slots | null>(null)
  const [error, setError] = useState(false)

  const zones = useMemo(
    () => (COMMON_ZONES.includes(timeZone) ? COMMON_ZONES : [timeZone, ...COMMON_ZONES]),
    [timeZone],
  )

  useEffect(() => {
    let active = true
    setData(null)
    setError(false)
    const start = new Date(Date.now() + offsetDays * 86_400_000)
    const end = new Date(start.getTime() + WINDOW_DAYS * 86_400_000)
    getSlots(eventTypeId, start.toISOString(), end.toISOString(), timeZone)
      .then((d) => active && setData(d))
      .catch(() => active && setError(true))
    return () => {
      active = false
    }
  }, [eventTypeId, timeZone, offsetDays])

  if (error) {
    return <p className="banner-error">Не удалось загрузить слоты. Попробуйте ещё раз.</p>
  }

  const dates = data ? Object.keys(data.slots).sort() : []

  return (
    <div>
      <label className="field">
        <span>Часовой пояс</span>
        <select value={timeZone} onChange={(e) => onTimeZoneChange(e.target.value)}>
          {zones.map((z) => (
            <option key={z} value={z}>
              {z}
            </option>
          ))}
        </select>
      </label>

      {data === null && <p className="muted">Загрузка…</p>}
      {data !== null && dates.length === 0 && <p className="muted">Нет свободных слотов в этом окне.</p>}

      {dates.map((date) => (
        <section key={date}>
          <h3>{formatDate(data!.slots[date][0], timeZone)}</h3>
          <div className="slot-grid">
            {data!.slots[date].map((iso) => (
              <button key={iso} type="button" className="slot-button" onClick={() => onSelect(iso)}>
                {formatTime(iso, timeZone)}
              </button>
            ))}
          </div>
        </section>
      ))}

      <div className="inline-actions">
        <button type="button" onClick={() => setOffsetDays((o) => o + WINDOW_DAYS)}>
          Позже →
        </button>
        {offsetDays > 0 && (
          <button type="button" onClick={() => setOffsetDays((o) => Math.max(0, o - WINDOW_DAYS))}>
            ← Раньше
          </button>
        )}
      </div>
    </div>
  )
}
```

- [ ] **Step 4: Run — verify PASS.** `npm run test -- SlotPicker` — 2 pass; `npm run lint`.

- [ ] **Step 5: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker-frontend/src/modules/booking/SlotPicker.tsx event-booker-frontend/src/modules/booking/SlotPicker.test.tsx
git commit -m "feat(booker-fe): slot picker with time-zone select + window paging (slice 4b.2)"
```

---

## Task 5: GuestForm (step 2)

**Files:**
- Create: `src/modules/booking/GuestForm.tsx`
- Test: `src/modules/booking/GuestForm.test.tsx`

**Interfaces:**
- Produces: `GuestForm` component with props `{ onSubmit: (name: string, email: string) => void; onBack: () => void; submitError?: string | null; submitting?: boolean }`.

- [ ] **Step 1: Write the failing test** `src/modules/booking/GuestForm.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { GuestForm } from './GuestForm.tsx'

let container: HTMLDivElement
let root: Root

async function mount(onSubmit = vi.fn(), onBack = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(<GuestForm onSubmit={onSubmit} onBack={onBack} />)
  })
  return { onSubmit, onBack }
}

function setInput(el: HTMLInputElement, value: string) {
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

describe('GuestForm', () => {
  it('rejects an invalid email and does not call onSubmit', async () => {
    const { onSubmit } = await mount()
    const [name, email] = Array.from(container.querySelectorAll('input')) as HTMLInputElement[]
    await act(async () => {
      setInput(name, 'Анна')
      setInput(email, 'not-an-email')
    })
    await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
    expect(onSubmit).not.toHaveBeenCalled()
    expect(container.textContent).toContain('Введите корректный email')
  })

  it('submits valid name + email', async () => {
    const { onSubmit } = await mount()
    const [name, email] = Array.from(container.querySelectorAll('input')) as HTMLInputElement[]
    await act(async () => {
      setInput(name, 'Анна')
      setInput(email, 'anna@example.com')
    })
    await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
    expect(onSubmit).toHaveBeenCalledWith('Анна', 'anna@example.com')
  })
})
```

- [ ] **Step 2: Run — verify FAIL.**

- [ ] **Step 3: `src/modules/booking/GuestForm.tsx`**:

```tsx
import { useState, type FormEvent } from 'react'

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/

type Props = {
  onSubmit: (name: string, email: string) => void
  onBack: () => void
  submitError?: string | null
  submitting?: boolean
}

export function GuestForm({ onSubmit, onBack, submitError, submitting }: Props) {
  const [name, setName] = useState('')
  const [email, setEmail] = useState('')
  const [error, setError] = useState<string | null>(null)

  function handleSubmit(e: FormEvent) {
    e.preventDefault()
    if (name.trim() === '') {
      setError('Введите имя')
      return
    }
    if (!EMAIL_RE.test(email)) {
      setError('Введите корректный email')
      return
    }
    setError(null)
    onSubmit(name.trim(), email.trim())
  }

  return (
    <form onSubmit={handleSubmit}>
      <label className="field">
        <span>Имя</span>
        <input value={name} onChange={(e) => setName(e.target.value)} />
      </label>
      <label className="field">
        <span>Email</span>
        <input value={email} onChange={(e) => setEmail(e.target.value)} />
      </label>
      {error && <p className="field-error">{error}</p>}
      {submitError && <p className="banner-error">{submitError}</p>}
      <div className="inline-actions">
        <button type="button" onClick={onBack} disabled={submitting}>
          ← Назад
        </button>
        <button type="submit" disabled={submitting}>
          {submitting ? 'Бронируем…' : 'Забронировать'}
        </button>
      </div>
    </form>
  )
}
```

- [ ] **Step 4: Run — verify PASS.** `npm run test -- GuestForm` — 2 pass; `npm run lint`.

- [ ] **Step 5: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker-frontend/src/modules/booking/GuestForm.tsx event-booker-frontend/src/modules/booking/GuestForm.test.tsx
git commit -m "feat(booker-fe): guest details form with client-side validation (slice 4b.2)"
```

---

## Task 6: BookingFlowPage + Confirmation (wizard) + route wiring

**Files:**
- Create: `src/modules/booking/Confirmation.tsx`, `src/modules/booking/BookingFlowPage.tsx`
- Modify: `src/App.tsx` (route `/book/{id}` → BookingFlowPage; keep NotFound)
- Test: `src/modules/booking/BookingFlowPage.test.tsx`

**Interfaces:**
- Consumes: `getEventType`, `createBooking` (Task 2), `SlotPicker` (Task 4), `GuestForm` (Task 5), `formatRange` (Task 3), `ApiError` (Task 2), `BookingConfirmation` type.
- Produces: `Confirmation` component props `{ confirmation: BookingConfirmation }`; `BookingFlowPage` component props `{ eventTypeId: string }`.

- [ ] **Step 1: `src/modules/booking/Confirmation.tsx`**:

```tsx
import { formatRange } from './datetime.ts'
import { navigateTo } from '../shared/routing.ts'
import type { BookingConfirmation } from './types.ts'

export function Confirmation({ confirmation }: { confirmation: BookingConfirmation }) {
  return (
    <div>
      <h1>Встреча забронирована</h1>
      <p>
        <strong>{confirmation.event_type_title}</strong>
      </p>
      <p>{formatRange(confirmation.start_time, confirmation.end_time, confirmation.time_zone)}</p>
      <p className="muted">Часовой пояс: {confirmation.time_zone}</p>
      <div className="inline-actions">
        <button type="button" onClick={() => navigateTo('/')}>
          На главную
        </button>
      </div>
    </div>
  )
}
```

- [ ] **Step 2: Write the failing test** `src/modules/booking/BookingFlowPage.test.tsx`:

```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { ApiError } from '../shared/api.ts'
import { BookingFlowPage } from './BookingFlowPage.tsx'

vi.mock('./bookerApi.ts', () => ({ getEventType: vi.fn(), getSlots: vi.fn(), createBooking: vi.fn() }))
vi.mock('../shared/routing.ts', () => ({ navigateTo: vi.fn() }))
import { createBooking, getEventType, getSlots } from './bookerApi.ts'

let container: HTMLDivElement
let root: Root

async function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => {
    root.render(<BookingFlowPage eventTypeId="e1" />)
  })
  await act(async () => {})
}

async function pickSlotAndFillForm() {
  const slot = container.querySelector('.slot-button') as HTMLButtonElement
  await act(async () => slot.click())
  const [name, email] = Array.from(container.querySelectorAll('input')) as HTMLInputElement[]
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  await act(async () => {
    setter.call(name, 'Анна')
    name.dispatchEvent(new Event('input', { bubbles: true }))
    setter.call(email, 'anna@example.com')
    email.dispatchEvent(new Event('input', { bubbles: true }))
  })
  await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
}

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

describe('BookingFlowPage', () => {
  it('walks slot → form → confirmation on success', async () => {
    vi.mocked(getEventType).mockResolvedValue({ id: 'e1', slug: 's', title: 'Знакомство', duration_minutes: 30 })
    vi.mocked(getSlots).mockResolvedValue({ event_type_id: 'e1', time_zone: 'UTC', slots: { '2026-10-01': ['2026-10-01T09:00:00Z'] } })
    vi.mocked(createBooking).mockResolvedValue({ booking_id: 'b1', event_type_title: 'Знакомство', start_time: '2026-10-01T09:00:00Z', end_time: '2026-10-01T09:30:00Z', status: 'confirmed', time_zone: 'UTC' })
    await mount()
    await pickSlotAndFillForm()
    await act(async () => {})
    expect(container.textContent).toContain('Встреча забронирована')
    expect(vi.mocked(createBooking).mock.calls[0][0]).toMatchObject({ event_type_id: 'e1', email: 'anna@example.com', start_time: '2026-10-01T09:00:00Z' })
  })

  it('returns to the slot step with a banner on 409', async () => {
    vi.mocked(getEventType).mockResolvedValue({ id: 'e1', slug: 's', title: 'Знакомство', duration_minutes: 30 })
    vi.mocked(getSlots).mockResolvedValue({ event_type_id: 'e1', time_zone: 'UTC', slots: { '2026-10-01': ['2026-10-01T09:00:00Z'] } })
    vi.mocked(createBooking).mockRejectedValue(new ApiError('slot no longer available', 409, {}))
    await mount()
    await pickSlotAndFillForm()
    await act(async () => {})
    expect(container.textContent).toContain('слот')
    expect(container.querySelector('.slot-button')).not.toBeNull()
  })
})
```

- [ ] **Step 3: Run — verify FAIL.**

- [ ] **Step 4: `src/modules/booking/BookingFlowPage.tsx`**:

```tsx
import { useEffect, useMemo, useState } from 'react'
import { ApiError } from '../shared/api.ts'
import { createBooking, getEventType } from './bookerApi.ts'
import { SlotPicker } from './SlotPicker.tsx'
import { GuestForm } from './GuestForm.tsx'
import { Confirmation } from './Confirmation.tsx'
import { formatRange } from './datetime.ts'
import { navigateTo } from '../shared/routing.ts'
import type { BookingConfirmation, EventType } from './types.ts'

type Step = 'slot' | 'details' | 'done'

function detectTimeZone(): string {
  return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
}

export function BookingFlowPage({ eventTypeId }: { eventTypeId: string }) {
  const [eventType, setEventType] = useState<EventType | null>(null)
  const [notFound, setNotFound] = useState(false)
  const [timeZone, setTimeZone] = useState(detectTimeZone)
  const [step, setStep] = useState<Step>('slot')
  const [selected, setSelected] = useState<string | null>(null)
  const [banner, setBanner] = useState<string | null>(null)
  const [submitError, setSubmitError] = useState<string | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [confirmation, setConfirmation] = useState<BookingConfirmation | null>(null)

  useEffect(() => {
    let active = true
    getEventType(eventTypeId)
      .then((et) => active && setEventType(et))
      .catch((err) => {
        if (!active) return
        if (err instanceof ApiError && err.status === 404) {
          setNotFound(true)
          return
        }
        setNotFound(true)
      })
    return () => {
      active = false
    }
  }, [eventTypeId])

  const durationLabel = useMemo(() => (eventType ? `${eventType.duration_minutes} мин` : ''), [eventType])

  if (notFound) {
    return (
      <main className="booker-shell">
        <h1>Тип встречи не найден</h1>
        <a href="/">На главную</a>
      </main>
    )
  }

  if (confirmation) {
    return (
      <main className="booker-shell">
        <Confirmation confirmation={confirmation} />
      </main>
    )
  }

  function handleSelect(startTime: string) {
    setSelected(startTime)
    setBanner(null)
    setStep('details')
  }

  async function handleSubmit(name: string, email: string) {
    if (selected === null) return
    setSubmitting(true)
    setSubmitError(null)
    try {
      const result = await createBooking({
        event_type_id: eventTypeId,
        name,
        email,
        start_time: selected,
        time_zone: timeZone,
      })
      setConfirmation(result)
      setStep('done')
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) {
        setBanner('Этот слот только что заняли. Выберите другое время.')
        setSelected(null)
        setStep('slot')
        return
      }
      if (err instanceof ApiError && err.status === 422) {
        setSubmitError(err.message)
        return
      }
      setSubmitError('Сервис временно недоступен. Попробуйте ещё раз.')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <main className="booker-shell">
      <h1>{eventType ? eventType.title : 'Бронирование'}</h1>
      {durationLabel && <p className="muted">{durationLabel}</p>}
      {banner && <p className="banner-error">{banner}</p>}

      {step === 'slot' && (
        <SlotPicker
          eventTypeId={eventTypeId}
          timeZone={timeZone}
          onTimeZoneChange={setTimeZone}
          onSelect={handleSelect}
        />
      )}

      {step === 'details' && selected && (
        <div>
          <p className="muted">Выбрано: {formatRange(selected, selected, timeZone)}</p>
          <GuestForm onSubmit={handleSubmit} onBack={() => setStep('slot')} submitError={submitError} submitting={submitting} />
        </div>
      )}

      <p className="inline-actions">
        <button type="button" onClick={() => navigateTo('/')}>
          ← Все типы встреч
        </button>
      </p>
    </main>
  )
}
```
> Note: on the details step the summary uses `formatRange(selected, selected, tz)` — end time is unknown client-side (only the slot start is chosen); showing the start twice renders "date, HH:MM–HH:MM" harmlessly, and the real end appears on the confirmation from the BFF. Do NOT try to compute the end from duration here (keep it simple; YAGNI).

- [ ] **Step 5: Wire the route** — in `src/App.tsx`: add `import { BookingFlowPage } from './modules/booking/BookingFlowPage.tsx'`, return `<BookingFlowPage eventTypeId={route.eventTypeId} />` for `route.name === 'book'`, delete `BookPlaceholder`. Keep the existing `NotFound`.

- [ ] **Step 6: Run — verify PASS + full suite.** `cd event-booker-frontend && npm run test` (all suites: routing 3, sentry 3, bookerApi 5, EventTypeListPage 2, SlotPicker 2, GuestForm 2, BookingFlowPage 2). Then `npm run lint && npm run build` — all green.

- [ ] **Step 7: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker-frontend/src/modules/booking/Confirmation.tsx \
        event-booker-frontend/src/modules/booking/BookingFlowPage.tsx \
        event-booker-frontend/src/modules/booking/BookingFlowPage.test.tsx event-booker-frontend/src/App.tsx
git commit -m "feat(booker-fe): booking wizard (flow + confirmation) + route wiring (slice 4b.2)"
```

---

## Task 7: docker-compose + docs + final gate

**Files:**
- Modify: `docker-compose.services.yml`, root `CLAUDE.md`, `docs/architecture/ONBOARDING.md`
- Create: `event-booker-frontend/CLAUDE.md`

- [ ] **Step 1: Full gate.** `cd /Users/alexandrlelikov/PycharmProjects/events/event-booker-frontend && npm run test && npm run lint && npm run build` — all green (19 tests: 3+3+5+2+2+2+2).

- [ ] **Step 2: `docker-compose.services.yml`** — add an `event-booker-frontend` service (mirror the `event-admin-frontend` block; read it first to match indentation):

```yaml
  event-booker-frontend:
    build:
      context: ./event-booker-frontend
      dockerfile: Dockerfile
      args:
        # Empty = same-origin: nginx in the container proxies /api and /health to event-booker:8888.
        VITE_API_BASE_URL: ${VITE_API_BASE_URL:-}
    ports:
      - "${BOOKER_FRONTEND_PORT:-3002}:80"
    depends_on:
      event-booker:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "wget -qO /dev/null http://127.0.0.1:80/health"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    restart: unless-stopped
```

- [ ] **Step 3: Docs.**
  - `event-booker-frontend/CLAUDE.md`: role (public Booker SPA over the event-booker BFF), the two routes (`/` list, `/book/{id}` wizard), the flow (event-type → slot → name+email → confirmation), the stack/conventions (React 19 + Vite, plain CSS, hand-rolled router, vitest, gated Sentry, nginx proxy to event-booker), commands (`npm run dev/test/lint/build`), and deferred items (cancel/reschedule, payments, i18n framework). Mirror `event-admin-frontend/CLAUDE.md` structure.
  - Root `CLAUDE.md`: add `event-booker-frontend` to the services table (row: "TypeScript, React, Vite — Public Booker SPA: guest event-type → slot → booking, over event-booker BFF") and the host-ports table (`3002 | event-booker-frontend (public Booker SPA, nginx → event-booker)`). Read the current tables first.
  - `docs/architecture/ONBOARDING.md`: add a short "event-booker-frontend (public Booker SPA)" note (role, two routes, talks only to event-booker; the public booking chain browser → event-booker-frontend → event-booker → event-scheduling/event-users).
  - Every doc claim must be TRUE against the code built in Tasks 1–6.

- [ ] **Step 4: Re-run the gate** to confirm still green (docs/compose don't affect the frontend build/tests).

- [ ] **Step 5: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add docker-compose.services.yml CLAUDE.md docs/architecture/ONBOARDING.md event-booker-frontend/CLAUDE.md
git commit -m "docs(booker-fe): compose wiring + docs for event-booker-frontend (slice 4b.2)"
```

---

## Self-Review (completed during plan authoring)

**1. Spec coverage:** §0/§1 architecture (SPA mirroring admin, feature module) → Tasks 1–6. §2 screens/flow: event-type list → Task 3; slot picker → Task 4; guest form → Task 5; wizard + confirmation + error UX (409/422/404/502) → Task 6. §3 API client + types → Task 2. §4 error/loading (ErrorBoundary, per-fetch states, 409 special UX) → Task 1 (ErrorBoundary) + Tasks 3/4/6. §5 Sentry gated → Task 1. §6 deploy (Dockerfile/nginx/entrypoint/vite proxy/compose) → Task 1 (build/nginx) + Task 7 (compose). §7 tests → distributed (routing, sentry, bookerApi, each component, flow). §8 deferred (cancel/reschedule/payments/i18n/Helm) → noted, not built. §9 DoR + docs → Task 7.

**2. Placeholders:** All app code is complete. Pure boilerplate (vitest/eslint/tsconfig configs, runtimeEnv, ErrorBoundary, sentry + its test, docker-entrypoint env script) is "copy verbatim from event-admin-frontend, adjust the named bits" (Task 1 Steps 2/5/8/9) — a copy instruction against real files, not a placeholder. Docs (Task 7) are "read file, add focused section."

**3. Type consistency:** `EventType{id,slug,title,duration_minutes}`, `Slots{event_type_id,time_zone,slots}`, `CreateBookingBody{event_type_id,name,email,start_time,time_zone}`, `BookingConfirmation{booking_id,event_type_title,start_time,end_time,status,time_zone}` (Task 2) used identically in bookerApi (Task 2), SlotPicker (Task 4), GuestForm→flow (Tasks 5/6), Confirmation (Task 6). `apiRequest`/`ApiError` (Task 2) consumed by bookerApi (Task 2) + BookingFlowPage 409/422 handling (Task 6). `AppRoute`/`parseRoute`/`navigateTo` (Task 1) used in App.tsx (Tasks 1/3/6), EventTypeListPage (Task 3), Confirmation/flow (Task 6). Component prop shapes: `SlotPicker{eventTypeId,timeZone,onTimeZoneChange,onSelect}` (Task 4) matches its use in BookingFlowPage (Task 6); `GuestForm{onSubmit,onBack,submitError,submitting}` (Task 5) matches flow usage (Task 6). `formatTime/formatDate/formatRange` (Task 3) used in SlotPicker (Task 4) + Confirmation/flow (Task 6).
