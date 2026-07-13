# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Run the server:**
```bash
uvicorn event_booker.main:app --reload --port 8005
```

**Lint and format:**
```bash
uv run ruff check .
uv run ruff format .
```

**Install dependencies:**
```bash
uv sync
```

**Tests:**
```bash
uv run pytest
```
No database, no external processes — all upstream calls (`event-scheduling`, `event-users`) are
faked via `httpx.MockTransport` in `tests/conftest.py`. 23 tests: health (3) + `SchedulingClient` (6)
+ `UsersClient` (5) + `GuestBookingService` (3) + `/api/public/*` (6).

**Configuration:** All settings have dev defaults baked in (see `config.py`); no `.env` required
locally. Override via env vars — see the table below.

## Architecture

**BFF (Backend-for-Frontend) pattern.** `event-booker` (slice 4b.1) is a stateless FastAPI service
that exposes an **unauthenticated public API** for guests to book a meeting slot, without ever
handing the browser the internal service credentials. It sits in front of `event-scheduling`
(schedules/event-types/slots/bookings) and `event-users` (user identity), holding
`SCHEDULING_API_KEY` and `EVENT_USERS_TOKEN` server-side. No database, no RabbitMQ, no background
tasks — pure request/response, mirroring `event-scheduling`'s app skeleton (telemetry/metrics/
logger/routes/config) minus DB/alembic/lifespan.

**Request flow:**
`routers/public.py` → `ISchedulingClient` / `GuestBookingService` (`services/guest_booking.py`) →
`ISchedulingClient` + `IUsersClient` (`interfaces/clients.py`) → `adapters/scheduling_client.py` /
`adapters/users_client.py` (raw `httpx.AsyncClient`, Bearer auth) → `event-scheduling` / `event-users`

**Layers:**

- **`routers/public.py`** — the 4 public endpoints, all under `/api/public`, wired via Dishka
  (`DishkaRoute`/`FromDishka`). No auth dependency on this router — see "Trust boundary" below.
- **`routes.py`** — ops endpoints (`/health`, `/ready`, `/metrics`), also unauthenticated.
- **`schemas/public.py`** — Pydantic request/response models (`EventTypeModel`,
  `EventTypeListResponse`, `SlotsPublicResponse`, `CreateBookingPublicRequest`,
  `BookingConfirmationResponse`), each with a `from_dto`/`from_result`/`from_confirmation`
  classmethod that maps from the internal frozen DTOs — this is the seam that keeps internal ids
  (`client_user_id`, `host_user_id`) and raw upstream response bodies out of public responses.
- **`dto.py`** — frozen dataclasses: `EventTypeDTO`, `SlotsResult`, `BookingResult`,
  `BookingConfirmation` (the public-facing confirmation: `booking_id`, `event_type_title`,
  `start_time`, `end_time`, `status`, `time_zone` — no user ids).
- **`services/guest_booking.py`** — `GuestBookingService.book`: resolves-or-creates the guest as a
  `client` user, then creates the booking; see "Guest→client resolution" below.
- **`interfaces/clients.py`** — `ISchedulingClient`, `IUsersClient` Protocols.
- **`adapters/scheduling_client.py`** — `SchedulingClient`: `list_event_types`, `get_event_type`,
  `get_slots`, `create_booking`; Bearer `SCHEDULING_API_KEY`.
- **`adapters/users_client.py`** — `UsersClient`: `get_client_by_email`, `create_client`; Bearer
  `EVENT_USERS_TOKEN`; role is hard-coded `"client"` (`_CLIENT_ROLE`), never taken from the request.
- **`errors.py`** — `DomainError` base + `ValidationError`, `NotFoundError`, `ConflictError`,
  `SlotUnavailableError`, `UpstreamError`; mapped to HTTP status in `main.py`.
- **`ioc.py`** — Dishka `AppProvider`, APP scope: `Settings`, `ISchedulingClient`
  (`SchedulingClient`), `IUsersClient` (`UsersClient`), `GuestBookingService`. No REQUEST-scoped
  providers (no DB session to open per request).
- **`main.py`** — app assembly: Dishka container, tracing, HTTP metrics middleware, CORS
  (only added when `BOOKER_CORS_ORIGINS` is non-empty), and the domain-error → HTTP-status
  exception handlers.
- **`config.py`** — `Settings` (pydantic-settings); dev defaults for every upstream URL/key so the
  service runs out of the box against the compose stack.
- **`telemetry.py`, `metrics.py`, `logger.py`** — copied from `event-scheduling`, imports renamed.

## Endpoints

