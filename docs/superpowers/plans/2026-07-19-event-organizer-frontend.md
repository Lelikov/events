# event-organizer-frontend Implementation Plan

> **For agentic workers:** Execute tasks in order. Each task is TDD: write the failing test, run it and SEE it fail, write the minimal code, run it and SEE it pass, then commit. Do not skip the "see it fail" step. Every code block below is complete and final — paste it verbatim (renaming only where a step tells you to). No placeholders, no "similar to Task N", no "add error handling later". If a command's real output diverges from the "expected output" shown, stop and reconcile before moving on.

## Goal

Build `event-organizer-frontend` — the organizer cabinet SPA (slice 6.2), a thin authenticated client over the `event-organizer` BFF. An organizer logs in with email + password, then manages their **own** availability schedule, views their **own** bookings, and edits their **own** profile + password. Every data call is a BFF `/api/me/*` request that injects the organizer's `user_id` server-side; the SPA holds no domain logic beyond form state and the JWT session. It is a 1:1 mirror of `event-admin-frontend`'s stack and conventions, minus TOTP.

## Architecture

- New package `event-organizer-frontend/` at the monorepo root.
- React 19 + Vite + TypeScript, **plain CSS only**, `events-design-system` (git-tag npm dep).
- **No router library** — manual routing in `shared/routing.ts` (`parseRoute` → `AppRoute` union, `navigateTo` → `history.pushState` + `app:navigate` event; `App.tsx` re-renders on `popstate`/`app:navigate`).
- **Authenticated**: mirrors admin-fe's auth machinery (sessionStorage JWT, `exp` decode, 401 interceptor), minus TOTP.
- nginx SPA (prod `Dockerfile` + `nginx.conf`) same-origin-proxies `/api/*`, `/auth/*` to `event-organizer:8888`; `/health` answered by nginx itself; `docker-entrypoint.d/40-env-config.sh` writes `window._env_` at container start.
- Host port **3003** (container 80) in `docker-compose.services.yml`.

Module structure (`src/modules/`):

```
auth/      LoginPage · AuthContext (AuthProvider) · useAuth · context.ts · jwt.ts · storage.ts · authApi.ts · types.ts
schedule/  SchedulePage · WeeklyHours · DateOverrides · Travel · scheduleApi.ts · schedule.ts (pure helpers) · types.ts
bookings/  BookingsPage · bookingsApi.ts · types.ts
profile/   ProfilePage · profileApi.ts · types.ts
shared/    api.ts · runtimeEnv.ts · routing.ts · ErrorBoundary.tsx · format.ts · TimeZoneField.tsx · timezones.ts
app/       OrganizerLayout.tsx
```

## Tech Stack

- Vite 8, React 19.2, TypeScript 5.9, Vitest 4 + happy-dom, ESLint 9 (typescript-eslint).
- `events-design-system` at `github:Lelikov/events-design-system#v0.1.0` (provides the CSS reset, `.field`/`.inline-actions`/`.card`/`.login-*`/`.tz-*`/`.badge--*` classes and the `Icon`/`ErrorBoundary` React components).
- Tests: `createRoot` + `act` (from `react`), **no @testing-library**; native `setInput` via the prototype value setter; mock `apiRequest`/`fetch` and assert request bodies + rendered DOM.

## Global Constraints

- **No `else if`**; **avoid `else`** — early returns, guard clauses, mapping objects.
- All UI copy in **Russian**.
- Plain CSS only — no Tailwind/styled-components/emotion. No Sentry, no TOTP, no dev-bypass login button.
- sessionStorage JWT key is exactly `event_organizer_jwt`.
- **Logout is client-side only** — the BFF has no logout endpoint. `logout()` clears storage + state; the caller navigates to `/login`.
- Host port **3003**; nginx proxy target `event-organizer:8888`.
- No client-side role logic — the BFF's JWT is organizer-scoped by construction.

### Pinned BFF contract (source: `event-organizer/event_organizer/routers/me.py` + `schemas/me.py`, `adapters/scheduling_client.py`)

| Method | Path | Request body the SPA sends | Success | Error |
|---|---|---|---|---|
| POST | `/auth/login` | `{email, password}` (`auth:false`) | `200 {access_token}` | `401` bad creds |
| GET | `/api/me/schedule` | — | `200` bundle | `404` = no schedule yet |
| PUT | `/api/me/schedule` | `{name, time_zone, weekly_hours, date_overrides}` | `200` bundle | `422` |
| PUT | `/api/me/schedule/travel` | `{travel_schedules: [{time_zone, start_date, end_date, prev_time_zone}]}` | `200` | `422` |
| GET | `/api/me/bookings` | — | `200 [{id, start_time, end_time, status}]` | — |
| GET | `/api/me/profile` | — | `200 {name, email, time_zone}` (name/tz nullable) | — |
| PUT | `/api/me/profile` | `{name, time_zone}` | `200 {name, email, time_zone}` | `422` |
| PUT | `/api/me/password` | `{old_password, new_password}` | `204` | `401` wrong old pw |

Bundle shape (from event-scheduling `ScheduleBundleResponse`): `{ schedule: {id, owner_user_id, name, time_zone}, weekly_hours: [{day_of_week (1=Mon..7=Sun), start_time, end_time}], date_overrides: [{date, start_time, end_time}], travel_schedules: [{time_zone, start_date, end_date, prev_time_zone}] }`. Times come back as `"HH:MM:SS"` and dates as `"YYYY-MM-DD"` (Pydantic JSON). The BFF/event-scheduling accept `"HH:MM"` on write. `date_overrides` with `start_time=null, end_time=null` = full-day block.

> **BFF FIX INCLUDED (Task 14):** `event-organizer`'s `SchedulePutRequest` currently has **no `name` field**, so it drops `name` before forwarding to event-scheduling's `UpsertScheduleRequest`, which **requires** `name` → the upsert would 422 end-to-end. **Task 14 adds `name` to the BFF request schema** (it auto-forwards via `body.model_dump()`), so the SPA's `{name, …}` body works. This is the one small out-of-package (Python) change this slice needs.

---

## Task 1 — Scaffold + shared plumbing

Create the package skeleton and the shared layer (`runtimeEnv`, `routing`, `api`, `ErrorBoundary`, `format`), plus root config. End state: `npm install` succeeds and `npm run build` typechecks.

**Files**
- Create: `event-organizer-frontend/package.json`, `tsconfig.json`, `tsconfig.app.json`, `tsconfig.node.json`, `vite.config.ts`, `vitest.config.ts`, `index.html`, `.env.example`, `.dockerignore`, `.gitignore`, `eslint.config.js`, `public/env-config.js`
- Create: `src/main.tsx`, `src/App.tsx` (temporary stub), `src/index.css`
- Create: `src/modules/shared/runtimeEnv.ts`, `src/modules/shared/routing.ts`, `src/modules/shared/api.ts`, `src/modules/shared/ErrorBoundary.tsx`, `src/modules/shared/format.ts`
- Create tests: `src/modules/shared/routing.test.ts`, `src/modules/shared/format.test.ts`

**Interfaces**
- Produces `getEnv(key: string): string`
- Produces `parseRoute(pathname: string): AppRoute` where `AppRoute = {name:'login'} | {name:'schedule'} | {name:'bookings'} | {name:'profile'} | {name:'not-found'}`; `navigateTo(path: string, options?: {replace?: boolean}): void`
- Produces `apiRequest<T>(path: string, options?: RequestOptions): Promise<T>`; `class ApiError extends Error { status; code; details }`
- Produces `formatDateTime(value, timeZone?): string`; `formatRange(start, end, timeZone?): string`
- Produces `ErrorBoundary({children})`

### Steps

1. **Scaffold the directory and write config files.**

`event-organizer-frontend/package.json`:
```json
{
  "name": "event-organizer-frontend",
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
    "events-design-system": "github:Lelikov/events-design-system#v0.1.0",
    "react": "^19.2.4",
    "react-dom": "^19.2.4"
  },
  "devDependencies": {
    "@eslint/js": "^9.39.4",
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

`tsconfig.json`:
```json
{
  "files": [],
  "references": [
    { "path": "./tsconfig.app.json" },
    { "path": "./tsconfig.node.json" }
  ]
}
```

`tsconfig.app.json`:
```json
{
  "compilerOptions": {
    "tsBuildInfoFile": "./node_modules/.tmp/tsconfig.app.tsbuildinfo",
    "target": "ES2023",
    "useDefineForClassFields": true,
    "lib": ["ES2023", "DOM", "DOM.Iterable"],
    "module": "ESNext",
    "types": ["vite/client"],
    "skipLibCheck": true,

    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "verbatimModuleSyntax": true,
    "moduleDetection": "force",
    "noEmit": true,
    "jsx": "react-jsx",

    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "erasableSyntaxOnly": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedSideEffectImports": true
  },
  "include": ["src"]
}
```

`tsconfig.node.json`:
```json
{
  "compilerOptions": {
    "tsBuildInfoFile": "./node_modules/.tmp/tsconfig.node.tsbuildinfo",
    "target": "ES2023",
    "lib": ["ES2023"],
    "module": "ESNext",
    "types": ["node"],
    "skipLibCheck": true,

    "moduleResolution": "bundler",
    "allowImportingTsExtensions": true,
    "verbatimModuleSyntax": true,
    "moduleDetection": "force",
    "noEmit": true,

    "strict": true,
    "noUnusedLocals": true,
    "noUnusedParameters": true,
    "erasableSyntaxOnly": true,
    "noFallthroughCasesInSwitch": true,
    "noUncheckedSideEffectImports": true
  },
  "include": ["vite.config.ts"]
}
```

`vite.config.ts`:
```ts
import { defineConfig, loadEnv } from 'vite'
import react from '@vitejs/plugin-react'

// All backend traffic goes to the event-organizer BFF: it authenticates the
// organizer and proxies /api/me/* to event-scheduling / event-users itself.
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  const apiBaseUrl = env.VITE_API_BASE_URL || 'http://localhost:8006'
  const toOrganizer = { target: apiBaseUrl, changeOrigin: true }
  return {
    plugins: [react()],
    server: {
      proxy: {
        '/api': toOrganizer,
        '/auth': toOrganizer,
        '/health': toOrganizer,
      },
    },
  }
})
```

`vitest.config.ts`:
```ts
import { defineConfig } from 'vitest/config'

export default defineConfig({
  test: {
    environment: 'happy-dom',
  },
})
```

`index.html`:
```html
<!doctype html>
<html lang="ru">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Кабинет организатора</title>
  </head>
  <body>
    <div id="root"></div>
    <script src="/env-config.js"></script>
    <script type="module" src="/src/main.tsx"></script>
  </body>
</html>
```

`.env.example`:
```
# Base URL of the event-organizer BFF (the ONLY backend this SPA talks to).
# Empty (default) = same-origin: nginx proxies /api, /auth and /health to
# event-organizer:8888. In dev, the Vite proxy forwards to this URL.
VITE_API_BASE_URL=http://localhost:8006
```

`.dockerignore`:
```
node_modules
dist
.git
.env
.env.*
!.env.example
```

`.gitignore`:
```
node_modules
dist
.env
.env.*
!.env.example
*.local
```

`eslint.config.js`:
```js
import js from '@eslint/js'
import globals from 'globals'
import reactHooks from 'eslint-plugin-react-hooks'
import reactRefresh from 'eslint-plugin-react-refresh'
import tseslint from 'typescript-eslint'
import { defineConfig, globalIgnores } from 'eslint/config'

export default defineConfig([
  globalIgnores(['dist']),
  {
    files: ['**/*.{ts,tsx}'],
    extends: [
      js.configs.recommended,
      tseslint.configs.recommended,
      reactHooks.configs.flat.recommended,
      reactRefresh.configs.vite,
    ],
    languageOptions: {
      ecmaVersion: 2020,
      globals: globals.browser,
    },
  },
])
```

`public/env-config.js`:
```js
window._env_ = {};
```

`src/index.css` (app-specific classes; the DS stylesheet provides the reset + base):
```css
/* App-specific layout. Reset, body, button, .field, .inline-actions, .card,
   .login-*, .tz-*, .badge--* all come from events-design-system/styles.css. */

.org-shell {
  display: grid;
  grid-template-columns: 240px 1fr;
  min-height: 100vh;
}

.org-content {
  padding: 28px 32px;
  max-width: 900px;
}

.page-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  margin-bottom: 20px;
}

.section {
  border: 1px solid var(--border);
  border-radius: 12px;
  background: var(--card);
  padding: 20px;
  margin-bottom: 18px;
  display: grid;
  gap: 14px;
}

.section h2 { font-size: 15px; font-weight: 600; }

.weekday-row {
  display: grid;
  grid-template-columns: 90px 1fr;
  gap: 12px;
  align-items: start;
  padding: 8px 0;
  border-top: 1px solid var(--border);
}

.weekday-name { font-weight: 600; padding-top: 6px; }
.interval-row { display: flex; align-items: center; gap: 8px; margin-bottom: 6px; }
.interval-row input[type="time"] { width: 120px; }

.override-row,
.travel-row {
  display: flex;
  align-items: center;
  gap: 10px;
  flex-wrap: wrap;
  padding: 8px 0;
  border-top: 1px solid var(--border);
}

