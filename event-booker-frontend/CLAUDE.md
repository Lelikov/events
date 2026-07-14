# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Role

`event-booker-frontend` is a **public** React/Vite SPA that lets a guest self-book a meeting.
It talks **only** to the `event-booker` BFF (never directly to event-scheduling or
event-users) via a same-origin nginx proxy in production. There is **no authentication**
anywhere in this app — `event-booker` is the trust boundary, and the fetch wrapper never
attaches an `Authorization` header or redirects to a login page.

## Commands

```bash
npm run dev       # Start dev server (Vite)
npm run build     # Type-check + production build
npm run lint      # Run ESLint
npm test          # Run Vitest unit tests (happy-dom)
npm run preview   # Preview production build locally
```

## Environment Variables

All backend traffic goes to event-booker only. There is no `.env.example` in this package
(unlike `event-admin-frontend`) — the one variable below is a build arg wired directly in
`docker-compose.services.yml`.

| Variable | Description |
|---|---|
| `VITE_API_BASE_URL` | event-booker backend base URL (e.g. `http://localhost:8005`); empty (default) = same-origin, nginx proxies `/api` and `/health` to `event-booker:8888` |

## Architecture

### Routing

There is no router library. Routing is implemented manually in `src/modules/shared/routing.ts`,
mirroring `event-admin-frontend`'s pattern:
- `parseRoute(pathname)` returns a typed `AppRoute` discriminated union
- `navigateTo(path)` calls `history.pushState`/`replaceState` and dispatches `app:navigate` on `window`
- `App.tsx` listens to `popstate` and `app:navigate` events to re-render on navigation

**Two routes**:
- `/` (or `/event-types`) — `EventTypeListPage`: lists bookable event types
- `/book/{id}` — `BookingFlowPage`: the booking wizard for one event type

### The booking flow (`src/modules/booking/`)

`BookingFlowPage` drives a 3-step wizard for a given `eventTypeId`:
1. **Slot** (`SlotPicker`) — fetches available slots for a 14-day window (`GET
   /api/public/slots?event_type_id=&start=&end=&time_zone=`); a "later" control advances the
   window by 14 days (capped at 62). Time zone is auto-detected via
   `Intl.DateTimeFormat().resolvedOptions().timeZone` and editable.
2. **Details** (`GuestForm`) — collects name + email, then `POST /api/public/bookings`
   (`{event_type_id, name, email, start_time, time_zone}`).
3. **Confirmation** (`Confirmation`) — shows the booking result (`booking_id`,
   `event_type_title`, `start_time`, `end_time`, `status`, `time_zone`).

A `409` response on submit (slot just taken) bounces the user back to step 1 with an inline
banner; `422` shows a field-level submit error; other failures show a generic retry message.

### API layer

`src/modules/shared/api.ts` → `apiRequest<T>()` is the only fetch wrapper: it prepends
`VITE_API_BASE_URL`, sets JSON headers, and throws `ApiError` (with `.status` and `.details`,
parsed from the BFF's `{"detail": "..."}` error body) for non-2xx responses. It does **not**
attach any auth header and does **not** redirect on 401/403 — there is no login flow in this
app. `src/modules/booking/bookerApi.ts` wraps the four `event-booker` public endpoints
(`listEventTypes`, `getEventType`, `getSlots`, `createBooking`).

### Module structure (`src/modules/`)

| Module | Purpose |
|---|---|
| `booking/` | `bookerApi.ts` (API client + types), `datetime.ts` (slot formatting), `EventTypeListPage`, `SlotPicker`, `GuestForm`, `Confirmation`, `BookingFlowPage` |
| `shared/` | `apiRequest` fetch wrapper (no auth), `runtimeEnv` (`window._env_` reader), `routing`, `ErrorBoundary` |

### Deploy

nginx (prod, `Dockerfile` + `nginx.conf`) same-origin-proxies `/api/*` and `/health` to
`event-booker:8888`; `docker-entrypoint.d/40-env-config.sh` writes `window._env_` at
container start (runtime config, same pattern as `event-admin-frontend`). Host port **3002**
(container 80) in `docker-compose.services.yml`.

## Conventions

- **No `else if`** — use early returns, guard clauses, or mapping objects instead of `else if` chains
- **Avoid `else`** — prefer early returns. Use `else` only when both branches are truly symmetric
- Plain CSS only (no Tailwind/styled-components/emotion)
- UI copy is in Russian

## Observability

Gated Sentry error + performance monitoring (`src/observability/sentry.ts`), off unless
`VITE_SENTRY_ENABLED === 'true'` and a DSN is configured — same pattern as
`event-admin-frontend`/`jitsi-chat`.

## Deferred (not built in this slice)

- Cancel / reschedule flows
- Payments
- An i18n framework (UI copy is hardcoded Russian, not translated)

## Service Documentation

Cross-service architecture docs (message contracts, system topology, onboarding) are in `../docs/`.
See `../docs/architecture/ONBOARDING.md` § "event-booker-frontend (public Booker SPA)" for the
public booking chain (browser → event-booker-frontend → event-booker → event-scheduling /
event-users) and `../docs/architecture/ONBOARDING.md` § "event-booker (public booking BFF)"
for the BFF's contract.

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
|------|----------|
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