| Method | Path | Auth | Behaviour |
|--------|------|------|-----------|
| GET | `/api/public/event-types` | none | List all event types: `{"items":[{"id","slug","title","duration_minutes"}]}`, proxied from `event-scheduling GET /api/v1/event-types` |
| GET | `/api/public/event-types/{event_type_id}` | none | Single event type; `404` if `event-scheduling` returns 404 |
| GET | `/api/public/slots` | none | Query params `event_type_id`, `start`, `end`, `time_zone`; proxies `event-scheduling GET /api/v1/slots`; response `{"event_type_id","time_zone","slots":{"<date>":["<iso>"]}}` |
| POST | `/api/public/bookings` | none | Body `{"event_type_id","name","email","start_time","time_zone"}` → resolves/creates the guest as a `client` user, then creates the booking (`actor_source: booker` header to event-scheduling); `201` `{"booking_id","event_type_title","start_time","end_time","status","time_zone"}`; `409` if the slot was taken concurrently; `404` unknown event type |
| GET | `/health` | none | Liveness — no deps |
| GET | `/ready` | none | Static readiness — `{"status":"ready"}`, no upstream check |
| GET | `/metrics` | none | Prometheus exposition |

Error mapping (`main.py`): `ValidationError`→422, `NotFoundError`→404, `ConflictError`→409,
`SlotUnavailableError`→409, `UpstreamError`→502 (any unexpected/non-success upstream response).

## Guest→Client Resolution

`GuestBookingService.book` (`services/guest_booking.py`):
1. `UsersClient.get_client_by_email(email)` → `GET /api/users/by-identity?email=&role=client` on
   event-users. `200` → reuse the existing user id. `404` → fall through to create.
2. `UsersClient.create_client(email, name, time_zone)` → `POST /api/users` with
   `{"email","name","role":"client","time_zone"}`. `201` → new user id. `409` (duplicate) surfaces
   as a `ConflictError` — a race where another request created the same email between steps 1 and 2.
3. `SchedulingClient.create_booking(event_type_id, client_user_id, start_time, time_zone)` →
   `POST /api/v1/bookings` on event-scheduling with header `actor_source: booker`.
4. `SchedulingClient.get_event_type(event_type_id)` is fetched again to read the title for the
   confirmation response.

The guest's role is **hard-fixed to `"client"`** in `adapters/users_client.py` (`_CLIENT_ROLE`) —
never read from the request body, so a guest can never provision themselves as an `organizer`/host.

## Trust Boundary

`event-booker` is the **public trust boundary** in front of `event-scheduling` and `event-users`:
it is designed to be reachable directly from an untrusted public browser (no `Authorization`
header required on any `/api/public/*` or ops route). It holds `SCHEDULING_API_KEY` and
`EVENT_USERS_TOKEN` server-side only — never returned in a response body, never logged. Response
schemas (`schemas/public.py`) are hand-mapped from internal DTOs, so upstream fields like
`client_user_id`/`host_user_id` and raw event-scheduling/event-users response bodies cannot leak
through even if an upstream response gains new fields.

## Configuration

| Env var | Meaning |
|---------|---------|
| `EVENT_SCHEDULING_URL` | Base URL of event-scheduling, e.g. `http://event-scheduling:8888` |
| `SCHEDULING_API_KEY` | Bearer token sent to event-scheduling's `/api/v1/*` — **must match** event-scheduling's own `SCHEDULING_API_KEY` |
| `EVENT_USERS_URL` | Base URL of event-users, e.g. `http://event-users:8888` |
| `EVENT_USERS_TOKEN` | Bearer token sent to event-users, gated by `require_admin` — **must match** event-users' own `API_BEARER_TOKEN` |
| `BOOKER_CORS_ORIGINS` | Comma-separated allowed CORS origins for the future public SPA; empty (default) = CORS middleware not added at all |
| `LOG_LEVEL` | Log level (default `INFO`) |
| `DEBUG` | Console log rendering (default `false`) |
| `OTEL_*` | OpenTelemetry export config, gated by `OTEL_SDK_DISABLED` (default `true`) — same pattern as every other Python service |

## Deferred / Not Yet Built

- **No frontend.** The public SPA that will call `/api/public/*` is slice 4b.2 — not part of this
  slice.
- **No cancel/reschedule.** Only `POST /api/public/bookings` (create) is exposed; event-scheduling
  has cancel/reschedule endpoints but this BFF does not proxy them yet.
- **No anti-abuse hardening.** The public API has no rate-limiting, CAPTCHA, or per-IP throttling —
  deferred to a future slice.
- **No Helm chart / CI wiring.** This slice only adds local docker-compose wiring; Kubernetes
  manifests and the GitHub Actions/GitLab CI image-publish pipelines are not yet set up for
  `event-booker`.

## Service Documentation

No `docs/` directory yet (no SERVICE_OVERVIEW/API_CONTRACTS/DATA_MODEL/DEPENDENCIES/AUDIT) — this
is a small, single-slice service; this `CLAUDE.md` is the only doc.

Cross-service architecture docs live in the monorepo root `../docs/`.
