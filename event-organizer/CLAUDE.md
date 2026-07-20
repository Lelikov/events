# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

**Run the server:**
```bash
uvicorn event_organizer.main:app --reload --port 8006
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
docker run -d --rm --name org-testpg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=event_organizer -p 5601:5432 postgres:16
TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5601/event_organizer' uv run pytest
```

**Pre-commit hooks:**
```bash
pre-commit run --all-files
```

**Alembic migrations:**
```bash
alembic revision --autogenerate -m "description"
alembic upgrade head
alembic downgrade -1
```

**Configuration:** Requires a `.env` file (or the compose defaults — see `docker-compose.services.yml`).

## Architecture

`event-organizer` is the **organizer cabinet BFF** (slice 6.1): an organizer logs in
with email + password and manages their **own** schedule, views their **own**
bookings, and edits their **own** profile (name/timezone) + password. It holds no
domain data of its own beyond login credentials — it authenticates the organizer,
then proxies to `event-scheduling` (schedule, bookings) and `event-users` (profile),
injecting the authenticated organizer's `user_id` server-side. It is the front door
a future organizer-facing SPA (slice 6.2) will call; no such SPA exists yet.

**Ownership by construction.** Every `/api/me/*` endpoint takes its resource id from
`me.user_id` — the `user_id` decoded out of the caller's JWT (`auth/identity.py::require_organizer`).
No endpoint accepts an owner/host/user id in its path or body. This makes the
slice-5 IDOR class (a caller supplying someone else's id) structurally impossible
for organizer-facing schedule/booking/profile access: there is no id parameter to
tamper with.

**Auth stack** (mirrors event-admin's, without TOTP): `auth/password.py::PasswordService`
(bcrypt hash/verify) + `auth/jwt.py` (`create_access_token`/`decode_token`, HS256,
`exp`, optional `aud`/`iss`) + `auth/identity.py::require_organizer` (plain FastAPI
`Depends`, not a Dishka-injected dependency — reads `get_settings()` directly since
`Depends()` callables aren't wrapped by `DishkaRoute`).

**Request flow (login):**
`routers/auth.py POST /auth/login` → `services/login_service.py::LoginService.login`
→ `credentials/adapter.py::CredentialAdapter.get_by_email` (own DB) → `PasswordService.verify`
→ `create_access_token` → `{access_token}`.

**Request flow (admin provisioning):**
`routers/admin.py POST /admin/organizers` (static `Authorization: Bearer ORGANIZER_ADMIN_KEY`,
compared via `hmac.compare_digest`) → `services/provisioning_service.py::ProvisioningService.create`
→ `IUsersClient.resolve_organizer` (event-users `GET /api/users/by-identity?email=&role=organizer`,
`404` → `None` → not provisioned, `ValidationError` → `422`; a resolved id that doesn't match the
caller-supplied `user_id` also raises `ValidationError` → `422`) → `PasswordService.hash` →
`ICredentialAdapter.create` (own DB, `409` on duplicate email/user_id).

**Request flow (`/api/me/*`):**
`routers/me.py` → `require_organizer` (JWT → `OrganizerIdentity{user_id, email}`) → either
`ISchedulingClient` (`adapters/scheduling_client.py`, Bearer `SCHEDULING_API_KEY`) for
`schedule`/`schedule/travel`/`bookings`, or `IUsersClient`/`ProfileService`/`PasswordChangeService`
for `profile`/`password`.

**Layers:**

- **`routers/auth.py`** — `POST /auth/login`; no auth required.
- **`routers/admin.py`** — `POST /admin/organizers`; static admin-key bearer auth
  (`ORGANIZER_ADMIN_KEY`), not JWT.
- **`routers/me.py`** — `GET/PUT /api/me/schedule`, `PUT /api/me/schedule/travel`,
  `GET /api/me/bookings`, `GET/PUT /api/me/profile`, `PUT /api/me/password`; all
  gated by `require_organizer` (JWT bearer).
- **`routes.py`** — ops endpoints (`/health`, `/ready`, `/metrics`), no auth.
- **`services/login_service.py`** — `LoginService.login`: verify credentials, mint JWT.
- **`services/provisioning_service.py`** — `ProvisioningService.create`: validates
  the email is a real organizer in event-users, hashes the password, inserts the
  credential row.
- **`services/profile_service.py`** — `ProfileService.get`/`update`: thin mapping over
  `IUsersClient.get_user`/`patch_user`. `update` forwards **only** `name` + `time_zone`
  to event-users — never `email`/`role`.
- **`services/password_change_service.py`** — `PasswordChangeService.change`:
  re-verifies the old password before hashing/storing the new one.
- **`auth/password.py`** — `PasswordService` (bcrypt hash/verify), copied from
  `event-admin/event_admin/services/password.py`.
- **`auth/jwt.py`** — `create_access_token`/`decode_token` (HS256, `exp`, optional
  `aud`/`iss`); raises `Unauthorized` on any decode failure.
- **`auth/identity.py`** — `OrganizerIdentity` (frozen: `user_id`, `email`) +
  `require_organizer` (extracts+decodes the `Authorization: Bearer` header).
- **`credentials/adapter.py`** — `CredentialAdapter`: raw SQL CRUD on
  `organizer_credential` via `ISqlExecutor` (`get_by_email`, `create`,
  `update_password_hash`); duplicate email/user_id → `ConflictError` (409).
- **`credentials/dto.py`** — `OrganizerCredentialDTO` (frozen: `id`, `user_id`,
  `email`, `password_hash`, `disabled`).
- **`adapters/scheduling_client.py`** — `SchedulingClient`: Bearer `SCHEDULING_API_KEY`
  httpx client for `GET/PUT /api/v1/schedules/{owner_user_id}`, `PUT …/travel`,
  `GET /api/v1/bookings?host_user_id=`. `404` → `NotFoundError`; `422` →
  `ValidationError` carrying the upstream `detail` (so a domain rejection like
  non-whole-hour schedule times surfaces to the editor, not a generic 502); any
  other non-2xx → `UpstreamError`.
- **`adapters/users_client.py`** — `UsersClient`: Bearer `EVENT_USERS_TOKEN` httpx
  client for `GET /api/users/id/{user_id}`, `PATCH /api/users/id/{user_id}`,
  `GET /api/users/by-identity?email=&role=organizer` (returns the resolved `UUID | None`,
  `404` → `None`, never raises for the not-found case).
- **`adapters/sql.py`** — `SqlExecutor`, copied verbatim from event-scheduling.
- **`errors.py`** — `Unauthorized`(401)/`Forbidden`(403)/`NotFoundError`(404)/
  `ConflictError`(409)/`ValidationError`(422)/`UpstreamError`(502); mapped to HTTP
  status in `main.py`'s exception handlers.
- **`ioc.py`** — Dishka container. APP scope: `Settings`, `AsyncEngine`,
  `async_sessionmaker`, `PasswordService`, `ISchedulingClient` (`SchedulingClient`),
  `IUsersClient` (`UsersClient`). REQUEST scope: `AsyncSession`, `ISqlExecutor`,
  `ICredentialAdapter` (`CredentialAdapter`), `LoginService`, `ProvisioningService`,
  `ProfileService`, `PasswordChangeService`.
- **`db/` (via alembic only)** — no ORM models in the app code; `alembic/versions/`
  defines `organizer_credential` directly with raw SQLAlchemy Core (`sa.Column`/`op.create_table`).

## Database Tables (1)

| Table | Description |
|-------|-------------|
| `organizer_credential` | Login credential per organizer. `user_id` (UNIQUE) is the event-users UUID this cabinet acts on behalf of; `email` (UNIQUE) is the login identity; `password_hash` is bcrypt; `disabled` soft-disables login without deleting the row. Migration `0001_organizer_credential`. |

## Endpoints

| Method | Path | Auth | Behaviour |
|--------|------|------|-----------|
| POST | `/auth/login` | none | Body `{email, password}`. `200 {access_token}` on success; `401` on unknown email, disabled credential, or wrong password. |
| POST | `/admin/organizers` | static `Authorization: Bearer ORGANIZER_ADMIN_KEY` | Body `{user_id, email, password}`. Resolves `email` to the real event-users organizer id (`422` if not an organizer, `422` if it doesn't match the supplied `user_id`), hashes the password, inserts the credential. `201 {id, user_id, email}`; `409` on duplicate email/user_id; `401` on wrong admin key. |
| GET | `/api/me/schedule` | JWT bearer | Proxies `GET /api/v1/schedules/{me.user_id}` on event-scheduling; `404` if the organizer has no schedule yet. |
| PUT | `/api/me/schedule` | JWT bearer | Body `{name, time_zone, weekly_hours, date_overrides}`. Proxies `PUT /api/v1/schedules/{me.user_id}` (upsert); `name` is required by event-scheduling. |
| PUT | `/api/me/schedule/travel` | JWT bearer | Proxies `PUT /api/v1/schedules/{me.user_id}/travel`. |
| GET | `/api/me/bookings` | JWT bearer | Proxies `GET /api/v1/bookings?host_user_id={me.user_id}`; returns a projected `{id, start_time, end_time, status}` list only — no other participant's ids or contact info leak through. |
| GET | `/api/me/profile` | JWT bearer | Proxies `GET /api/users/id/{me.user_id}` on event-users; returns `{name, email, time_zone}`. |
| PUT | `/api/me/profile` | JWT bearer | Body `{name, time_zone}` **only**. Proxies `PATCH /api/users/id/{me.user_id}` with exactly those two fields — never forwards `email`/`role` even though event-users' PATCH contract accepts them. |
| PUT | `/api/me/password` | JWT bearer | Body `{old_password, new_password}`. Re-verifies `old_password` against the stored hash before updating; `401` on mismatch. `204` on success. |
| GET | `/health` | none | Liveness — no deps. |
| GET | `/ready` | none | Static readiness — no DB check. |
| GET | `/metrics` | none | Prometheus exposition. |

Error codes: `401 Unauthorized`, `403 Forbidden`, `404 NotFoundError`, `409 ConflictError`, `422 ValidationError`, `502 UpstreamError`.

## Configuration

| Env var | Meaning |
|---------|---------|
| `POSTGRES_DSN` | asyncpg URL for the service's own `event_organizer` DB |
| `JWT_SECRET_KEY` | HS256 signing secret for the session JWT (dev default is a ≥32-char placeholder — **must** be overridden with a real secret outside dev) |
| `JWT_ALGORITHM` | default `HS256` |
| `JWT_EXPIRE_MINUTES` | session token lifetime, default `60` |
| `JWT_AUDIENCE` / `JWT_ISSUER` | optional `aud`/`iss` claims, unset by default |
| `ORGANIZER_ADMIN_KEY` | static bearer key gating `POST /admin/organizers` |
| `EVENT_SCHEDULING_URL` | base URL of event-scheduling, e.g. `http://event-scheduling:8888` |
| `SCHEDULING_API_KEY` | Bearer key sent to event-scheduling's `/api/v1/*` — must match that service's own `SCHEDULING_API_KEY` |
| `EVENT_USERS_URL` | base URL of event-users, e.g. `http://event-users:8888` |
| `EVENT_USERS_TOKEN` | Bearer token sent to event-users — must match that service's own `API_BEARER_TOKEN` |
| `LOG_LEVEL` | Log level (default `INFO`) |
| `DEBUG` | Console log rendering (default `false`) |

## Deferred (out of scope for slice 6.1)

- **Frontend.** No SPA exists yet — this is the BFF only. The organizer-facing UI
  is slice 6.2.
- **TOTP / password reset / self-registration.** Login is password-only (no
  second factor); there is no "forgot password" flow; organizers are provisioned
  only via the admin-key-gated `POST /admin/organizers` — there is no self-serve
  sign-up.
- **Event-types and calendars.** `event-scheduling`'s `/api/v1/event-types` and
  `/api/v1/calendars` endpoints are **not** fronted here — those remain
  admin/other-owned surfaces. This BFF's scheduling proxy covers only schedule +
  travel + bookings.
- **Login rate-limiting/lockout.** No brute-force guard on `/auth/login` in this slice.

## Service Documentation

Cross-service architecture docs live in the monorepo root `../docs/`.