.icon-button {
  background: transparent;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 4px 10px;
  cursor: pointer;
  color: var(--muted);
}
.icon-button:hover { color: var(--danger, #c92a2a); }

.link-button {
  background: transparent;
  border: none;
  box-shadow: none;
  color: var(--primary);
  padding: 6px 0;
  cursor: pointer;
}

.error-text { color: #c92a2a; font-size: 0.9rem; }
.ok-text { color: #2b8a3e; font-size: 0.9rem; }

.booking-group { display: grid; gap: 8px; margin-bottom: 20px; }
.booking-row {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 12px 14px;
  background: var(--card);
}
.empty-state { color: var(--muted); padding: 24px 0; text-align: center; }

.login-form-panel .field + .field { margin-top: 12px; }
```

2. **Write the shared runtime env reader** — `src/modules/shared/runtimeEnv.ts`:
```ts
declare global {
  interface Window {
    _env_?: Record<string, string>
  }
}

// Runtime value (window._env_, injected by docker-entrypoint.d/40-env-config.sh)
// wins over the build-time import.meta.env value so one image serves every env.
export const getEnv = (key: string): string => {
  const runtimeEnv = typeof window === 'undefined' ? undefined : window._env_
  if (runtimeEnv && runtimeEnv[key]) {
    return runtimeEnv[key]
  }
  return (import.meta.env[key] as string | undefined) ?? ''
}
```

3. **Write the failing routing test** — `src/modules/shared/routing.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { parseRoute } from './routing.ts'

describe('parseRoute', () => {
  it('parses known routes', () => {
    expect(parseRoute('/login')).toEqual({ name: 'login' })
    expect(parseRoute('/')).toEqual({ name: 'schedule' })
    expect(parseRoute('/bookings')).toEqual({ name: 'bookings' })
    expect(parseRoute('/profile')).toEqual({ name: 'profile' })
  })

  it('returns not-found for unknown paths', () => {
    expect(parseRoute('/nope')).toEqual({ name: 'not-found' })
    expect(parseRoute('/bookings/123')).toEqual({ name: 'not-found' })
  })
})
```

4. **Run it — expect FAIL** (module not found):
```bash
cd event-organizer-frontend && npm install && npm test -- src/modules/shared/routing.test.ts
```
Expected: `Error: Failed to load url ./routing.ts` / `1 failed`.

5. **Implement** `src/modules/shared/routing.ts`:
```ts
export type AppRoute =
  | { name: 'login' }
  | { name: 'schedule' }
  | { name: 'bookings' }
  | { name: 'profile' }
  | { name: 'not-found' }

export function parseRoute(pathname: string): AppRoute {
  if (pathname === '/login') {
    return { name: 'login' }
  }
  if (pathname === '/' || pathname === '/schedule') {
    return { name: 'schedule' }
  }
  if (pathname === '/bookings') {
    return { name: 'bookings' }
  }
  if (pathname === '/profile') {
    return { name: 'profile' }
  }
  return { name: 'not-found' }
}

export function navigateTo(path: string, options?: { replace?: boolean }): void {
  const method = options?.replace ? 'replaceState' : 'pushState'
  window.history[method](null, '', path)
  window.dispatchEvent(new Event('app:navigate'))
}
```

6. **Run — expect PASS**: `npm test -- src/modules/shared/routing.test.ts` → `1 passed`.

7. **Write `src/modules/shared/api.ts`** (mirrors admin-fe; the 401-with-token interceptor clears the session and redirects to `/login`):
```ts
import { getJwtToken, removeJwtToken } from '../auth/storage.ts'

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? ''

if (!import.meta.env.DEV && !API_BASE_URL) {
  console.warn(
    'VITE_API_BASE_URL is empty: API requests will be sent relative to the static host. ' +
      'Set VITE_API_BASE_URL at build time unless the SPA is served behind the same origin as event-organizer.',
  )
}

export class ApiError extends Error {
  status: number
  code: string | null
  details: unknown

  constructor(message: string, status: number, details: unknown, code: string | null = null) {
    super(message)
    this.name = 'ApiError'
    this.status = status
    this.code = code
    this.details = details
  }
}

type ErrorDetail = { code: string | null; message: string | null }

// FastAPI HTTPException returns detail as a plain string; a structured
// {code, message} detail is also tolerated.
function parseErrorDetail(payload: unknown): ErrorDetail {
  if (typeof payload !== 'object' || payload === null || !('detail' in payload)) {
    return { code: null, message: null }
  }
  const detail = (payload as { detail: unknown }).detail
  if (typeof detail === 'string') {
    return { code: null, message: detail }
  }
  if (typeof detail === 'object' && detail !== null) {
    const structured = detail as { code?: unknown; message?: unknown }
    return {
      code: typeof structured.code === 'string' ? structured.code : null,
      message: typeof structured.message === 'string' ? structured.message : null,
    }
  }
  return { code: null, message: null }
}

type RequestOptions = {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE'
  body?: unknown
  auth?: boolean
  baseUrl?: string
}

export async function apiRequest<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, auth = true, baseUrl = API_BASE_URL } = options
  const headers: Record<string, string> = {
    Accept: 'application/json',
  }

  if (body !== undefined) {
    headers['Content-Type'] = 'application/json'
  }

  let tokenAttached = false
  if (auth) {
    const token = getJwtToken()
    if (token) {
      headers.Authorization = `Bearer ${token}`
      tokenAttached = true
    }
  }

  const response = await fetch(`${baseUrl}${path}`, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (response.status === 204) {
    return null as T
  }

  const contentType = response.headers.get('content-type')
  const isJson = contentType?.includes('application/json')
  const payload = isJson ? await response.json() : await response.text()

  if (!response.ok) {
    const detail = parseErrorDetail(payload)
    const message = detail.message ?? `Ошибка запроса (${response.status})`
    const error = new ApiError(message, response.status, payload, detail.code)
    // A 401 on a request that carried a token means the JWT is expired or
    // revoked: clear the session and force a re-login. Requests without a
    // token (POST /auth/login itself) must NOT redirect.
    if (error.status === 401 && tokenAttached) {
      removeJwtToken()
      window.location.href = '/login'
    }
    throw error
  }

  return payload as T
}
```
> `api.ts` imports `../auth/storage.ts`, created in Task 2. That is fine — this task does not compile `api.ts` in isolation; the full `npm run build` at the end of Task 1 does not import `api.ts` yet (the stub `App.tsx` in step 10 does not). `api.ts` is first exercised in Task 2.

8. **Write `src/modules/shared/ErrorBoundary.tsx`** (thin wrapper over the design-system boundary):
```tsx
import type { ReactNode } from 'react'
import { ErrorBoundary as DSErrorBoundary } from 'events-design-system'

export function ErrorBoundary({ children }: { children: ReactNode }) {
  return (
    <DSErrorBoundary homeHref="/" onError={(e) => console.error(e)}>
      {children}
    </DSErrorBoundary>
  )
}
```

9. **Write the failing format test** — `src/modules/shared/format.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { formatDateTime, formatRange } from './format.ts'

describe('formatDateTime', () => {
  it('returns a dash for null', () => {
    expect(formatDateTime(null)).toBe('—')
  })

  it('formats an ISO string in a fixed zone', () => {
    expect(formatDateTime('2026-07-25T09:00:00Z', 'UTC')).toContain('2026')
  })

  it('falls back to the raw value for an unparseable input', () => {
    expect(formatDateTime('not-a-date')).toBe('not-a-date')
  })

  it('falls back to the default zone for an invalid timeZone', () => {
    expect(formatDateTime('2026-07-25T09:00:00Z', 'Not/AZone')).toContain('2026')
  })
})

describe('formatRange', () => {
  it('joins start and end with an en dash', () => {
    const out = formatRange('2026-07-25T09:00:00Z', '2026-07-25T10:00:00Z', 'UTC')
    expect(out).toContain('–')
  })
})
```

10. **Run — expect FAIL**: `npm test -- src/modules/shared/format.test.ts` → `Failed to load url ./format.ts`.

11. **Implement** `src/modules/shared/format.ts`:
```ts
export function formatDateTime(value: string | null | undefined, timeZone?: string): string {
  if (value == null) return '—'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) {
    return value
  }

  const options = { dateStyle: 'medium', timeStyle: 'short' } as const

  try {
    return new Intl.DateTimeFormat('ru-RU', { ...options, timeZone }).format(date)
  } catch {
    return new Intl.DateTimeFormat('ru-RU', options).format(date)
  }
}

export function formatRange(
  start: string | null | undefined,
  end: string | null | undefined,
  timeZone?: string,
): string {
  return `${formatDateTime(start, timeZone)} – ${formatDateTime(end, timeZone)}`
}
```

12. **Run — expect PASS**: `npm test -- src/modules/shared/format.test.ts` → `5 passed`.

13. **Write a temporary `src/App.tsx` stub** so the build has a root (replaced in Task 3):
```tsx
function App() {
  return <div className="card">event-organizer-frontend</div>
}

export default App
```

14. **Write `src/main.tsx`** (no Sentry, no global TimeZoneProvider; AuthProvider is added in Task 2 — for now render App directly):
```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ErrorBoundary } from './modules/shared/ErrorBoundary.tsx'
import 'events-design-system/styles.css'
import './index.css'
import App from './App.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <App />
    </ErrorBoundary>
  </StrictMode>,
)
```

15. **Typecheck + build** (this pulls in the whole `src` tree except `api.ts`, which is not imported until Task 2):
```bash
npm run build
```
Expected: `tsc -b` passes and `vite build` prints `✓ built in …` with no TS errors.

16. **Commit**:
```bash
git add event-organizer-frontend && git commit -m "feat(organizer-fe): scaffold + shared plumbing (runtimeEnv, routing, api, format, ErrorBoundary)"
```

---

## Task 2 — Auth module

sessionStorage token (`event_organizer_jwt`), `exp` decode, AuthProvider (drop expired at startup, client-side logout), `login()` (no TOTP), LoginPage (401 → «Неверный email или пароль»).

**Files**
- Create: `src/modules/auth/storage.ts`, `jwt.ts`, `types.ts`, `context.ts`, `AuthContext.tsx`, `useAuth.ts`, `authApi.ts`, `LoginPage.tsx`
- Create tests: `src/modules/auth/jwt.test.ts`, `src/modules/auth/LoginPage.test.tsx`

**Interfaces**
- Produces `getJwtToken(): string | null`, `setJwtToken(token: string): void`, `removeJwtToken(): void`
- Produces `decodeJwtPayload(token: string): JwtPayload | null`, `isTokenExpired(token: string): boolean`
- Produces `AuthContextValue = { isAuthenticated: boolean; jwtToken: string | null; loginWithToken: (token: string) => void; logout: () => void }`; `AuthProvider`, `useAuth()`
- Produces `login(payload: {email, password}): Promise<{access_token: string}>`
- Consumes `apiRequest` (Task 1)

### Steps

1. **`src/modules/auth/storage.ts`**:
```ts
const TOKEN_STORAGE_KEY = 'event_organizer_jwt'

// The JWT lives in sessionStorage (tab-scoped, dropped when the tab closes),
// narrowing the theft window. One-time cleanup of any token an older build may
// have left in localStorage.
localStorage.removeItem(TOKEN_STORAGE_KEY)

export function getJwtToken(): string | null {
  return sessionStorage.getItem(TOKEN_STORAGE_KEY)
}

export function setJwtToken(token: string): void {
  sessionStorage.setItem(TOKEN_STORAGE_KEY, token)
}

export function removeJwtToken(): void {
  sessionStorage.removeItem(TOKEN_STORAGE_KEY)
}
```

2. **Write the failing jwt test** — `src/modules/auth/jwt.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { decodeJwtPayload, isTokenExpired } from './jwt.ts'

function makeToken(payload: object): string {
  const base64 = btoa(JSON.stringify(payload)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '')
  return `header.${base64}.signature`
}

describe('decodeJwtPayload', () => {
  it('decodes a base64url payload', () => {
    const token = makeToken({ sub: 'a@b.c', exp: 123 })
    expect(decodeJwtPayload(token)).toEqual({ sub: 'a@b.c', exp: 123 })
  })

  it('returns null for a token without dots', () => {
    expect(decodeJwtPayload('not-a-jwt')).toBeNull()
  })

  it('returns null for garbage payloads', () => {
    expect(decodeJwtPayload('a.%%%%.c')).toBeNull()
    expect(decodeJwtPayload('')).toBeNull()
  })

  it('returns null for non-object payloads', () => {
    const base64 = btoa(JSON.stringify('just-a-string'))
    expect(decodeJwtPayload(`a.${base64}.c`)).toBeNull()
  })
})

describe('isTokenExpired', () => {
  it('is true when exp is in the past', () => {
    expect(isTokenExpired(makeToken({ exp: Math.floor(Date.now() / 1000) - 60 }))).toBe(true)
  })

  it('is false when exp is in the future', () => {
    expect(isTokenExpired(makeToken({ exp: Math.floor(Date.now() / 1000) + 3600 }))).toBe(false)
  })

  it('treats undecodable tokens as not expired', () => {
    expect(isTokenExpired('garbage')).toBe(false)
  })

  it('treats tokens without exp as not expired', () => {
    expect(isTokenExpired(makeToken({ sub: 'a@b.c' }))).toBe(false)
  })
})
```

3. **Run — expect FAIL**: `npm test -- src/modules/auth/jwt.test.ts` → `Failed to load url ./jwt.ts`.

4. **Implement** `src/modules/auth/jwt.ts`:
```ts
export type JwtPayload = {
  sub?: string
  exp?: number
}

/**
 * Decodes a JWT payload without verifying the signature (the BFF verifies; the
 * client only needs claims for UX like expiry + the sidebar identity). Handles
 * base64url and returns null for malformed tokens.
 */
export function decodeJwtPayload(token: string): JwtPayload | null {
  const part = token.split('.')[1]
  if (!part) return null
  const base64 = part.replace(/-/g, '+').replace(/_/g, '/')
  const padded = base64.padEnd(base64.length + ((4 - (base64.length % 4)) % 4), '=')
  try {
    const parsed: unknown = JSON.parse(atob(padded))
    if (typeof parsed !== 'object' || parsed === null) return null
    return parsed as JwtPayload
  } catch {
    return null
  }
}

/**
 * True when the token carries an `exp` claim already in the past. Undecodable
 * tokens or tokens without `exp` are treated as not expired: the BFF rejects
 * them with 401 and the apiRequest interceptor handles it.
 */
export function isTokenExpired(token: string): boolean {
  const payload = decodeJwtPayload(token)
  if (!payload || typeof payload.exp !== 'number') return false
  return payload.exp * 1000 <= Date.now()
}
```

5. **Run — expect PASS**: `npm test -- src/modules/auth/jwt.test.ts` → `9 passed`.

6. **`src/modules/auth/types.ts`**:
```ts
export type LoginPayload = {
  email: string
  password: string
}

export type LoginResponse = {
  access_token: string
}
```

7. **`src/modules/auth/context.ts`**:
```ts
import { createContext } from 'react'

export type AuthContextValue = {
  isAuthenticated: boolean
  jwtToken: string | null
  loginWithToken: (token: string) => void
  logout: () => void
}

export const AuthContext = createContext<AuthContextValue | null>(null)
```

8. **`src/modules/auth/useAuth.ts`**:
```ts
import { useContext } from 'react'
import { AuthContext, type AuthContextValue } from './context.ts'

export function useAuth(): AuthContextValue {
  const ctx = useContext(AuthContext)
  if (!ctx) {
    throw new Error('useAuth must be used within AuthProvider')
  }
  return ctx
}
```

9. **`src/modules/auth/authApi.ts`** (no logout endpoint, no TOTP):
```ts
import { apiRequest } from '../shared/api.ts'
import type { LoginPayload, LoginResponse } from './types.ts'

export async function login(payload: LoginPayload): Promise<LoginResponse> {
  return apiRequest<LoginResponse>('/auth/login', {
    method: 'POST',
    body: payload,
    auth: false,
  })
}
```

10. **`src/modules/auth/AuthContext.tsx`** (client-side logout only):
```tsx
import { useCallback, useMemo, useState, type ReactNode } from 'react'
import { AuthContext, type AuthContextValue } from './context.ts'
import { isTokenExpired } from './jwt.ts'
import { getJwtToken, removeJwtToken, setJwtToken } from './storage.ts'

type AuthProviderProps = {
  children: ReactNode
}

function getValidStoredToken(): string | null {
  const token = getJwtToken()
  if (token && isTokenExpired(token)) {
    removeJwtToken()
    return null
  }
  return token
}

export function AuthProvider({ children }: AuthProviderProps) {
  const [jwtToken, setJwtTokenState] = useState<string | null>(() => getValidStoredToken())

  const loginWithToken = useCallback((token: string) => {
    setJwtToken(token)
    setJwtTokenState(token)
  }, [])

  // The BFF has no logout endpoint — logout is purely local: clear storage + state.
  const logout = useCallback(() => {
    removeJwtToken()
    setJwtTokenState(null)
  }, [])

  const value = useMemo<AuthContextValue>(
    () => ({
      isAuthenticated: Boolean(jwtToken),
      jwtToken,
      loginWithToken,
      logout,
    }),
    [jwtToken, loginWithToken, logout],
  )

  return <AuthContext.Provider value={value}>{children}</AuthContext.Provider>
}
```

11. **`src/modules/auth/LoginPage.tsx`** (email + password, no TOTP, no dev bypass):
```tsx
import { type FormEvent, useMemo, useState } from 'react'
import { ApiError } from '../shared/api.ts'
import { navigateTo } from '../shared/routing.ts'
import { login } from './authApi.ts'
import { useAuth } from './useAuth.ts'

function translateLoginError(err: unknown): string {
  if (!(err instanceof ApiError)) return 'Не удалось выполнить вход'
  if (err.status === 401) return 'Неверный email или пароль'
  return err.message
}

