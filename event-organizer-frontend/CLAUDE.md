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
