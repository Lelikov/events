# event-organizer-frontend (organizer cabinet SPA, slice 6.2) — Design

**Status:** approved (design), pending implementation plan
**Date:** 2026-07-19
**Depends on:** `event-organizer` BFF (slice 6.1, built) + `event-scheduling` + `event-users`

## Goal

Build the organizer-facing SPA (**slice 6.2**) over the `event-organizer` BFF: an
organizer logs in with email + password, then manages their **own** availability
schedule, views their **own** bookings, and edits their **own** profile and
password. It is a thin authenticated client over the BFF — it holds no domain
logic beyond form state and the JWT session; every data endpoint is a BFF
`/api/me/*` call that injects the organizer's `user_id` server-side.

## Non-goals (deferred)

- **Helm chart + CI** for the new frontend — a thin parity follow-up (mirrors
  `event-booker-frontend`'s chart), out of this slice.
- **Event-types / calendars** management — the BFF deliberately does not front
  these; they stay admin/other-owned.
- **Password reset, self-registration, TOTP** — login is password-only;
  organizers are provisioned only via the admin-key `POST /admin/organizers`.
- **Booking actions** (cancel/reschedule from the cabinet) — bookings are
  read-only here; the BFF exposes only a projected list.

## Architecture

New package **`event-organizer-frontend/`**, a 1:1 mirror of
`event-admin-frontend`'s stack and conventions:

- React 19 + Vite + TypeScript, plain CSS only (no Tailwind/styled-components),
  `events-design-system` (git-tag npm dep, `file:` link for local dev).
- **No router library** — manual routing in `shared/routing.ts`
  (`parseRoute(pathname) -> AppRoute` discriminated union, `navigateTo(path)` →
  `history.pushState` + `app:navigate` event; `App.tsx` re-renders on
  `popstate`/`app:navigate`).
- **Authenticated** (unlike the public Booker): mirrors admin-fe's auth
  machinery, **minus TOTP**.
- nginx SPA (prod `Dockerfile` + `nginx.conf`) same-origin-proxies `/api/*`,
  `/auth/*`, `/health` to `event-organizer:8888`; `docker-entrypoint.d`
  writes `window._env_` at container start (runtime config).
- Host port **3003** (container 80) in `docker-compose.services.yml`.
- Conventions: **no `else if`**, **avoid `else`** (early returns / guard clauses
  / mapping objects); UI copy in Russian.

### Module structure

```
src/modules/
  auth/      LoginPage · AuthProvider + useAuth · context.ts · jwt.ts (exp decode)
             · storage.ts (sessionStorage key "event_organizer_jwt") · authApi.ts
  schedule/  SchedulePage · WeeklyHours · DateOverrides · Travel
             · scheduleApi.ts · schedule.ts (pure build/validate helpers) · types.ts
  bookings/  BookingsPage · bookingsApi.ts · types.ts
  profile/   ProfilePage (profile card + password card) · profileApi.ts · types.ts
  shared/    api.ts (apiRequest + ApiError, 401 interceptor) · runtimeEnv.ts
             (window._env_) · routing.ts · ErrorBoundary.tsx · format.ts (ru-RU)
             · TimeZoneField.tsx (searchable tz combobox on the DS .tz-picker classes)
  app/       OrganizerLayout.tsx (sidebar shell + logout)
```

### Routing

`AppRoute` union: `{name:'login'}`, `{name:'schedule'}` (path `/`),
`{name:'bookings'}` (`/bookings`), `{name:'profile'}` (`/profile`). Sidebar nav:
**Расписание** (`/`), **Брони** (`/bookings`), **Профиль** (`/profile`) + a
logout control. `App.tsx` redirects unauthenticated users to `/login` and
authenticated users away from `/login`.

## Auth

Mirrors `event-admin-frontend/src/modules/auth`:

- `AuthProvider` holds the JWT in React state + `sessionStorage`
  (`event_organizer_jwt`); at startup `getValidStoredToken()` drops an expired
  token (`jwt.ts` decodes the `exp` claim).
- `useAuth()` → `{ isAuthenticated, jwtToken, loginWithToken, logout }`.
- **Login**: `LoginPage` posts `{email, password}` to `POST /auth/login`
  (`auth:false`); `200 {access_token}` → `loginWithToken(token)` → navigate `/`.
  **No TOTP.** `401` → «Неверный email или пароль».
- **Logout is client-side only** — the BFF has no logout endpoint. `logout()`
  clears storage + state and navigates to `/login`.
- `apiRequest<T>()` (in `shared/api.ts`): prepends `VITE_API_BASE_URL`, sets JSON
  headers, attaches `Authorization: Bearer <token>` when `auth:true` (default),
  throws `ApiError` (`.status`, `.details` parsed from the BFF's `{"detail":...}`
  body) on non-2xx. **On `401` for a request that carried a token → clear the
  session and redirect to `/login`**; requests with `auth:false` (login) never
  redirect.
- **No client-side role logic** — the BFF's JWT is organizer-scoped by
  construction; there is nothing to check client-side.

## Screens

### Schedule editor (`/`)

`GET /api/me/schedule` → bundle
`{ schedule:{name,time_zone}, weekly_hours[], date_overrides[], travel_schedules[] }`.
**`404` → start with an empty editor** (no schedule yet); the first save creates
it via the upsert PUT. Base `time_zone` defaults from the profile when creating.

```
Расписание                                            [Сохранить]
─ Часы по неделям ───────────────────────────────────────────
  Пн  [✓]  09:00 – 12:00  ✕
           14:00 – 18:00  ✕        + интервал
  Вт  [✓]  09:00 – 18:00  ✕        + интервал
  Ср  [ ]  Недоступно
  …
  Вс  [ ]  Недоступно

─ Исключения по датам ───────────────────────────────────────
  25 июля 2026   10:00 – 14:00           ✕
  26 июля 2026   Весь день недоступен    ✕
  + Добавить дату

─ Поездки (временный часовой пояс) ──────────  [Сохранить поездки]
  01.08 – 10.08   Asia/Dubai              ✕
  + Добавить поездку
```

- **Weekly hours** — 7 rows Mon–Sun (`day_of_week` 1–7, ISO). Each day has a
  toggle: on → one or more `HH:MM–HH:MM` intervals (add/remove interval); off →
  «Недоступно» (no rows emitted for that day).
- **Date overrides** — «+ Добавить дату» → pick a date, then either set
  `HH:MM–HH:MM` hours or check «весь день недоступен» (emits `start_time=null,
  end_time=null` = full-day block). Remove per row.
- **Base** — schedule `time_zone` (the `TimeZoneField` combobox); `name` is
  preserved from the loaded bundle, or defaults to «Моё расписание» on create —
  not a prominent field.
- **Save** — «Сохранить» → `PUT /api/me/schedule
  { name, time_zone, weekly_hours, date_overrides }` (atomic upsert). **Travel**
  is its own section with its own «Сохранить поездки» → `PUT
  /api/me/schedule/travel` (a separate BFF endpoint that replaces all travel
  rows). Each travel row: `start_date`, optional `end_date`, `time_zone`;
  `prev_time_zone` defaults to the schedule's base tz.
- **Pure helpers** (`schedule.ts`, unit-tested): map bundle ⇄ editor state, build
  the upsert payload, and validate (per-day interval overlap, `start < end`,
  valid IANA tz). Client validation blocks the PUT with an inline message; the
  server `422` is the backstop.

### Bookings (`/bookings`)

`GET /api/me/bookings` → a **projected** list `{id, start_time, end_time,
status}` only (the BFF intentionally leaks no participant ids/contact info).
Render two groups — **Предстоящие** / **Прошедшие** — split by `start_time` vs
now; each row shows the time range (ru-RU, organizer's tz from profile) and a
status badge (confirmed / cancelled). Empty state when there are none.

### Profile + Password (`/profile`)

Two cards on one page:

- **Профиль** — `GET /api/me/profile` → `{name, email, time_zone}`. Form: `name`
  (editable), `time_zone` (`TimeZoneField`), `email` (read-only). Save → `PUT
  /api/me/profile { name, time_zone }` (the BFF forwards only these two fields).
- **Пароль** — form `{old_password, new_password, confirm}` (client checks
  new==confirm and non-empty). Save → `PUT /api/me/password
  { old_password, new_password }`; `204` → «Пароль изменён»; `401` → «Неверный
  текущий пароль».

## BFF endpoints consumed

| Method | Path | Body / result |
|---|---|---|
| POST | `/auth/login` | `{email,password}` → `{access_token}`; `401` bad creds |
| GET | `/api/me/schedule` | → bundle; `404` = no schedule yet |
| PUT | `/api/me/schedule` | `{name,time_zone,weekly_hours,date_overrides}` (upsert) |
| PUT | `/api/me/schedule/travel` | travel list per the BFF travel schema (`[{time_zone,start_date,end_date,prev_time_zone}]`); replaces all. Exact envelope key pinned from `event-organizer`'s travel router at implementation time. |
| GET | `/api/me/bookings` | → `[{id,start_time,end_time,status}]` |
| GET | `/api/me/profile` | → `{name,email,time_zone}` |
| PUT | `/api/me/profile` | `{name,time_zone}` |
| PUT | `/api/me/password` | `{old_password,new_password}` → `204`; `401` bad old pw |

All `/api/me/*` require the `Authorization: Bearer <jwt>` the frontend attaches.

## Error handling

All copy in Russian. `apiRequest` throws `ApiError(status, details)`:

- `401` on login → inline «Неверный email или пароль».
- `401` elsewhere (token present) → clear session + redirect `/login`.
- `404` on `GET /api/me/schedule` → empty editor (not an error to the user).
- `409` / `422` → inline field/section message from the BFF `detail`.
- `502` (upstream) → «Сервис временно недоступен. Попробуйте ещё раз.»
- network / other → generic retry message.

## Testing

vitest + happy-dom, `createRoot` + `act` (no testing-library) — mirrors
booker/admin.

- **Pure helpers** (`schedule.ts`): bundle⇄editor mapping, upsert-payload build,
  validation (overlap, `start<end`, tz) — table-driven unit tests.
- **Components**: LoginPage (submit → `loginWithToken`, 401 message), SchedulePage
  (load bundle → rows; add/remove interval; toggle a day; add override incl.
  full-day; save issues PUT with the exact body; travel save hits the travel
  endpoint), BookingsPage (groups upcoming/past, empty state), ProfilePage
  (profile save body = name+tz only; password save + 401 message).
- Mock `fetch`/`apiRequest`; assert request bodies and rendered output.

## Prerequisite (setup task, not deferred)

`event-organizer` was crashlooping (`password authentication failed for user
"event_organizer"` — the DB role was not seeded in the shared postgres). Before
a browser smoke, this must be fixed and an organizer provisioned:

1. Ensure the `event_organizer` role/DB exists in the shared postgres (init or
   `CREATE ROLE`) so `event-organizer` starts.
2. Provision a test organizer: `POST /admin/organizers` with
   `Authorization: Bearer <ORGANIZER_ADMIN_KEY>` and `{user_id, email, password}`
   for an email that already exists in `event-users` as `role=organizer`.
3. Then log in through the SPA and exercise schedule / bookings / profile.

## Deliverables

- New `event-organizer-frontend/` package (source, tests, Dockerfile,
  nginx.conf, docker-entrypoint env-config, `.env.example`, `CLAUDE.md`).
- `docker-compose.services.yml` gains an `event-organizer-frontend` service
  (`3003:80`, build args, `window._env_` entrypoint, `depends_on: event-organizer`).
- The `event_organizer` DB-role seed fix so the BFF starts.
- Docs: this spec, and an `event-organizer-frontend/CLAUDE.md`.