export function LoginPage() {
  const { loginWithToken } = useAuth()
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const canSubmit = useMemo(
    () => email.trim().length > 0 && password.trim().length > 0,
    [email, password],
  )

  async function handleLogin(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    if (!canSubmit) return

    setError(null)
    setLoading(true)
    try {
      const response = await login({ email: email.trim(), password })
      loginWithToken(response.access_token)
      navigateTo('/', { replace: true })
    } catch (err) {
      setError(translateLoginError(err))
    } finally {
      setLoading(false)
    }
  }

  return (
    <main className="login-shell">
      <section className="login-split">
        <aside className="login-brand">
          <div className="login-brand-dots" />
          <div className="login-brand-logo">
            <div className="app-logo">EO</div>
            <span>Кабинет организатора</span>
          </div>
          <div>
            <h1>Ваше расписание<br />и встречи</h1>
            <p>Управляйте доступностью, бронями и профилем в одном месте.</p>
          </div>
          <div className="login-brand-foot">Сессия защищена · вход по паролю</div>
        </aside>

        <div className="login-form-panel">
          <div>
            <p className="eyebrow">Вход в кабинет</p>
            <h1>С возвращением</h1>
          </div>

          <form className="form" onSubmit={handleLogin}>
            <label className="field">
              <span>Email</span>
              <input
                type="email"
                name="email"
                autoComplete="username"
                placeholder="organizer@example.com"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                required
              />
            </label>

            <label className="field">
              <span>Пароль</span>
              <input
                type="password"
                name="password"
                autoComplete="current-password"
                placeholder="••••••••"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                required
              />
            </label>

            <div className="inline-actions">
              <button type="submit" disabled={loading || !canSubmit}>
                {loading ? 'Входим…' : 'Войти'}
              </button>
            </div>
          </form>

          {error && <p className="error-text">{error}</p>}
        </div>
      </section>
    </main>
  )
}
```

12. **Write the failing LoginPage test** — `src/modules/auth/LoginPage.test.tsx` (this is the canonical test STYLE for the whole package: `createRoot` + `act`, native `setInput`, mocked module):
```tsx
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { LoginPage } from './LoginPage.tsx'
import { AuthProvider } from './AuthContext.tsx'
import { ApiError } from '../shared/api.ts'
import * as authApi from './authApi.ts'

let container: HTMLDivElement
let root: Root

async function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () =>
    root.render(
      <AuthProvider>
        <LoginPage />
      </AuthProvider>,
    ),
  )
}

beforeEach(() => {
  sessionStorage.clear()
})

afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

function setInput(sel: string, value: string) {
  const el = container.querySelector(sel) as HTMLInputElement
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('LoginPage', () => {
  it('logs in and stores the token', async () => {
    const spy = vi.spyOn(authApi, 'login').mockResolvedValue({ access_token: 'tok.123' })
    await mount()
    setInput('input[name="email"]', 'organizer@example.com')
    setInput('input[name="password"]', 'secret')
    await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
    expect(spy).toHaveBeenCalledWith({ email: 'organizer@example.com', password: 'secret' })
    expect(sessionStorage.getItem('event_organizer_jwt')).toBe('tok.123')
  })

  it('shows the Russian message on 401', async () => {
    vi.spyOn(authApi, 'login').mockRejectedValue(new ApiError('bad', 401, null))
    await mount()
    setInput('input[name="email"]', 'x@y.z')
    setInput('input[name="password"]', 'wrong')
    await act(async () => (container.querySelector('form') as HTMLFormElement).requestSubmit())
    expect(container.querySelector('.error-text')?.textContent).toBe('Неверный email или пароль')
    expect(sessionStorage.getItem('event_organizer_jwt')).toBeNull()
  })
})
```

13. **Run — expect PASS** (all auth files exist now):
```bash
npm test -- src/modules/auth/
```
Expected: `jwt.test.ts` + `LoginPage.test.tsx` → `11 passed`.

14. **Commit**:
```bash
git add event-organizer-frontend/src/modules/auth && git commit -m "feat(organizer-fe): auth module (storage, jwt, AuthProvider, login, LoginPage)"
```

---

## Task 3 — App shell + routing wiring

Wire `AuthProvider` into `main.tsx`, replace the `App.tsx` stub with real routing + redirects, and build the `OrganizerLayout` sidebar (Расписание / Брони / Профиль + logout).

**Files**
- Modify: `src/main.tsx`, `src/App.tsx`
- Create: `src/modules/app/OrganizerLayout.tsx`
- Create test: `src/modules/app/OrganizerLayout.test.tsx`

**Interfaces**
- Produces `OrganizerLayout({pathname, children})`
- Consumes `useAuth`, `navigateTo`, `parseRoute`, `decodeJwtPayload`

### Steps

1. **Update `src/main.tsx`** to wrap the app in `AuthProvider`:
```tsx
import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { ErrorBoundary } from './modules/shared/ErrorBoundary.tsx'
import 'events-design-system/styles.css'
import './index.css'
import App from './App.tsx'
import { AuthProvider } from './modules/auth/AuthContext.tsx'

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <ErrorBoundary>
      <AuthProvider>
        <App />
      </AuthProvider>
    </ErrorBoundary>
  </StrictMode>,
)
```

2. **`src/modules/app/OrganizerLayout.tsx`**:
```tsx
import type { ReactNode } from 'react'
import { Icon, type IconName } from 'events-design-system'
import { useAuth } from '../auth/useAuth.ts'
import { decodeJwtPayload } from '../auth/jwt.ts'
import { navigateTo } from '../shared/routing.ts'

type OrganizerLayoutProps = {
  pathname: string
  children: ReactNode
}

type NavItem = {
  label: string
  path: string
  icon: IconName
  match: (pathname: string) => boolean
}

const NAV_ITEMS: NavItem[] = [
  {
    label: 'Расписание',
    path: '/',
    icon: 'bookings',
    match: (pathname) => pathname === '/' || pathname === '/schedule',
  },
  {
    label: 'Брони',
    path: '/bookings',
    icon: 'dashboard',
    match: (pathname) => pathname === '/bookings',
  },
  {
    label: 'Профиль',
    path: '/profile',
    icon: 'users',
    match: (pathname) => pathname === '/profile',
  },
]

function sidebarIdentity(jwtToken: string | null): { name: string; email: string | null; initials: string } {
  const sub = jwtToken ? decodeJwtPayload(jwtToken)?.sub ?? null : null
  const email = sub && sub.includes('@') ? sub : null
  const name = email ? email.split('@')[0] : sub ?? 'Организатор'
  const initials = name.slice(0, 2).toUpperCase()
  return { name, email, initials }
}

export function OrganizerLayout({ pathname, children }: OrganizerLayoutProps) {
  const { logout, jwtToken } = useAuth()
  const identity = sidebarIdentity(jwtToken)

  function handleLogout() {
    logout()
    navigateTo('/login', { replace: true })
  }

  return (
    <div className="admin-shell org-shell">
      <aside className="app-sidebar">
        <div className="app-brand">
          <div className="app-logo">EO</div>
          <div>
            <div className="app-brand-name">Кабинет организатора</div>
          </div>
        </div>

        <nav className="app-nav">
          {NAV_ITEMS.map((item) => {
            const active = item.match(pathname)
            return (
              <button
                key={item.path}
                type="button"
                className={`app-nav-item${active ? ' is-active' : ''}`}
                aria-current={active ? 'page' : undefined}
                onClick={() => navigateTo(item.path)}
              >
                <span className="app-nav-icon">
                  <Icon name={item.icon} />
                </span>
                <span>{item.label}</span>
              </button>
            )
          })}
        </nav>

        <div className="app-user">
          <div className="app-user-avatar">{identity.initials}</div>
          <div className="app-user-meta">
            <div className="app-user-name">{identity.name}</div>
            {identity.email && <div className="app-user-email">{identity.email}</div>}
          </div>
          <button type="button" className="app-logout" title="Выйти" aria-label="Выйти" onClick={handleLogout}>
            <Icon name="logout" size={15} />
          </button>
        </div>
      </aside>

      <main className="content org-content">{children}</main>
    </div>
  )
}
```
> `IconName` values `bookings`/`dashboard`/`users`/`logout` are the same names admin-fe uses against `events-design-system`. If `tsc` reports one is not a valid `IconName`, substitute the nearest valid name from the DS `Icon` union (the build in step 6 will catch it).

3. **Replace `src/App.tsx`** with real routing + redirects:
```tsx
import { useEffect, useMemo, useState } from 'react'
import { OrganizerLayout } from './modules/app/OrganizerLayout.tsx'
import { LoginPage } from './modules/auth/LoginPage.tsx'
import { useAuth } from './modules/auth/useAuth.ts'
import { SchedulePage } from './modules/schedule/SchedulePage.tsx'
import { BookingsPage } from './modules/bookings/BookingsPage.tsx'
import { ProfilePage } from './modules/profile/ProfilePage.tsx'
import { navigateTo, parseRoute } from './modules/shared/routing.ts'

function App() {
  const { isAuthenticated } = useAuth()
  const [pathname, setPathname] = useState(window.location.pathname)

  useEffect(() => {
    const syncPath = () => setPathname(window.location.pathname)
    window.addEventListener('popstate', syncPath)
    window.addEventListener('app:navigate', syncPath)
    return () => {
      window.removeEventListener('popstate', syncPath)
      window.removeEventListener('app:navigate', syncPath)
    }
  }, [])

  const route = useMemo(() => parseRoute(pathname), [pathname])

  useEffect(() => {
    if (!isAuthenticated && route.name !== 'login') {
      navigateTo('/login', { replace: true })
      return
    }
    if (isAuthenticated && route.name === 'login') {
      navigateTo('/', { replace: true })
    }
  }, [isAuthenticated, route.name])

  if (route.name === 'login') {
    return <LoginPage />
  }

  return (
    <OrganizerLayout pathname={pathname}>
      {route.name === 'schedule' && <SchedulePage />}
      {route.name === 'bookings' && <BookingsPage />}
      {route.name === 'profile' && <ProfilePage />}
      {route.name === 'not-found' && (
        <div className="card">
          <h2>Страница не найдена</h2>
          <p>
            Адрес <code>{pathname}</code> не существует.
          </p>
          <button type="button" onClick={() => navigateTo('/', { replace: true })}>
            Вернуться к расписанию
          </button>
        </div>
      )}
    </OrganizerLayout>
  )
}

export default App
```
> `App.tsx` now imports `SchedulePage`/`BookingsPage`/`ProfilePage`, created in Tasks 10/11/12. To keep this task self-contained and buildable, create **temporary stubs** now and replace them later:
> - `src/modules/schedule/SchedulePage.tsx`: `export function SchedulePage() { return <div className="card">Расписание</div> }`
> - `src/modules/bookings/BookingsPage.tsx`: `export function BookingsPage() { return <div className="card">Брони</div> }`
> - `src/modules/profile/ProfilePage.tsx`: `export function ProfilePage() { return <div className="card">Профиль</div> }`

4. **Write the failing layout test** — `src/modules/app/OrganizerLayout.test.tsx`:
```tsx
import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { OrganizerLayout } from './OrganizerLayout.tsx'
import { AuthProvider } from '../auth/AuthContext.tsx'

let container: HTMLDivElement
let root: Root

async function mount(pathname: string) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () =>
    root.render(
      <AuthProvider>
        <OrganizerLayout pathname={pathname}>
          <div className="probe">child</div>
        </OrganizerLayout>
      </AuthProvider>,
    ),
  )
}

beforeEach(() => sessionStorage.clear())
afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

describe('OrganizerLayout', () => {
  it('renders the three nav items and children', async () => {
    await mount('/')
    const labels = [...container.querySelectorAll('.app-nav-item')].map((b) => b.textContent)
    expect(labels).toEqual(['Расписание', 'Брони', 'Профиль'])
    expect(container.querySelector('.probe')?.textContent).toBe('child')
  })

  it('marks the active item by pathname', async () => {
    await mount('/bookings')
    const active = container.querySelector('.app-nav-item.is-active')
    expect(active?.textContent).toBe('Брони')
  })

  it('logout clears the session and navigates to /login', async () => {
    sessionStorage.setItem('event_organizer_jwt', 'tok')
    await mount('/')
    await act(async () => (container.querySelector('.app-logout') as HTMLButtonElement).click())
    expect(sessionStorage.getItem('event_organizer_jwt')).toBeNull()
    expect(window.location.pathname).toBe('/login')
  })
})
```

5. **Run — expect PASS**: `npm test -- src/modules/app/` → `3 passed`.

6. **Typecheck**: `npm run build` → passes.

7. **Commit**:
```bash
git add event-organizer-frontend/src && git commit -m "feat(organizer-fe): app shell + routing (OrganizerLayout, redirects, page stubs)"
```

---

## Task 4 — shared/TimeZoneField + timezones

Mirror booker's searchable, portaled timezone combobox on the DS `.tz-picker-input`/`.tz-dropdown`/`.tz-option` classes.

**Files**
- Create: `src/modules/shared/timezones.ts`, `src/modules/shared/TimeZoneField.tsx`
- Create test: `src/modules/shared/TimeZoneField.test.tsx`

**Interfaces**
- Produces `listTimeZones(): TimeZoneOption[]`, `timeZoneLabel(id: string): string`
- Produces `TimeZoneField({value, onChange?})`

### Steps

1. **`src/modules/shared/timezones.ts`** — paste verbatim from `event-booker-frontend/src/modules/booking/timezones.ts` (full IANA list, RU labels, offset sort). It is self-contained (no imports). Copy the entire file content:
```ts
// Full IANA time-zone list with Russian, offset-prefixed labels. Built once
// from the platform's Intl data so it stays complete and localized.

export type TimeZoneOption = { id: string; label: string }

const FALLBACK_IDS = ['UTC', 'Europe/Kaliningrad', 'Europe/Moscow', 'Asia/Yekaterinburg', 'Asia/Novosibirsk']

const RU_CITY: Record<string, string> = {
  'Europe/Kaliningrad': 'Калининград',
  'Europe/Moscow': 'Москва',
  'Europe/Simferopol': 'Симферополь',
  'Europe/Volgograd': 'Волгоград',
  'Europe/Kirov': 'Киров',
  'Europe/Astrakhan': 'Астрахань',
  'Europe/Saratov': 'Саратов',
  'Europe/Ulyanovsk': 'Ульяновск',
  'Europe/Samara': 'Самара',
  'Asia/Yekaterinburg': 'Екатеринбург',
  'Asia/Omsk': 'Омск',
  'Asia/Novosibirsk': 'Новосибирск',
  'Asia/Barnaul': 'Барнаул',
  'Asia/Tomsk': 'Томск',
  'Asia/Novokuznetsk': 'Новокузнецк',
  'Asia/Krasnoyarsk': 'Красноярск',
  'Asia/Irkutsk': 'Иркутск',
  'Asia/Chita': 'Чита',
  'Asia/Yakutsk': 'Якутск',
  'Asia/Khandyga': 'Хандыга',
  'Asia/Vladivostok': 'Владивосток',
  'Asia/Ust-Nera': 'Усть-Нера',
  'Asia/Magadan': 'Магадан',
  'Asia/Sakhalin': 'Южно-Сахалинск',
  'Asia/Srednekolymsk': 'Среднеколымск',
  'Asia/Kamchatka': 'Петропавловск-Камчатский',
  'Asia/Anadyr': 'Анадырь',
  'Europe/Kyiv': 'Киев',
  'Europe/Minsk': 'Минск',
  'Europe/Chisinau': 'Кишинёв',
  'Asia/Baku': 'Баку',
  'Asia/Yerevan': 'Ереван',
  'Asia/Tbilisi': 'Тбилиси',
  'Asia/Almaty': 'Алматы',
  'Asia/Tashkent': 'Ташкент',
  'Asia/Bishkek': 'Бишкек',
  'Asia/Ashgabat': 'Ашхабад',
  'Asia/Dushanbe': 'Душанбе',
  'Europe/London': 'Лондон',
  'Europe/Paris': 'Париж',
  'Europe/Berlin': 'Берлин',
  'Europe/Rome': 'Рим',
  'Europe/Madrid': 'Мадрид',
  'Europe/Amsterdam': 'Амстердам',
  'Europe/Istanbul': 'Стамбул',
  'Asia/Dubai': 'Дубай',
  'Asia/Jerusalem': 'Иерусалим',
  'Asia/Bangkok': 'Бангкок',
  'Asia/Shanghai': 'Пекин',
  'Asia/Tokyo': 'Токио',
  'America/New_York': 'Нью-Йорк',
  'America/Los_Angeles': 'Лос-Анджелес',
}

function offsetString(id: string): string {
  try {
    const parts = new Intl.DateTimeFormat('en-US', { timeZone: id, timeZoneName: 'longOffset' }).formatToParts(new Date())
    const value = parts.find((p) => p.type === 'timeZoneName')?.value ?? ''
    return value.replace('GMT', '')
  } catch {
    return ''
  }
}

function offsetMinutes(off: string): number {
  const m = /([+-])(\d{2}):(\d{2})/.exec(off)
  if (!m) return 0
  const sign = m[1] === '-' ? -1 : 1
  return sign * (Number(m[2]) * 60 + Number(m[3]))
}

function russianName(id: string): string {
  try {
    const parts = new Intl.DateTimeFormat('ru-RU', { timeZone: id, timeZoneName: 'long' }).formatToParts(new Date())
    const value = parts.find((p) => p.type === 'timeZoneName')?.value
    if (value) return value.replace(/,\s*(стандартное|летнее)\s+время$/i, '')
  } catch {
    // fall through to the id-derived name
  }
  return cityFromId(id)
}

function cityFromId(id: string): string {
  return id.split('/').pop()?.replace(/_/g, ' ') ?? id
}

export function timeZoneLabel(id: string): string {
  return RU_CITY[id] ?? withCityHint(id)
}

function withCityHint(id: string): string {
  const name = russianName(id)
  const city = cityFromId(id)
  return name.toLowerCase() === city.toLowerCase() ? name : `${name} · ${city}`
}

function allZoneIds(): string[] {
  try {
    const supported = (Intl as unknown as { supportedValuesOf?: (k: string) => string[] }).supportedValuesOf
    if (typeof supported === 'function') {
      const list = supported('timeZone')
      if (Array.isArray(list) && list.length > 0) return list
    }
  } catch {
    // fall through to the curated fallback
  }
  return FALLBACK_IDS
}

let cache: TimeZoneOption[] | null = null

export function listTimeZones(): TimeZoneOption[] {
  if (cache) return cache
  const ranked = allZoneIds().map((id) => {
    const off = offsetString(id)
    return { id, label: timeZoneLabel(id), rank: offsetMinutes(off) }
  })
  ranked.sort((a, b) => a.rank - b.rank || a.label.localeCompare(b.label, 'ru'))
  cache = ranked.map(({ id, label }) => ({ id, label }))
  return cache
}
```

2. **`src/modules/shared/TimeZoneField.tsx`** — paste verbatim from `event-booker-frontend/src/modules/booking/TimeZoneField.tsx`, changing only the `./timezones.ts` import path (same directory here, so it is unchanged):
```tsx
import { useEffect, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent } from 'react'
import { createPortal } from 'react-dom'
import { listTimeZones, timeZoneLabel } from './timezones.ts'

type Props = {
  value: string
  onChange?: (id: string) => void
}

export function TimeZoneField({ value, onChange }: Props) {
  if (!onChange) return <TimeZoneReadonly value={value} />
  return <TimeZoneCombo value={value} onChange={onChange} />
}

function TimeZoneReadonly({ value }: { value: string }) {
  const label = useMemo(() => timeZoneLabel(value), [value])
  return <div className="tz-readonly">{label}</div>
}

function TimeZoneCombo({ value, onChange }: { value: string; onChange: (id: string) => void }) {
  const zones = useMemo(() => listTimeZones(), [])
  const currentLabel = useMemo(() => timeZoneLabel(value), [value])
  const [open, setOpen] = useState(false)
  const [query, setQuery] = useState('')
  const [active, setActive] = useState(0)
  const [rect, setRect] = useState<{ left: number; top: number; width: number } | null>(null)
  const inputRef = useRef<HTMLInputElement>(null)
  const listRef = useRef<HTMLUListElement>(null)

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase()
    if (!q) return zones
    return zones.filter((z) => z.label.toLowerCase().includes(q))
  }, [zones, query])

  function place() {
    const el = inputRef.current
    if (!el) return
    const r = el.getBoundingClientRect()
    setRect({ left: r.left, top: r.bottom + 4, width: Math.max(r.width, 240) })
  }

  function openList() {
    place()
    setQuery('')
    const idx = zones.findIndex((z) => z.id === value)
    setActive(idx < 0 ? 0 : idx)
    setOpen(true)
  }

  function choose(id: string) {
    onChange(id)
    setOpen(false)
    setQuery('')
    inputRef.current?.blur()
  }

  useEffect(() => {
    if (!open) return
    const onDown = (e: MouseEvent) => {
      const t = e.target as Node
      if (inputRef.current?.contains(t) || listRef.current?.contains(t)) return
      setOpen(false)
    }
    const onScroll = (e: Event) => {
      if (listRef.current?.contains(e.target as Node)) return
      setOpen(false)
    }
    const onResize = () => setOpen(false)
    document.addEventListener('mousedown', onDown)
    window.addEventListener('scroll', onScroll, true)
    window.addEventListener('resize', onResize)
    return () => {
      document.removeEventListener('mousedown', onDown)
      window.removeEventListener('scroll', onScroll, true)
      window.removeEventListener('resize', onResize)
    }
  }, [open])

  useEffect(() => {
    if (!open) return
    const ul = listRef.current
    const el = ul?.querySelector<HTMLElement>('.tz-option.is-active')
    if (ul && el) ul.scrollTop = el.offsetTop - ul.clientHeight / 2 + el.clientHeight / 2
  }, [active, open])

  function onKeyDown(e: ReactKeyboardEvent<HTMLInputElement>) {
    if (e.key === 'ArrowDown') {
      e.preventDefault()
      if (!open) {
        openList()
        return
      }
      setActive((i) => Math.min(i + 1, filtered.length - 1))
      return
    }
    if (e.key === 'ArrowUp') {
      e.preventDefault()
      setActive((i) => Math.max(i - 1, 0))
      return
    }
    if (e.key === 'Enter') {
      e.preventDefault()
      const opt = filtered[active]
      if (open && opt) choose(opt.id)
      return
    }
    if (e.key === 'Escape') setOpen(false)
  }

  return (
    <div className="tz-field">
      <input
        ref={inputRef}
        className="tz-picker-input"
        role="combobox"
        aria-expanded={open}
        aria-label="Часовой пояс"
        placeholder="Начните вводить город…"
        value={open ? query : currentLabel}
        onFocus={openList}
        onChange={(e) => {
          setQuery(e.target.value)
          setActive(0)
          place()
          setOpen(true)
        }}
        onKeyDown={onKeyDown}
      />
      {open &&
        rect &&
        createPortal(
          <ul
            ref={listRef}
            className="tz-dropdown"
            role="listbox"
            style={{ position: 'fixed', left: rect.left, top: rect.top, width: rect.width, zIndex: 60 }}
          >
            {filtered.length === 0 && <li className="tz-option-empty">Ничего не найдено</li>}
            {filtered.map((z, i) => (
              <li
                key={z.id}
                role="option"
                aria-selected={z.id === value}
                className={`tz-option${i === active ? ' is-active' : ''}`}
                onMouseEnter={() => setActive(i)}
                onMouseDown={(e) => {
                  e.preventDefault()
                  choose(z.id)
                }}
              >
                {z.label}
              </li>
            ))}
          </ul>,
          document.body,
        )}
    </div>
  )
}
```
> Add `.tz-field { display: block; }` and the `.tz-readonly` rule to `src/index.css` (append the two rules booker defines) so the wrapper is styled.

3. **Write the smoke test** — `src/modules/shared/TimeZoneField.test.tsx`:
```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { TimeZoneField } from './TimeZoneField.tsx'

let container: HTMLDivElement
let root: Root

async function mount(node: React.ReactNode) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(node))
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
})

describe('TimeZoneField', () => {
  it('renders read-only text when no onChange is given', async () => {
    await mount(<TimeZoneField value="Europe/Moscow" />)
    expect(container.querySelector('.tz-readonly')?.textContent).toBe('Москва')
  })

  it('opens the portaled dropdown on focus and selects an option', async () => {
    const onChange = vi.fn()
    await mount(<TimeZoneField value="Europe/Moscow" onChange={onChange} />)
    const input = container.querySelector('.tz-picker-input') as HTMLInputElement
    await act(async () => input.focus())
    const option = document.body.querySelector('.tz-option') as HTMLLIElement
    expect(option).toBeTruthy()
    await act(async () => option.dispatchEvent(new MouseEvent('mousedown', { bubbles: true })))
    expect(onChange).toHaveBeenCalled()
  })
})
```

4. **Run — expect PASS**: `npm test -- src/modules/shared/TimeZoneField.test.tsx` → `2 passed`.

5. **Commit**:
```bash
git add event-organizer-frontend/src/modules/shared && git commit -m "feat(organizer-fe): shared TimeZoneField + timezones (portaled combobox)"
```

---

## Task 5 — schedule types + scheduleApi

Types for the bundle/weekly/override/travel and the three API calls (`getSchedule` 404-aware, `putSchedule`, `putTravel`).

**Files**
- Create: `src/modules/schedule/types.ts`, `src/modules/schedule/scheduleApi.ts`
- Create test: `src/modules/schedule/scheduleApi.test.ts`

**Interfaces**
- Produces types `WeeklyHour`, `DateOverride`, `Travel`, `ScheduleMeta`, `ScheduleBundle`, `UpsertScheduleBody`, `TravelBody`
- Produces `getSchedule(): Promise<ScheduleBundle | null>` (404 → null), `putSchedule(body: UpsertScheduleBody): Promise<ScheduleBundle>`, `putTravel(body: TravelBody): Promise<unknown>`
- Consumes `apiRequest`, `ApiError`

### Steps

1. **`src/modules/schedule/types.ts`**:
```ts
// day_of_week: 1=Mon..7=Sun (ISO). Times as "HH:MM" on write; the BFF returns
// "HH:MM:SS" on read — the editor normalises to "HH:MM".
export type WeeklyHour = { day_of_week: number; start_time: string; end_time: string }
export type DateOverride = { date: string; start_time: string | null; end_time: string | null }
export type Travel = {
  time_zone: string
  start_date: string
  end_date: string | null
  prev_time_zone: string | null
}
export type ScheduleMeta = { id: string; owner_user_id: string; name: string; time_zone: string }

export type ScheduleBundle = {
  schedule: ScheduleMeta
  weekly_hours: WeeklyHour[]
  date_overrides: DateOverride[]
  travel_schedules: Travel[]
}

export type UpsertScheduleBody = {
  name: string
  time_zone: string
  weekly_hours: WeeklyHour[]
  date_overrides: DateOverride[]
}

export type TravelBody = { travel_schedules: Travel[] }
```

2. **Write the failing api test** — `src/modules/schedule/scheduleApi.test.ts`:
```ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../shared/api.ts'
import { ApiError } from '../shared/api.ts'
import { getSchedule, putSchedule, putTravel } from './scheduleApi.ts'

afterEach(() => vi.restoreAllMocks())

describe('scheduleApi', () => {
  it('getSchedule returns the bundle', async () => {
    const bundle = { schedule: { id: '1', owner_user_id: '2', name: 'N', time_zone: 'UTC' }, weekly_hours: [], date_overrides: [], travel_schedules: [] }
    vi.spyOn(api, 'apiRequest').mockResolvedValue(bundle)
    await expect(getSchedule()).resolves.toEqual(bundle)
  })

  it('getSchedule returns null on 404', async () => {
    vi.spyOn(api, 'apiRequest').mockRejectedValue(new ApiError('nope', 404, null))
    await expect(getSchedule()).resolves.toBeNull()
  })

  it('getSchedule rethrows non-404 errors', async () => {
    vi.spyOn(api, 'apiRequest').mockRejectedValue(new ApiError('boom', 502, null))
    await expect(getSchedule()).rejects.toBeInstanceOf(ApiError)
  })

  it('putSchedule PUTs the body', async () => {
    const spy = vi.spyOn(api, 'apiRequest').mockResolvedValue({})
    const body = { name: 'N', time_zone: 'UTC', weekly_hours: [], date_overrides: [] }
    await putSchedule(body)
    expect(spy).toHaveBeenCalledWith('/api/me/schedule', { method: 'PUT', body })
  })

  it('putTravel PUTs the travel envelope', async () => {
    const spy = vi.spyOn(api, 'apiRequest').mockResolvedValue({})
    const body = { travel_schedules: [] }
    await putTravel(body)
    expect(spy).toHaveBeenCalledWith('/api/me/schedule/travel', { method: 'PUT', body })
  })
})
```

3. **Run — expect FAIL**: `npm test -- src/modules/schedule/scheduleApi.test.ts` → `Failed to load url ./scheduleApi.ts`.

4. **Implement** `src/modules/schedule/scheduleApi.ts`:
```ts
import { ApiError, apiRequest } from '../shared/api.ts'
import type { ScheduleBundle, TravelBody, UpsertScheduleBody } from './types.ts'

// 404 = the organizer has no schedule yet → empty editor (not an error).
export async function getSchedule(): Promise<ScheduleBundle | null> {
  try {
    return await apiRequest<ScheduleBundle>('/api/me/schedule')
  } catch (err) {
    if (err instanceof ApiError && err.status === 404) return null
    throw err
  }
}

export async function putSchedule(body: UpsertScheduleBody): Promise<ScheduleBundle> {
  return apiRequest<ScheduleBundle>('/api/me/schedule', { method: 'PUT', body })
}

export async function putTravel(body: TravelBody): Promise<unknown> {
  return apiRequest<unknown>('/api/me/schedule/travel', { method: 'PUT', body })
}
```

5. **Run — expect PASS**: `npm test -- src/modules/schedule/scheduleApi.test.ts` → `5 passed`.

6. **Commit**:
```bash
git add event-organizer-frontend/src/modules/schedule && git commit -m "feat(organizer-fe): schedule types + scheduleApi (404-aware get, upsert, travel)"
```

---

## Task 6 — schedule.ts pure helpers + tests

Bundle⇄editor-state mapping, `buildUpsert`, `buildTravel`, `validate` (per-day interval overlap, `start<end`, valid IANA tz). Table-driven tests.

**Files**
- Create: `src/modules/schedule/schedule.ts`
- Create test: `src/modules/schedule/schedule.test.ts`

**Interfaces**
- Produces `type Interval = { start: string; end: string }`, `type DayState = { enabled: boolean; intervals: Interval[] }`, `type OverrideState = { date: string; fullDay: boolean; start: string; end: string }`, `type TravelState = { start_date: string; end_date: string; time_zone: string }`, `type EditorState = { name: string; timeZone: string; days: DayState[]; overrides: OverrideState[]; travels: TravelState[] }`
- Produces `bundleToState(bundle: ScheduleBundle | null, defaultTz: string): EditorState`
- Produces `buildUpsert(state: EditorState): UpsertScheduleBody`
- Produces `buildTravel(state: EditorState): TravelBody`
- Produces `validate(state: EditorState): string[]`
- Produces `DAY_LABELS: string[]` (Пн…Вс, index 0..6)

### Steps

1. **Write the failing helper test** — `src/modules/schedule/schedule.test.ts`:
```ts
import { describe, expect, it } from 'vitest'
import { bundleToState, buildUpsert, buildTravel, validate, emptyDays } from './schedule.ts'
import type { ScheduleBundle } from './types.ts'

const bundle: ScheduleBundle = {
  schedule: { id: '1', owner_user_id: '2', name: 'Моё', time_zone: 'Europe/Moscow' },
  weekly_hours: [
    { day_of_week: 1, start_time: '09:00:00', end_time: '12:00:00' },
    { day_of_week: 1, start_time: '14:00:00', end_time: '18:00:00' },
    { day_of_week: 2, start_time: '09:00:00', end_time: '18:00:00' },
  ],
  date_overrides: [
    { date: '2026-07-25', start_time: '10:00:00', end_time: '14:00:00' },
    { date: '2026-07-26', start_time: null, end_time: null },
  ],
  travel_schedules: [
    { time_zone: 'Asia/Dubai', start_date: '2026-08-01', end_date: '2026-08-10', prev_time_zone: 'Europe/Moscow' },
  ],
}

describe('bundleToState', () => {
  it('maps a null bundle to an empty editor with the default tz', () => {
    const s = bundleToState(null, 'UTC')
    expect(s.timeZone).toBe('UTC')
    expect(s.name).toBe('Моё расписание')
    expect(s.days).toHaveLength(7)
    expect(s.days.every((d) => !d.enabled && d.intervals.length === 0)).toBe(true)
    expect(s.overrides).toEqual([])
    expect(s.travels).toEqual([])
  })

  it('groups weekly hours by day and normalises HH:MM', () => {
    const s = bundleToState(bundle, 'UTC')
    expect(s.timeZone).toBe('Europe/Moscow')
    expect(s.name).toBe('Моё')
    expect(s.days[0]).toEqual({ enabled: true, intervals: [{ start: '09:00', end: '12:00' }, { start: '14:00', end: '18:00' }] })
    expect(s.days[1]).toEqual({ enabled: true, intervals: [{ start: '09:00', end: '18:00' }] })
    expect(s.days[2].enabled).toBe(false)
  })

  it('maps overrides incl. the full-day block', () => {
    const s = bundleToState(bundle, 'UTC')
    expect(s.overrides[0]).toEqual({ date: '2026-07-25', fullDay: false, start: '10:00', end: '14:00' })
    expect(s.overrides[1]).toEqual({ date: '2026-07-26', fullDay: true, start: '', end: '' })
  })

  it('maps travel rows', () => {
    const s = bundleToState(bundle, 'UTC')
    expect(s.travels[0]).toEqual({ start_date: '2026-08-01', end_date: '2026-08-10', time_zone: 'Asia/Dubai' })
  })
})

describe('buildUpsert', () => {
  it('emits weekly_hours only for enabled days and full-day override nulls', () => {
    const s = bundleToState(bundle, 'UTC')
    const body = buildUpsert(s)
    expect(body).toEqual({
      name: 'Моё',
      time_zone: 'Europe/Moscow',
      weekly_hours: [
        { day_of_week: 1, start_time: '09:00', end_time: '12:00' },
        { day_of_week: 1, start_time: '14:00', end_time: '18:00' },
        { day_of_week: 2, start_time: '09:00', end_time: '18:00' },
      ],
      date_overrides: [
        { date: '2026-07-25', start_time: '10:00', end_time: '14:00' },
        { date: '2026-07-26', start_time: null, end_time: null },
      ],
    })
  })

  it('drops intervals of a disabled day', () => {
    const s = bundleToState(null, 'UTC')
    s.days[0] = { enabled: false, intervals: [{ start: '09:00', end: '10:00' }] }
    expect(buildUpsert(s).weekly_hours).toEqual([])
  })
})

describe('buildTravel', () => {
  it('wraps rows in the travel_schedules envelope, prev_time_zone = base tz, empty end → null', () => {
    const s = bundleToState(null, 'Europe/Moscow')
    s.travels = [{ start_date: '2026-08-01', end_date: '', time_zone: 'Asia/Dubai' }]
    expect(buildTravel(s)).toEqual({
      travel_schedules: [
        { time_zone: 'Asia/Dubai', start_date: '2026-08-01', end_date: null, prev_time_zone: 'Europe/Moscow' },
      ],
    })
  })
})

describe('validate', () => {
  const base = () => {
    const s = bundleToState(null, 'Europe/Moscow')
    s.days[0] = { enabled: true, intervals: [{ start: '09:00', end: '12:00' }] }
    return s
  }

  it('passes a valid state', () => {
    expect(validate(base())).toEqual([])
  })

  it('flags an invalid time zone', () => {
    const s = base()
    s.timeZone = 'Not/AZone'
    expect(validate(s).some((e) => e.includes('часовой пояс'))).toBe(true)
  })

  it('flags start >= end', () => {
    const s = base()
    s.days[0].intervals = [{ start: '12:00', end: '09:00' }]
    expect(validate(s).some((e) => e.includes('Пн'))).toBe(true)
  })

  it('flags overlapping intervals within a day', () => {
    const s = base()
    s.days[0].intervals = [{ start: '09:00', end: '12:00' }, { start: '11:00', end: '13:00' }]
    expect(validate(s).some((e) => e.includes('пересек'))).toBe(true)
  })

  it('flags an override with start >= end when not full-day', () => {
    const s = base()
    s.overrides = [{ date: '2026-07-25', fullDay: false, start: '14:00', end: '10:00' }]
    expect(validate(s).some((e) => e.includes('2026-07-25'))).toBe(true)
  })

  it('flags an empty interval time', () => {
    const s = base()
    s.days[0].intervals = [{ start: '', end: '12:00' }]
    expect(validate(s).length).toBeGreaterThan(0)
  })

  it('uses emptyDays for 7 disabled days', () => {
    expect(emptyDays()).toHaveLength(7)
  })
})
```

2. **Run — expect FAIL**: `npm test -- src/modules/schedule/schedule.test.ts` → `Failed to load url ./schedule.ts`.

3. **Implement** `src/modules/schedule/schedule.ts`:
```ts
import type { ScheduleBundle, TravelBody, UpsertScheduleBody } from './types.ts'

export type Interval = { start: string; end: string }
export type DayState = { enabled: boolean; intervals: Interval[] }
export type OverrideState = { date: string; fullDay: boolean; start: string; end: string }
export type TravelState = { start_date: string; end_date: string; time_zone: string }
export type EditorState = {
  name: string
  timeZone: string
  days: DayState[]
  overrides: OverrideState[]
  travels: TravelState[]
}

// Index 0..6 → day_of_week 1..7 (ISO, Mon..Sun).
export const DAY_LABELS = ['Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб', 'Вс']

export function emptyDays(): DayState[] {
  return DAY_LABELS.map(() => ({ enabled: false, intervals: [] }))
}

// "09:00:00" | "09:00" → "09:00".
function hhmm(value: string): string {
  return value.slice(0, 5)
}

export function bundleToState(bundle: ScheduleBundle | null, defaultTz: string): EditorState {
  if (!bundle) {
    return { name: 'Моё расписание', timeZone: defaultTz, days: emptyDays(), overrides: [], travels: [] }
  }

  const days = emptyDays()
  for (const wh of bundle.weekly_hours) {
    const idx = wh.day_of_week - 1
    if (idx < 0 || idx > 6) continue
    days[idx].enabled = true
    days[idx].intervals.push({ start: hhmm(wh.start_time), end: hhmm(wh.end_time) })
  }

  const overrides: OverrideState[] = bundle.date_overrides.map((o) => {
    const fullDay = o.start_time === null || o.end_time === null
    return {
      date: o.date,
      fullDay,
      start: fullDay ? '' : hhmm(o.start_time ?? ''),
      end: fullDay ? '' : hhmm(o.end_time ?? ''),
    }
  })

  const travels: TravelState[] = bundle.travel_schedules.map((t) => ({
    start_date: t.start_date,
    end_date: t.end_date ?? '',
    time_zone: t.time_zone,
  }))

  return { name: bundle.schedule.name, timeZone: bundle.schedule.time_zone, days, overrides, travels }
}

export function buildUpsert(state: EditorState): UpsertScheduleBody {
  const weekly_hours = state.days.flatMap((day, idx) => {
    if (!day.enabled) return []
    return day.intervals.map((iv) => ({
      day_of_week: idx + 1,
      start_time: iv.start,
      end_time: iv.end,
    }))
  })

  const date_overrides = state.overrides.map((o) => ({
    date: o.date,
    start_time: o.fullDay ? null : o.start,
    end_time: o.fullDay ? null : o.end,
  }))

  return { name: state.name, time_zone: state.timeZone, weekly_hours, date_overrides }
}

export function buildTravel(state: EditorState): TravelBody {
  return {
    travel_schedules: state.travels.map((t) => ({
      time_zone: t.time_zone,
      start_date: t.start_date,
      end_date: t.end_date === '' ? null : t.end_date,
      prev_time_zone: state.timeZone,
    })),
  }
}

function isValidTimeZone(tz: string): boolean {
  if (!tz) return false
  try {
    new Intl.DateTimeFormat('en-US', { timeZone: tz })
    return true
  } catch {
    return false
  }
}

// Half-open overlap on "HH:MM" strings (lexical compare is correct: zero-padded).
function overlaps(intervals: Interval[]): boolean {
  const sorted = [...intervals].sort((a, b) => a.start.localeCompare(b.start))
  for (let i = 1; i < sorted.length; i += 1) {
    if (sorted[i].start < sorted[i - 1].end) return true
  }
  return false
}

export function validate(state: EditorState): string[] {
  const errors: string[] = []

  if (!isValidTimeZone(state.timeZone)) {
    errors.push('Укажите корректный часовой пояс')
  }

  state.days.forEach((day, idx) => {
    if (!day.enabled) return
    const label = DAY_LABELS[idx]
    if (day.intervals.length === 0) {
      errors.push(`${label}: добавьте хотя бы один интервал или отключите день`)
      return
    }
    for (const iv of day.intervals) {
      if (!iv.start || !iv.end) {
        errors.push(`${label}: заполните время интервала`)
        continue
      }
      if (iv.start >= iv.end) {
        errors.push(`${label}: начало должно быть раньше конца`)
      }
    }
    if (overlaps(day.intervals)) {
      errors.push(`${label}: интервалы пересекаются`)
    }
  })

  state.overrides.forEach((o) => {
    if (!o.date) {
      errors.push('Укажите дату исключения')
      return
    }
    if (o.fullDay) return
    if (!o.start || !o.end || o.start >= o.end) {
      errors.push(`Исключение ${o.date}: начало должно быть раньше конца`)
    }
  })

  return errors
}
```

4. **Run — expect PASS**: `npm test -- src/modules/schedule/schedule.test.ts` → all pass (`15 passed` or as counted).

5. **Commit**:
```bash
git add event-organizer-frontend/src/modules/schedule && git commit -m "feat(organizer-fe): schedule pure helpers (bundle<->state, buildUpsert/buildTravel, validate)"
```

---

## Task 7 — WeeklyHours component

7 rows Mon–Sun, per-day toggle, add/remove HH:MM intervals.

**Files**
- Create: `src/modules/schedule/WeeklyHours.tsx`
- Create test: `src/modules/schedule/WeeklyHours.test.tsx`

**Interfaces**
- Produces `WeeklyHours({days, onChange})` where `days: DayState[]`, `onChange: (days: DayState[]) => void`

### Steps

1. **Write the failing test** — `src/modules/schedule/WeeklyHours.test.tsx`:
```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { WeeklyHours } from './WeeklyHours.tsx'
import { emptyDays, type DayState } from './schedule.ts'

let container: HTMLDivElement
let root: Root

async function mount(days: DayState[], onChange = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<WeeklyHours days={days} onChange={onChange} />))
  return onChange
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

describe('WeeklyHours', () => {
  it('renders 7 weekday rows', async () => {
    await mount(emptyDays())
    expect(container.querySelectorAll('.weekday-row')).toHaveLength(7)
    expect(container.querySelectorAll('.weekday-name')[0].textContent).toBe('Пн')
    expect(container.querySelectorAll('.weekday-name')[6].textContent).toBe('Вс')
  })

  it('enabling a day adds a default interval', async () => {
    const onChange = await mount(emptyDays())
    const toggle = container.querySelector('.weekday-row input[type="checkbox"]') as HTMLInputElement
    await act(async () => toggle.click())
    const next = onChange.mock.calls[0][0] as DayState[]
    expect(next[0].enabled).toBe(true)
    expect(next[0].intervals).toHaveLength(1)
  })

  it('adds and removes an interval on an enabled day', async () => {
    const days = emptyDays()
    days[0] = { enabled: true, intervals: [{ start: '09:00', end: '12:00' }] }
    const onChange = await mount(days)
    const addBtn = [...container.querySelectorAll('button')].find((b) => b.textContent?.includes('интервал'))!
    await act(async () => addBtn.click())
    expect((onChange.mock.calls[0][0] as DayState[])[0].intervals).toHaveLength(2)
  })
})
```

2. **Run — expect FAIL**: `npm test -- src/modules/schedule/WeeklyHours.test.tsx` → `Failed to load url ./WeeklyHours.tsx`.

3. **Implement** `src/modules/schedule/WeeklyHours.tsx`:
```tsx
import { DAY_LABELS, type DayState, type Interval } from './schedule.ts'

type Props = {
  days: DayState[]
  onChange: (days: DayState[]) => void
}

const DEFAULT_INTERVAL: Interval = { start: '09:00', end: '18:00' }

export function WeeklyHours({ days, onChange }: Props) {
  function updateDay(idx: number, next: DayState) {
    const copy = days.map((d, i) => (i === idx ? next : d))
    onChange(copy)
  }

  function toggle(idx: number) {
    const day = days[idx]
    if (day.enabled) {
      updateDay(idx, { enabled: false, intervals: [] })
      return
    }
    updateDay(idx, { enabled: true, intervals: [{ ...DEFAULT_INTERVAL }] })
  }

  function addInterval(idx: number) {
    const day = days[idx]
    updateDay(idx, { ...day, intervals: [...day.intervals, { ...DEFAULT_INTERVAL }] })
  }

  function removeInterval(idx: number, ivIdx: number) {
    const day = days[idx]
    updateDay(idx, { ...day, intervals: day.intervals.filter((_, i) => i !== ivIdx) })
  }

  function setTime(idx: number, ivIdx: number, field: 'start' | 'end', value: string) {
    const day = days[idx]
    const intervals = day.intervals.map((iv, i) => (i === ivIdx ? { ...iv, [field]: value } : iv))
    updateDay(idx, { ...day, intervals })
  }

  return (
    <div>
      {DAY_LABELS.map((label, idx) => {
        const day = days[idx]
        return (
          <div className="weekday-row" key={label}>
            <label className="weekday-name">
              <input type="checkbox" checked={day.enabled} onChange={() => toggle(idx)} /> {label}
            </label>
            <div>
              {!day.enabled && <span className="muted">Недоступно</span>}
              {day.enabled &&
                day.intervals.map((iv, ivIdx) => (
                  <div className="interval-row" key={ivIdx}>
                    <input
                      type="time"
                      value={iv.start}
                      onChange={(e) => setTime(idx, ivIdx, 'start', e.target.value)}
                    />
                    <span>–</span>
                    <input
                      type="time"
                      value={iv.end}
                      onChange={(e) => setTime(idx, ivIdx, 'end', e.target.value)}
                    />
                    <button
                      type="button"
                      className="icon-button"
                      aria-label="Удалить интервал"
                      onClick={() => removeInterval(idx, ivIdx)}
                    >
                      ✕
                    </button>
                  </div>
                ))}
              {day.enabled && (
                <button type="button" className="link-button" onClick={() => addInterval(idx)}>
                  + интервал
                </button>
              )}
            </div>
          </div>
        )
      })}
    </div>
  )
}
```

4. **Run — expect PASS**: `npm test -- src/modules/schedule/WeeklyHours.test.tsx` → `3 passed`.

5. **Commit**:
```bash
git add event-organizer-frontend/src/modules/schedule && git commit -m "feat(organizer-fe): WeeklyHours component (7 rows, toggle, add/remove intervals)"
```

---

## Task 8 — DateOverrides component

Add a date (hours or «весь день недоступен» = null times), remove per row.

**Files**
- Create: `src/modules/schedule/DateOverrides.tsx`
- Create test: `src/modules/schedule/DateOverrides.test.tsx`

**Interfaces**
- Produces `DateOverrides({overrides, onChange})` where `overrides: OverrideState[]`, `onChange: (overrides: OverrideState[]) => void`

### Steps

1. **Write the failing test** — `src/modules/schedule/DateOverrides.test.tsx`:
```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { DateOverrides } from './DateOverrides.tsx'
import type { OverrideState } from './schedule.ts'

let container: HTMLDivElement
let root: Root

async function mount(overrides: OverrideState[], onChange = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<DateOverrides overrides={overrides} onChange={onChange} />))
  return onChange
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

describe('DateOverrides', () => {
  it('adds a new override row', async () => {
    const onChange = await mount([])
    const addBtn = [...container.querySelectorAll('button')].find((b) => b.textContent?.includes('Добавить дату'))!
    await act(async () => addBtn.click())
    expect((onChange.mock.calls[0][0] as OverrideState[])).toHaveLength(1)
  })

  it('toggling full-day clears the times', async () => {
    const onChange = await mount([{ date: '2026-07-25', fullDay: false, start: '10:00', end: '14:00' }])
    const box = container.querySelector('.override-row input[type="checkbox"]') as HTMLInputElement
    await act(async () => box.click())
    const next = (onChange.mock.calls[0][0] as OverrideState[])[0]
    expect(next.fullDay).toBe(true)
  })

  it('removes a row', async () => {
    const onChange = await mount([{ date: '2026-07-25', fullDay: true, start: '', end: '' }])
    const del = container.querySelector('.override-row .icon-button') as HTMLButtonElement
    await act(async () => del.click())
    expect((onChange.mock.calls[0][0] as OverrideState[])).toHaveLength(0)
  })
})
```

2. **Run — expect FAIL**: `npm test -- src/modules/schedule/DateOverrides.test.tsx` → module not found.

3. **Implement** `src/modules/schedule/DateOverrides.tsx`:
```tsx
import type { OverrideState } from './schedule.ts'

type Props = {
  overrides: OverrideState[]
  onChange: (overrides: OverrideState[]) => void
}

const EMPTY: OverrideState = { date: '', fullDay: false, start: '09:00', end: '18:00' }

export function DateOverrides({ overrides, onChange }: Props) {
  function update(idx: number, next: OverrideState) {
    onChange(overrides.map((o, i) => (i === idx ? next : o)))
  }

  function add() {
    onChange([...overrides, { ...EMPTY }])
  }

  function remove(idx: number) {
    onChange(overrides.filter((_, i) => i !== idx))
  }

  return (
    <div>
      {overrides.map((o, idx) => (
        <div className="override-row" key={idx}>
          <input type="date" value={o.date} onChange={(e) => update(idx, { ...o, date: e.target.value })} />
          {!o.fullDay && (
            <>
              <input type="time" value={o.start} onChange={(e) => update(idx, { ...o, start: e.target.value })} />
              <span>–</span>
              <input type="time" value={o.end} onChange={(e) => update(idx, { ...o, end: e.target.value })} />
            </>
          )}
          <label>
            <input
              type="checkbox"
              checked={o.fullDay}
              onChange={(e) => update(idx, { ...o, fullDay: e.target.checked })}
            />{' '}
            весь день недоступен
          </label>
          <button type="button" className="icon-button" aria-label="Удалить дату" onClick={() => remove(idx)}>
            ✕
          </button>
        </div>
      ))}
      <button type="button" className="link-button" onClick={add}>
        + Добавить дату
      </button>
    </div>
  )
}
```

4. **Run — expect PASS**: `npm test -- src/modules/schedule/DateOverrides.test.tsx` → `3 passed`.

5. **Commit**:
```bash
git add event-organizer-frontend/src/modules/schedule && git commit -m "feat(organizer-fe): DateOverrides component (add/remove, full-day block)"
```

---

## Task 9 — Travel component

Add/remove travel rows (start_date, optional end_date, time_zone via TimeZoneField).

**Files**
- Create: `src/modules/schedule/Travel.tsx`
- Create test: `src/modules/schedule/Travel.test.tsx`

**Interfaces**
- Produces `Travel({travels, onChange})` where `travels: TravelState[]`, `onChange: (travels: TravelState[]) => void`
- Consumes `TimeZoneField`

### Steps

1. **Write the failing test** — `src/modules/schedule/Travel.test.tsx`:
```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { Travel } from './Travel.tsx'
import type { TravelState } from './schedule.ts'

let container: HTMLDivElement
let root: Root

async function mount(travels: TravelState[], onChange = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<Travel travels={travels} onChange={onChange} />))
  return onChange
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.clearAllMocks()
})

describe('Travel', () => {
  it('adds a travel row', async () => {
    const onChange = await mount([])
    const addBtn = [...container.querySelectorAll('button')].find((b) => b.textContent?.includes('Добавить поездку'))!
    await act(async () => addBtn.click())
    expect((onChange.mock.calls[0][0] as TravelState[])).toHaveLength(1)
  })

  it('renders existing rows and removes one', async () => {
    const onChange = await mount([{ start_date: '2026-08-01', end_date: '2026-08-10', time_zone: 'Asia/Dubai' }])
    expect(container.querySelectorAll('.travel-row')).toHaveLength(1)
    const del = container.querySelector('.travel-row .icon-button') as HTMLButtonElement
    await act(async () => del.click())
    expect((onChange.mock.calls[0][0] as TravelState[])).toHaveLength(0)
  })
})
```

2. **Run — expect FAIL**: module not found.

3. **Implement** `src/modules/schedule/Travel.tsx`:
```tsx
import { TimeZoneField } from '../shared/TimeZoneField.tsx'
import type { TravelState } from './schedule.ts'

type Props = {
  travels: TravelState[]
  onChange: (travels: TravelState[]) => void
}

const EMPTY: TravelState = { start_date: '', end_date: '', time_zone: 'UTC' }

export function Travel({ travels, onChange }: Props) {
  function update(idx: number, next: TravelState) {
    onChange(travels.map((t, i) => (i === idx ? next : t)))
  }

  function add() {
    onChange([...travels, { ...EMPTY }])
  }

  function remove(idx: number) {
    onChange(travels.filter((_, i) => i !== idx))
  }

  return (
    <div>
      {travels.map((t, idx) => (
        <div className="travel-row" key={idx}>
          <input type="date" value={t.start_date} onChange={(e) => update(idx, { ...t, start_date: e.target.value })} />
          <span>–</span>
          <input type="date" value={t.end_date} onChange={(e) => update(idx, { ...t, end_date: e.target.value })} />
          <TimeZoneField value={t.time_zone} onChange={(tz) => update(idx, { ...t, time_zone: tz })} />
          <button type="button" className="icon-button" aria-label="Удалить поездку" onClick={() => remove(idx)}>
            ✕
          </button>
        </div>
      ))}
      <button type="button" className="link-button" onClick={add}>
        + Добавить поездку
      </button>
    </div>
  )
}
```

4. **Run — expect PASS**: `npm test -- src/modules/schedule/Travel.test.tsx` → `2 passed`.

5. **Commit**:
```bash
git add event-organizer-frontend/src/modules/schedule && git commit -m "feat(organizer-fe): Travel component (add/remove travel rows with tz)"
```

---

## Task 10 — SchedulePage

Load the bundle (404 → empty editor), compose the three sections + base tz, «Сохранить» → `putSchedule` with the exact body, «Сохранить поездки» → `putTravel`; inline validation + save errors. Replaces the Task-3 stub.

**Files**
- Modify: `src/modules/schedule/SchedulePage.tsx` (replace stub)
- Create test: `src/modules/schedule/SchedulePage.test.tsx`

**Interfaces**
- Produces `SchedulePage()`
- Consumes `getSchedule`, `putSchedule`, `putTravel`, `bundleToState`, `buildUpsert`, `buildTravel`, `validate`, `WeeklyHours`, `DateOverrides`, `Travel`, `TimeZoneField`

### Steps

1. **Write the failing test** — `src/modules/schedule/SchedulePage.test.tsx`:
```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { SchedulePage } from './SchedulePage.tsx'
import * as scheduleApi from './scheduleApi.ts'
import type { ScheduleBundle } from './types.ts'

let container: HTMLDivElement
let root: Root

const bundle: ScheduleBundle = {
  schedule: { id: '1', owner_user_id: '2', name: 'Моё', time_zone: 'Europe/Moscow' },
  weekly_hours: [{ day_of_week: 1, start_time: '09:00:00', end_time: '12:00:00' }],
  date_overrides: [],
  travel_schedules: [],
}

async function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<SchedulePage />))
  await act(async () => {}) // flush the load effect
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

describe('SchedulePage', () => {
  it('loads the bundle into rows', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue(bundle)
    await mount()
    expect(container.querySelectorAll('.weekday-row')).toHaveLength(7)
    const mon = container.querySelector('.weekday-row input[type="checkbox"]') as HTMLInputElement
    expect(mon.checked).toBe(true)
  })

  it('starts empty on 404', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue(null)
    await mount()
    const boxes = [...container.querySelectorAll('.weekday-row input[type="checkbox"]')] as HTMLInputElement[]
    expect(boxes.every((b) => !b.checked)).toBe(true)
  })

  it('saves with the exact upsert body incl. name', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue(bundle)
    const put = vi.spyOn(scheduleApi, 'putSchedule').mockResolvedValue(bundle)
    await mount()
    const save = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сохранить')!
    await act(async () => save.click())
    expect(put).toHaveBeenCalledWith({
      name: 'Моё',
      time_zone: 'Europe/Moscow',
      weekly_hours: [{ day_of_week: 1, start_time: '09:00', end_time: '12:00' }],
      date_overrides: [],
    })
  })

  it('travel save hits putTravel with the envelope', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue(bundle)
    const putT = vi.spyOn(scheduleApi, 'putTravel').mockResolvedValue({})
    await mount()
    const saveT = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сохранить поездки')!
    await act(async () => saveT.click())
    expect(putT).toHaveBeenCalledWith({ travel_schedules: [] })
  })

  it('blocks save and shows an inline error on invalid state', async () => {
    vi.spyOn(scheduleApi, 'getSchedule').mockResolvedValue({
      ...bundle,
      weekly_hours: [{ day_of_week: 1, start_time: '12:00:00', end_time: '09:00:00' }],
    })
    const put = vi.spyOn(scheduleApi, 'putSchedule').mockResolvedValue(bundle)
    await mount()
    const save = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сохранить')!
    await act(async () => save.click())
    expect(put).not.toHaveBeenCalled()
    expect(container.querySelector('.error-text')?.textContent).toContain('Пн')
  })
})
```

2. **Run — expect FAIL**: the stub renders no `.weekday-row` → assertions fail.

3. **Implement** `src/modules/schedule/SchedulePage.tsx` (replace the stub):
```tsx
import { useEffect, useState } from 'react'
import { ApiError } from '../shared/api.ts'
import { TimeZoneField } from '../shared/TimeZoneField.tsx'
import { WeeklyHours } from './WeeklyHours.tsx'
import { DateOverrides } from './DateOverrides.tsx'
import { Travel } from './Travel.tsx'
import { getSchedule, putSchedule, putTravel } from './scheduleApi.ts'
import {
  bundleToState,
  buildTravel,
  buildUpsert,
  emptyDays,
  validate,
  type EditorState,
} from './schedule.ts'

function browserTz(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

function upstreamMessage(err: unknown): string {
  if (err instanceof ApiError && err.status === 502) return 'Сервис временно недоступен. Попробуйте ещё раз.'
  if (err instanceof ApiError) return err.message
  return 'Не удалось сохранить. Попробуйте ещё раз.'
}

export function SchedulePage() {
  const [state, setState] = useState<EditorState | null>(null)
  const [loading, setLoading] = useState(true)
  const [errors, setErrors] = useState<string[]>([])
  const [travelError, setTravelError] = useState<string | null>(null)
  const [savedOk, setSavedOk] = useState(false)
  const [saving, setSaving] = useState(false)

  useEffect(() => {
    let cancelled = false
    const defaultTz = browserTz()
    getSchedule()
      .then((bundle) => {
        if (cancelled) return
        setState(bundleToState(bundle, defaultTz))
      })
      .catch(() => {
        if (cancelled) return
        setState({ name: 'Моё расписание', timeZone: defaultTz, days: emptyDays(), overrides: [], travels: [] })
        setErrors(['Не удалось загрузить расписание'])
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (loading || !state) {
    return <div className="card">Загрузка…</div>
  }

  async function handleSave() {
    if (!state) return
    setSavedOk(false)
    const validationErrors = validate(state)
    if (validationErrors.length > 0) {
      setErrors(validationErrors)
      return
    }
    setErrors([])
    setSaving(true)
    try {
      await putSchedule(buildUpsert(state))
      setSavedOk(true)
    } catch (err) {
      setErrors([upstreamMessage(err)])
    } finally {
      setSaving(false)
    }
  }

  async function handleSaveTravel() {
    if (!state) return
    setTravelError(null)
    setSaving(true)
    try {
      await putTravel(buildTravel(state))
    } catch (err) {
      setTravelError(upstreamMessage(err))
    } finally {
      setSaving(false)
    }
  }

  return (
    <div>
      <div className="page-head">
        <h1>Расписание</h1>
        <button type="button" onClick={handleSave} disabled={saving}>
          Сохранить
        </button>
      </div>

      {errors.length > 0 && (
        <div className="section">
          {errors.map((e) => (
            <p className="error-text" key={e}>
              {e}
            </p>
          ))}
        </div>
      )}
      {savedOk && <p className="ok-text">Сохранено</p>}

      <div className="section">
        <h2>Часовой пояс</h2>
        <TimeZoneField value={state.timeZone} onChange={(tz) => setState({ ...state, timeZone: tz })} />
      </div>

      <div className="section">
        <h2>Часы по неделям</h2>
        <WeeklyHours days={state.days} onChange={(days) => setState({ ...state, days })} />
      </div>

      <div className="section">
        <h2>Исключения по датам</h2>
        <DateOverrides overrides={state.overrides} onChange={(overrides) => setState({ ...state, overrides })} />
      </div>

      <div className="section">
        <div className="page-head">
          <h2>Поездки (временный часовой пояс)</h2>
          <button type="button" onClick={handleSaveTravel} disabled={saving}>
            Сохранить поездки
          </button>
        </div>
        {travelError && <p className="error-text">{travelError}</p>}
        <Travel travels={state.travels} onChange={(travels) => setState({ ...state, travels })} />
      </div>
    </div>
  )
}
```

4. **Run — expect PASS**: `npm test -- src/modules/schedule/SchedulePage.test.tsx` → `5 passed`.

5. **Commit**:
```bash
git add event-organizer-frontend/src/modules/schedule && git commit -m "feat(organizer-fe): SchedulePage (load bundle, save upsert + travel, inline validation)"
```

---

## Task 11 — bookings module

`GET /api/me/bookings` → `{id, start_time, end_time, status}`; split предстоящие/прошедшие by `start_time` vs now; status badge; empty state. Replaces the Task-3 stub.

**Files**
- Create: `src/modules/bookings/types.ts`, `src/modules/bookings/bookingsApi.ts`
- Modify: `src/modules/bookings/BookingsPage.tsx` (replace stub)
- Create test: `src/modules/bookings/BookingsPage.test.tsx`

**Interfaces**
- Produces `type BookingRow = { id: string; start_time: string; end_time: string; status: string }`
- Produces `getBookings(): Promise<BookingRow[]>`
- Produces `BookingsPage()`
- Consumes `apiRequest`, `getProfile` (Task 12; used only for the display tz — see note), `formatRange`

### Steps

1. **`src/modules/bookings/types.ts`**:
```ts
export type BookingRow = {
  id: string
  start_time: string
  end_time: string
  status: string
}
```

2. **`src/modules/bookings/bookingsApi.ts`**:
```ts
import { apiRequest } from '../shared/api.ts'
import type { BookingRow } from './types.ts'

export async function getBookings(): Promise<BookingRow[]> {
  return apiRequest<BookingRow[]>('/api/me/bookings')
}
```

3. **Write the failing test** — `src/modules/bookings/BookingsPage.test.tsx`:
```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { BookingsPage } from './BookingsPage.tsx'
import * as bookingsApi from './bookingsApi.ts'
import * as profileApi from '../profile/profileApi.ts'
import type { BookingRow } from './types.ts'

let container: HTMLDivElement
let root: Root

const future = new Date(Date.now() + 86_400_000).toISOString()
const futureEnd = new Date(Date.now() + 90_000_000).toISOString()
const past = new Date(Date.now() - 86_400_000).toISOString()
const pastEnd = new Date(Date.now() - 82_800_000).toISOString()

async function mount(rows: BookingRow[]) {
  vi.spyOn(bookingsApi, 'getBookings').mockResolvedValue(rows)
  vi.spyOn(profileApi, 'getProfile').mockResolvedValue({ name: 'N', email: 'e@x.io', time_zone: 'UTC' })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<BookingsPage />))
  await act(async () => {})
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

describe('BookingsPage', () => {
  it('splits upcoming and past', async () => {
    await mount([
      { id: 'a', start_time: future, end_time: futureEnd, status: 'confirmed' },
      { id: 'b', start_time: past, end_time: pastEnd, status: 'cancelled' },
    ])
    const groups = container.querySelectorAll('.booking-group')
    expect(groups).toHaveLength(2)
    expect(groups[0].querySelectorAll('.booking-row')).toHaveLength(1) // upcoming
    expect(groups[1].querySelectorAll('.booking-row')).toHaveLength(1) // past
    expect(container.querySelector('.badge--confirmed')).toBeTruthy()
  })

  it('shows an empty state when there are none', async () => {
    await mount([])
    expect(container.querySelector('.empty-state')).toBeTruthy()
  })
})
```

4. **Run — expect FAIL**: the stub renders no groups; also `profileApi` must exist. If `profileApi.ts` is not created yet, create the minimal file in Task 12 first, or create it now as part of this task. To keep tasks ordered, create `src/modules/profile/profileApi.ts` + `types.ts` now (they are finalized in Task 12) — paste the Task-12 versions. Then run and see the assertion failures from the stub.

5. **Implement** `src/modules/bookings/BookingsPage.tsx` (replace the stub):
```tsx
import { useEffect, useState } from 'react'
import { Badge } from 'events-design-system'
import { formatRange } from '../shared/format.ts'
import { getProfile } from '../profile/profileApi.ts'
import { getBookings } from './bookingsApi.ts'
import type { BookingRow } from './types.ts'

const STATUS_LABEL: Record<string, string> = {
  confirmed: 'Подтверждена',
  cancelled: 'Отменена',
}

function statusVariant(status: string): string {
  return status === 'cancelled' ? 'badge--cancelled' : 'badge--confirmed'
}

function BookingList({ rows, timeZone }: { rows: BookingRow[]; timeZone: string | undefined }) {
  if (rows.length === 0) {
    return <div className="empty-state">Нет броней</div>
  }
  return (
    <>
      {rows.map((b) => (
        <div className="booking-row" key={b.id}>
          <span>{formatRange(b.start_time, b.end_time, timeZone)}</span>
          <span className={`badge ${statusVariant(b.status)}`}>{STATUS_LABEL[b.status] ?? b.status}</span>
        </div>
      ))}
    </>
  )
}

export function BookingsPage() {
  const [rows, setRows] = useState<BookingRow[] | null>(null)
  const [timeZone, setTimeZone] = useState<string | undefined>(undefined)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    Promise.all([getBookings(), getProfile().catch(() => null)])
      .then(([bookings, profile]) => {
        if (cancelled) return
        setRows(bookings)
        setTimeZone(profile?.time_zone ?? undefined)
      })
      .catch(() => {
        if (!cancelled) setError('Не удалось загрузить брони')
      })
    return () => {
      cancelled = true
    }
  }, [])

  if (error) return <div className="card">{error}</div>
  if (!rows) return <div className="card">Загрузка…</div>

  const now = Date.now()
  const upcoming = rows.filter((b) => new Date(b.start_time).getTime() >= now)
  const past = rows.filter((b) => new Date(b.start_time).getTime() < now)

  if (rows.length === 0) {
    return (
      <div>
        <div className="page-head">
          <h1>Брони</h1>
        </div>
        <div className="empty-state">У вас пока нет броней</div>
      </div>
    )
  }

  return (
    <div>
      <div className="page-head">
        <h1>Брони</h1>
      </div>
      <div className="booking-group">
        <h2>Предстоящие</h2>
        <BookingList rows={upcoming} timeZone={timeZone} />
      </div>
      <div className="booking-group">
        <h2>Прошедшие</h2>
        <BookingList rows={past} timeZone={timeZone} />
      </div>
    </div>
  )
}
```
> If `Badge` is unused after this implementation (it is — statuses render as plain `.badge` spans), drop the `import { Badge }` line to satisfy `noUnusedLocals`. Keep the import only if you switch to the DS `Badge` component.

6. **Run — expect PASS**: `npm test -- src/modules/bookings/BookingsPage.test.tsx` → `2 passed`.

7. **Commit**:
```bash
git add event-organizer-frontend/src/modules/bookings event-organizer-frontend/src/modules/profile && git commit -m "feat(organizer-fe): BookingsPage (upcoming/past split, status badges, empty state)"
```

---

## Task 12 — profile module

Profile card (name + tz editable, email read-only; `PUT {name, time_zone}`) + password card (old/new/confirm; `PUT {old_password, new_password}`; 204 ok; 401 «Неверный текущий пароль»). Replaces the Task-3 stub; finalizes `profileApi.ts`/`types.ts` created in Task 11.

**Files**
- Create/confirm: `src/modules/profile/types.ts`, `src/modules/profile/profileApi.ts`
- Modify: `src/modules/profile/ProfilePage.tsx` (replace stub)
- Create test: `src/modules/profile/ProfilePage.test.tsx`, `src/modules/profile/profileApi.test.ts`

**Interfaces**
- Produces `type Profile = { name: string | null; email: string; time_zone: string | null }`
- Produces `getProfile(): Promise<Profile>`, `updateProfile(body: {name: string; time_zone: string}): Promise<Profile>`, `changePassword(body: {old_password: string; new_password: string}): Promise<void>`
- Produces `ProfilePage()`

### Steps

1. **`src/modules/profile/types.ts`**:
```ts
export type Profile = {
  name: string | null
  email: string
  time_zone: string | null
}
```

2. **`src/modules/profile/profileApi.ts`**:
```ts
import { apiRequest } from '../shared/api.ts'
import type { Profile } from './types.ts'

export async function getProfile(): Promise<Profile> {
  return apiRequest<Profile>('/api/me/profile')
}

export async function updateProfile(body: { name: string; time_zone: string }): Promise<Profile> {
  return apiRequest<Profile>('/api/me/profile', { method: 'PUT', body })
}

export async function changePassword(body: { old_password: string; new_password: string }): Promise<void> {
  await apiRequest<void>('/api/me/password', { method: 'PUT', body })
}
```

3. **Write the failing api test** — `src/modules/profile/profileApi.test.ts`:
```ts
import { afterEach, describe, expect, it, vi } from 'vitest'
import * as api from '../shared/api.ts'
import { changePassword, getProfile, updateProfile } from './profileApi.ts'

afterEach(() => vi.restoreAllMocks())

describe('profileApi', () => {
  it('updateProfile sends only name + time_zone', async () => {
    const spy = vi.spyOn(api, 'apiRequest').mockResolvedValue({ name: 'N', email: 'e@x.io', time_zone: 'UTC' })
    await updateProfile({ name: 'N', time_zone: 'UTC' })
    expect(spy).toHaveBeenCalledWith('/api/me/profile', { method: 'PUT', body: { name: 'N', time_zone: 'UTC' } })
  })

  it('changePassword PUTs old + new', async () => {
    const spy = vi.spyOn(api, 'apiRequest').mockResolvedValue(null)
    await changePassword({ old_password: 'a', new_password: 'b' })
    expect(spy).toHaveBeenCalledWith('/api/me/password', { method: 'PUT', body: { old_password: 'a', new_password: 'b' } })
  })

  it('getProfile GETs the profile', async () => {
    const spy = vi.spyOn(api, 'apiRequest').mockResolvedValue({ name: null, email: 'e@x.io', time_zone: null })
    await getProfile()
    expect(spy).toHaveBeenCalledWith('/api/me/profile')
  })
})
```

4. **Run — expect PASS** (profileApi already implemented in step 2): `npm test -- src/modules/profile/profileApi.test.ts` → `3 passed`.

5. **Write the failing page test** — `src/modules/profile/ProfilePage.test.tsx`:
```tsx
import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { ProfilePage } from './ProfilePage.tsx'
import * as profileApi from './profileApi.ts'
import { ApiError } from '../shared/api.ts'

let container: HTMLDivElement
let root: Root

async function mount() {
  vi.spyOn(profileApi, 'getProfile').mockResolvedValue({ name: 'Ада', email: 'ada@x.io', time_zone: 'Europe/Moscow' })
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<ProfilePage />))
  await act(async () => {})
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

function setInput(sel: string, value: string) {
  const el = container.querySelector(sel) as HTMLInputElement
  const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!
  setter.call(el, value)
  el.dispatchEvent(new Event('input', { bubbles: true }))
}

describe('ProfilePage', () => {
  it('renders email read-only and saves name + tz only', async () => {
    const put = vi.spyOn(profileApi, 'updateProfile').mockResolvedValue({ name: 'Ада Л', email: 'ada@x.io', time_zone: 'Europe/Moscow' })
    await mount()
    expect((container.querySelector('input[name="email"]') as HTMLInputElement).readOnly).toBe(true)
    setInput('input[name="name"]', 'Ада Л')
    const save = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сохранить профиль')!
    await act(async () => save.click())
    expect(put).toHaveBeenCalledWith({ name: 'Ада Л', time_zone: 'Europe/Moscow' })
  })

  it('blocks the password save when confirm mismatches', async () => {
    const chg = vi.spyOn(profileApi, 'changePassword').mockResolvedValue()
    await mount()
    setInput('input[name="old_password"]', 'old')
    setInput('input[name="new_password"]', 'newpass')
    setInput('input[name="confirm"]', 'other')
    const save = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сменить пароль')!
    await act(async () => save.click())
    expect(chg).not.toHaveBeenCalled()
    expect(container.textContent).toContain('не совпадают')
  })

  it('shows the 401 message on a wrong current password', async () => {
    vi.spyOn(profileApi, 'changePassword').mockRejectedValue(new ApiError('bad', 401, null))
    await mount()
    setInput('input[name="old_password"]', 'wrong')
    setInput('input[name="new_password"]', 'newpass')
    setInput('input[name="confirm"]', 'newpass')
    const save = [...container.querySelectorAll('button')].find((b) => b.textContent === 'Сменить пароль')!
    await act(async () => save.click())
    expect(container.textContent).toContain('Неверный текущий пароль')
  })
})
```

6. **Run — expect FAIL**: the stub renders no inputs.

7. **Implement** `src/modules/profile/ProfilePage.tsx` (replace the stub):
```tsx
import { type FormEvent, useEffect, useState } from 'react'
import { ApiError } from '../shared/api.ts'
import { TimeZoneField } from '../shared/TimeZoneField.tsx'
import { changePassword, getProfile, updateProfile } from './profileApi.ts'

function browserTz(): string {
  try {
    return Intl.DateTimeFormat().resolvedOptions().timeZone || 'UTC'
  } catch {
    return 'UTC'
  }
}

export function ProfilePage() {
  const [email, setEmail] = useState('')
  const [name, setName] = useState('')
  const [timeZone, setTimeZone] = useState('UTC')
  const [loaded, setLoaded] = useState(false)
  const [profileMsg, setProfileMsg] = useState<{ ok: boolean; text: string } | null>(null)

  const [oldPassword, setOldPassword] = useState('')
  const [newPassword, setNewPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [pwMsg, setPwMsg] = useState<{ ok: boolean; text: string } | null>(null)

  useEffect(() => {
    let cancelled = false
    getProfile()
      .then((p) => {
        if (cancelled) return
        setEmail(p.email)
        setName(p.name ?? '')
        setTimeZone(p.time_zone ?? browserTz())
        setLoaded(true)
      })
      .catch(() => {
        if (!cancelled) setProfileMsg({ ok: false, text: 'Не удалось загрузить профиль' })
      })
    return () => {
      cancelled = true
    }
  }, [])

  async function handleProfileSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setProfileMsg(null)
    try {
      await updateProfile({ name, time_zone: timeZone })
      setProfileMsg({ ok: true, text: 'Профиль сохранён' })
    } catch (err) {
      const text = err instanceof ApiError ? err.message : 'Не удалось сохранить профиль'
      setProfileMsg({ ok: false, text })
    }
  }

  async function handlePasswordSave(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    setPwMsg(null)
    if (!oldPassword || !newPassword) {
      setPwMsg({ ok: false, text: 'Заполните все поля' })
      return
    }
    if (newPassword !== confirm) {
      setPwMsg({ ok: false, text: 'Новый пароль и подтверждение не совпадают' })
      return
    }
    try {
      await changePassword({ old_password: oldPassword, new_password: newPassword })
      setPwMsg({ ok: true, text: 'Пароль изменён' })
      setOldPassword('')
      setNewPassword('')
      setConfirm('')
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        setPwMsg({ ok: false, text: 'Неверный текущий пароль' })
        return
      }
      setPwMsg({ ok: false, text: 'Не удалось изменить пароль' })
    }
  }

  if (!loaded && !profileMsg) {
    return <div className="card">Загрузка…</div>
  }

  return (
    <div>
      <div className="page-head">
        <h1>Профиль</h1>
      </div>

      <form className="section" onSubmit={handleProfileSave}>
        <h2>Профиль</h2>
        <label className="field">
          <span>Имя</span>
          <input type="text" name="name" value={name} onChange={(e) => setName(e.target.value)} />
        </label>
        <label className="field">
          <span>Часовой пояс</span>
          <TimeZoneField value={timeZone} onChange={setTimeZone} />
        </label>
        <label className="field">
          <span>Email</span>
          <input type="email" name="email" value={email} readOnly />
        </label>
        <div className="inline-actions">
          <button type="submit">Сохранить профиль</button>
        </div>
        {profileMsg && <p className={profileMsg.ok ? 'ok-text' : 'error-text'}>{profileMsg.text}</p>}
      </form>

      <form className="section" onSubmit={handlePasswordSave}>
        <h2>Пароль</h2>
        <label className="field">
          <span>Текущий пароль</span>
          <input
            type="password"
            name="old_password"
            autoComplete="current-password"
            value={oldPassword}
            onChange={(e) => setOldPassword(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Новый пароль</span>
          <input
            type="password"
            name="new_password"
            autoComplete="new-password"
            value={newPassword}
            onChange={(e) => setNewPassword(e.target.value)}
          />
        </label>
        <label className="field">
          <span>Подтверждение</span>
          <input
            type="password"
            name="confirm"
            autoComplete="new-password"
            value={confirm}
            onChange={(e) => setConfirm(e.target.value)}
          />
        </label>
        <div className="inline-actions">
          <button type="submit">Сменить пароль</button>
        </div>
        {pwMsg && <p className={pwMsg.ok ? 'ok-text' : 'error-text'}>{pwMsg.text}</p>}
      </form>
    </div>
  )
}
```

8. **Run — expect PASS**: `npm test -- src/modules/profile/` → `6 passed`.

9. **Full test run + build**:
```bash
npm test && npm run build
```
Expected: every suite passes; `tsc -b` + `vite build` succeed with no errors.

10. **Commit**:
```bash
git add event-organizer-frontend/src/modules/profile && git commit -m "feat(organizer-fe): ProfilePage (profile card + password change with 401 handling)"
```

---

## Task 13 — Docker + compose + BFF DB-role prerequisite + CLAUDE.md

Prod image (nginx SPA proxying to the BFF), runtime env-config, the compose service on port 3003, the `event_organizer` DB-role seed fix, and the package `CLAUDE.md`.

**Files**
- Create: `event-organizer-frontend/Dockerfile`, `nginx.conf`, `docker-entrypoint.d/40-env-config.sh`, `CLAUDE.md`
- Modify: `docker-compose.services.yml` (add `event-organizer-frontend` service)
- Verify/Modify: `docker/postgres-init/00-init-databases.sh` (already seeds `event_organizer` — see step 4)

**Interfaces** — none (infra).

### Steps

1. **`event-organizer-frontend/Dockerfile`**:
```dockerfile
# Build stage: Vite production build. With the default empty VITE_API_BASE_URL
# the SPA issues same-origin requests and nginx proxies them to event-organizer.
FROM node:22-alpine AS build
WORKDIR /app
COPY package*.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL=""
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

# Production stage: nginx serves the SPA and proxies API/auth paths to event-organizer.
FROM nginx:alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
COPY docker-entrypoint.d/40-env-config.sh /docker-entrypoint.d/40-env-config.sh
RUN chmod +x /docker-entrypoint.d/40-env-config.sh
EXPOSE 80
```
> `npm ci` requires a `package-lock.json`. Generate it once with `npm install` (already run in Task 1) and commit it.

2. **`event-organizer-frontend/nginx.conf`**:
```nginx
# SPA + same-origin API proxy to the event-organizer BFF.
server {
    listen 80;
    server_name _;
    root /usr/share/nginx/html;
    index index.html;

    location / {
        try_files $uri /index.html;
    }

    location /api/ {
        proxy_pass http://event-organizer:8888;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    location /auth/ {
        proxy_pass http://event-organizer:8888;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    }

    # Liveness probe for THIS container — answered by nginx, no BFF dependency.
    location = /health {
        default_type text/plain;
        return 200 "ok";
    }
}
```

3. **`event-organizer-frontend/docker-entrypoint.d/40-env-config.sh`** (identical to admin/booker):
```sh
#!/bin/sh
# Regenerate env-config.js from VITE_* env vars at container start so one image
# serves every environment. Only names starting with VITE_ are injected.
set -e
OUT=/usr/share/nginx/html/env-config.js
echo "window._env_ = {" > "$OUT"
printenv | grep '^VITE_' | while read -r line; do
  key=$(echo "$line" | cut -d '=' -f 1)
  value=$(echo "$line" | cut -d '=' -f 2-)
  escaped=$(printf '%s' "$value" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
  echo "  \"$key\": \"$escaped\"," >> "$OUT"
done
echo "};" >> "$OUT"
```

4. **Verify the `event_organizer` DB-role seed.** `docker/postgres-init/00-init-databases.sh` **already** has (line ~30):
```bash
create_db_role "${PG_ORGANIZER_DB:-event_organizer}"   "${PG_ORGANIZER_USER:-event_organizer}"   "${PG_ORGANIZER_PASSWORD:-event_organizer}"
```
The init script runs **only on a fresh postgres data volume**. The crashloop (`password authentication failed for user "event_organizer"`) happens on a **pre-existing** volume created before that line was added. Fix, without wiping other services' data, by running the same idempotent SQL against the running instance, then restarting the BFF (its startup runs alembic against the now-existing DB):
```bash
docker compose exec -T postgres psql -v ON_ERROR_STOP=1 -U postgres -d postgres <<'SQL'
DO $$ BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'event_organizer') THEN
    CREATE ROLE "event_organizer" LOGIN PASSWORD 'event_organizer';
  END IF;
END $$;
SELECT 'CREATE DATABASE "event_organizer" OWNER "event_organizer"'
  WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'event_organizer')\gexec
GRANT ALL PRIVILEGES ON DATABASE "event_organizer" TO "event_organizer";
SQL
docker compose restart event-organizer
```
Confirm it is healthy:
```bash
docker compose ps event-organizer
curl -fsS http://localhost:8006/health && echo OK
```
Expected: `event-organizer` is `running (healthy)` and `/health` returns 200. (The full-reset alternative `docker compose down -v && docker compose up -d --build` also fixes it by re-running the init script, at the cost of all dev data.)

5. **Add the compose service.** In `docker-compose.services.yml`, after the `event-booker-frontend` block, add:
```yaml
  event-organizer-frontend:
    build:
      context: ./event-organizer-frontend
      dockerfile: Dockerfile
      args:
        # Empty = same-origin: nginx in the container proxies /api, /auth and
        # /health to event-organizer:8888.
        VITE_API_BASE_URL: ${VITE_API_BASE_URL:-}
    ports:
      - "${ORGANIZER_FRONTEND_PORT:-3003}:80"
    depends_on:
      event-organizer:
        condition: service_healthy
    healthcheck:
      test: ["CMD-SHELL", "wget -qO /dev/null http://127.0.0.1:80/health"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 10s
    restart: unless-stopped
```

6. **`event-organizer-frontend/CLAUDE.md`**:
```markdown
# CLAUDE.md

Guidance for Claude Code when working in this repository.

## Role

`event-organizer-frontend` is the **organizer cabinet SPA** (slice 6.2): an
authenticated React/Vite SPA over the `event-organizer` BFF. An organizer logs
in with email + password, then manages their **own** availability schedule,
views their **own** bookings, and edits their **own** profile + password. It
talks **only** to `event-organizer` (same-origin nginx proxy in prod). Every
data call is a BFF `/api/me/*` request that injects the organizer's `user_id`
server-side — the SPA holds no owner id and no domain logic beyond form state.

## Commands

```bash
npm run dev       # Vite dev server (proxies /api,/auth,/health to :8006)
npm run build     # Type-check + production build
npm run lint      # ESLint
npm test          # Vitest (happy-dom)
npm run preview   # Preview the production build
```

## Environment Variables

| Variable | Description |
|---|---|
| `VITE_API_BASE_URL` | event-organizer base URL (e.g. `http://localhost:8006`); empty (default) = same-origin, nginx proxies `/api`, `/auth`, `/health` to `event-organizer:8888` |

## Architecture

- **Routing**: no router library. `src/modules/shared/routing.ts` — `parseRoute`
  → `AppRoute` union (`login`/`schedule`/`bookings`/`profile`/`not-found`),
  `navigateTo` → `history.pushState` + `app:navigate`; `App.tsx` re-renders on
  `popstate`/`app:navigate`, redirects unauth → `/login` and auth → away from `/login`.
- **Auth** (`src/modules/auth/`): `AuthProvider` holds the JWT in state +
  `sessionStorage` key `event_organizer_jwt`; drops an expired token at startup
  (`jwt.ts` decodes `exp`). `login(email,password)` → `POST /auth/login` (no
  TOTP). **Logout is client-side only** (the BFF has no logout endpoint).
- **API** (`src/modules/shared/api.ts`): `apiRequest<T>()` prepends
  `VITE_API_BASE_URL`, attaches `Authorization: Bearer <jwt>` when `auth:true`,
  throws `ApiError`; on `401` for a token-carrying request → clear session +
  redirect `/login`.
- **Screens**: `schedule/` (weekly hours + date overrides + travel editor over
  `/api/me/schedule` + `/api/me/schedule/travel`), `bookings/` (read-only
  upcoming/past list over `/api/me/bookings`), `profile/` (profile + password
  cards over `/api/me/profile` + `/api/me/password`).
- **Deploy**: nginx (`Dockerfile` + `nginx.conf`) same-origin-proxies `/api`,
  `/auth`, `/health` to `event-organizer:8888`; `docker-entrypoint.d/40-env-config.sh`
  writes `window._env_` at start. Host port **3003** in `docker-compose.services.yml`.

## Conventions

- **No `else if`**; **avoid `else`** — early returns / guard clauses / mappings.
- Plain CSS only. UI copy in **Russian**. No Sentry, no TOTP.

## Testing

vitest + happy-dom, `createRoot` + `act` (no testing-library); native `setInput`
via the prototype value setter; mock `apiRequest` and assert request bodies +
rendered DOM.

Cross-service docs live in the monorepo root `../docs/`. Design spec:
`../docs/superpowers/specs/2026-07-19-event-organizer-frontend-design.md`.
```

7. **Build the image** to verify Docker wiring:
```bash
docker compose build event-organizer-frontend
```
Expected: build completes; final stage is `nginx:alpine` with `/usr/share/nginx/html` populated.

8. **Commit**:
```bash
git add event-organizer-frontend docker-compose.services.yml && git commit -m "feat(organizer-fe): Dockerfile, nginx, compose service (3003), CLAUDE.md + DB-role prereq notes"
```

---

## Task 14 — Fix event-organizer BFF: forward `name` on schedule PUT

**BFF (Python) change**, not frontend — required for schedule save to work end-to-end. `event-organizer`'s `SchedulePutRequest` has no `name` field, so it silently drops `name` (Pydantic ignores unknown keys) before `put_schedule` forwards `body.model_dump(mode="json")` to event-scheduling, whose `UpsertScheduleRequest` **requires** `name` → the upsert 422s. Adding `name` to the request model makes `model_dump()` include it — no router change (`routers/me.py::put_schedule` already forwards the whole model).

**Files**
- Modify: `event-organizer/event_organizer/schemas/me.py` (add `name` to `SchedulePutRequest`)
- Test: `event-organizer/tests/test_me_api.py` (capture the forwarded body in the fake + assert `name` is forwarded)

**Interfaces**
- Consumes: nothing from earlier tasks (independent BFF fix).
- Produces: `PUT /api/me/schedule` now forwards `{name, time_zone, weekly_hours, date_overrides}` to event-scheduling — the exact body Task 5/10's `putSchedule` sends.

### Steps

1. **Write the failing test.** In `event-organizer/tests/test_me_api.py`, make the fake capture the forwarded body: in `_FakeScheduling.__init__` add `self.seen_body = None`, and in `put_schedule` set `self.seen_body = body` (first line). Then append this test:

```python
@pytest.mark.asyncio
async def test_schedule_put_forwards_name(sessionmaker_fixture) -> None:
    from starlette.testclient import TestClient

    app, sched, _ = _app_and_fakes()
    body = {
        "name": "Моё расписание",
        "time_zone": "Europe/Moscow",
        "weekly_hours": [{"day_of_week": 1, "start_time": "09:00", "end_time": "18:00"}],
        "date_overrides": [],
    }
    with TestClient(app) as c:
        r = c.put("/api/me/schedule", headers=_auth(uuid4()), json=body)
        assert r.status_code == 200
        assert sched.seen_body["name"] == "Моё расписание"  # BFF must forward name to event-scheduling
```

Also update `_FakeScheduling`:

```python
class _FakeScheduling:
    def __init__(self) -> None:
        self.seen_owner = None
        self.seen_body = None

    async def get_schedule(self, owner_user_id):
        self.seen_owner = owner_user_id
        return {"schedule": {"owner_user_id": str(owner_user_id)}, "weekly_hours": [], "date_overrides": []}

    async def put_schedule(self, owner_user_id, body):
        self.seen_owner = owner_user_id
        self.seen_body = body
        return {"schedule": {"owner_user_id": str(owner_user_id)}, "weekly_hours": [], "date_overrides": []}
```

2. **Run it → SEE it fail.**

```bash
cd event-organizer && uv run pytest tests/test_me_api.py::test_schedule_put_forwards_name -x -q
```
Expected: **FAIL** with `KeyError: 'name'` (the current `SchedulePutRequest` drops `name`, so the forwarded body has no `name`).

3. **Add `name` to the request model.** In `event-organizer/event_organizer/schemas/me.py`, change `SchedulePutRequest`:

```python
class SchedulePutRequest(BaseModel):
    name: str
    time_zone: str
    weekly_hours: list[WeeklyHourModel]
    date_overrides: list[DateOverrideModel]
```

4. **Run it → SEE it pass** (and the rest of the suite is unaffected).

```bash
cd event-organizer && uv run pytest tests/test_me_api.py -q
```
Expected: **PASS** (all tests, including `test_schedule_put_forwards_name`).

5. **Lint + commit.**

```bash
cd event-organizer && uv run ruff check --fix . && uv run ruff format .
git add event-organizer/event_organizer/schemas/me.py event-organizer/tests/test_me_api.py
git commit -m "fix(event-organizer): forward name on PUT /api/me/schedule (event-scheduling requires it)"
```

---

## Task 15 — Browser smoke (manual verification checklist)

No unit test — a documented, run-by-hand checklist verifying the full stack end-to-end. Record actual outputs.

**Files** — none (verification only).

### Checklist

1. **Bring up the stack** (or just the needed services):
```bash
docker compose up -d --build postgres event-users event-scheduling event-organizer event-organizer-frontend
docker compose ps
```
Expected: all five are `running (healthy)`. If `event-organizer` is crashlooping, apply Task 13 step 4 first.

2. **Ensure a test organizer exists in event-users** with `role=organizer`. The provisioning endpoint resolves the email against event-users' `GET /api/users/by-identity?email=&role=organizer` and requires the resolved id to match the `user_id` you pass. Pick an existing organizer, or create one via event-users' API/seed. Capture its `user_id` (UUID) and `email`. (One source: run `scripts/calcom_sim.py` + the db-sync flow, which upserts organizers into event-users; or insert directly into the `event_users` DB.)

3. **Provision the login credential** via the admin-key endpoint (dev key `dev-organizer-admin-key`):
```bash
curl -sS -X POST http://localhost:8006/admin/organizers \
  -H 'Authorization: Bearer dev-organizer-admin-key' \
  -H 'Content-Type: application/json' \
  -d '{"user_id":"<ORGANIZER_UUID>","email":"<ORGANIZER_EMAIL>","password":"Organizer123!"}'
```
Expected: `201 {"id":..., "user_id":"<ORGANIZER_UUID>", "email":"<ORGANIZER_EMAIL>"}`. `422` = the email isn't an event-users organizer or doesn't match `user_id`; `409` = already provisioned (fine).

4. **Log in through the SPA**: open `http://localhost:3003`, expect a redirect to `/login`. Enter the email + `Organizer123!`, submit. Expect a redirect to `/` (Расписание) with the sidebar showing Расписание / Брони / Профиль.
   - Negative check: a wrong password shows «Неверный email или пароль».

5. **Exercise the schedule editor** (`/`):
   - Enable Пн, set `09:00–12:00`, add a second interval `14:00–18:00`.
   - Add a date override with hours, and one with «весь день недоступен».
   - Set the base Часовой пояс via the combobox.
   - Click **Сохранить**. Expect «Сохранено» (Task 14 fixed the BFF `name` forwarding, so this persists instead of 422-ing).
   - Add a travel row (start/end date + tz), click **Сохранить поездки**. Verify against event-scheduling:
```bash
curl -sS http://localhost:8004/api/v1/schedules/<ORGANIZER_UUID> \
  -H 'Authorization: Bearer dev-scheduling-api-key-3f9c2e1a7b64d508' | jq
```
Expect the weekly_hours / date_overrides / travel_schedules you entered.

6. **Bookings** (`/bookings`): expect two groups (Предстоящие / Прошедшие) or the empty state «У вас пока нет броней». (To populate, create a booking for this host via event-scheduling `POST /api/v1/bookings`.)

7. **Profile + password** (`/profile`):
   - Change Имя and/or Часовой пояс, **Сохранить профиль** → «Профиль сохранён». Confirm `email` is read-only.
   - **Пароль**: mismatched confirm → «…не совпадают» (no request). Wrong current password → «Неверный текущий пароль». Correct old + matching new/confirm → «Пароль изменён»; log out and back in with the new password.

8. **Session**: click logout (sidebar) → redirect to `/login`; `sessionStorage` no longer holds `event_organizer_jwt`. Reload a deep link (e.g. `/profile`) while logged out → redirect to `/login`.

Record the actual results (pass/fail per step) in the branch's task report.
