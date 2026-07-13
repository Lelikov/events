# event-scheduling: Dependencies

## Runtime Dependencies

| Dependency | Purpose | Failure mode |
|------------|---------|--------------|
| PostgreSQL (`event_scheduling` DB) | Sole datastore; all 11 domain tables | All API requests fail (5xx); `/ready` returns `503`. `/health` still `200` (liveness only). |
| event-receiver `POST /event/booking` (slice 4a, NEW) | Background dispatcher publishes `booking.created`/`rescheduled`/`cancelled` CloudEvents for event-saver to project | Outbox row stays `pending`/retries on network error or any non-`202` status except `400`/`401` (which mark it `failed`, no further retries). **The booking write-path (`POST /api/v1/bookings` and friends) is NOT on this call path** — it only writes an `outbox` row in its own transaction and returns; the HTTP request never blocks on or fails because of event-receiver. |
| event-users `POST /api/users/by-ids` (slice 4a, NEW) | Background dispatcher resolves `host_user_id`/`client_user_id` UUIDs → email/time_zone before building the CloudEvent | A network error, non-2xx (including a `require_admin` `401`), or ids simply not found leaves the outbox row `pending` and retrying with backoff — see "Admin token prerequisite" below. Same as above: this is dispatcher-only, never on the booking write-path. |

No RabbitMQ, no FastStream consumer. **The booking write-path itself remains
dependency-free** — `POST/GET /api/v1/bookings*` never call event-receiver or
event-users synchronously; both new HTTP calls above happen only inside the
background dispatcher task (`publishing/dispatcher.py`, started in `main.py`'s
`lifespan`), on its own poll interval (`OUTBOX_DISPATCH_INTERVAL`, default 5s).

### Admin token prerequisite (`EVENT_USERS_TOKEN`)

event-users' `POST /api/users/by-ids` is gated by `require_admin` (a `Bearer`
token event-users recognizes as an admin/service caller). `event-scheduling`
must be configured with a **valid admin token** in `EVENT_USERS_TOKEN` for
email resolution to succeed — a placeholder or wrong value causes every
resolution attempt to `401`, which the dispatcher's generic exception handling
treats as transient (retry with backoff, not `failed`), so outbox rows for real
bookings will retry forever and never reach `sent` without silently corrupting
anything. Whether a long-lived static token is accepted by `require_admin`, or
whether it expects a real JWT, depends on event-users' own auth implementation —
verify against the deployed event-users instance; this is a genuine deploy
prerequisite, not a code defect in event-scheduling.

## ETL-time Dependencies

The one-time ETL (`scripts/etl_from_calcom.py`) additionally requires:

| Dependency | Purpose |
|------------|---------|
| cal.com PostgreSQL | Source of `Schedule`, `Availability`, `users` tables |
| **event-users API (indirect)** | The ETL caller must resolve organizer emails → UUIDs by querying event-users (`GET /api/users/by-email` or equivalent) before invoking `run_etl`. The ETL itself accepts a `resolve_email_to_uuid` callback — no direct HTTP call is hardcoded. |

## Callers (Inbound)

| Caller | Calls | Notes |
|--------|-------|-------|
| event-admin-frontend / admin UI | Schedule and event-type CRUD via event-admin proxy (planned) | Not yet wired; bearer key required |
| Booker UI (planned, slice 5) | `GET /api/v1/slots` to display available slots to participants | Not yet built |
| event-booking (planned, slice 4a.2) | Would read/act on the `booking.lifecycle` CloudEvent (or the cal.com DB today) to drive chat/Jitsi/reminders for `event-scheduling` bookings | Not yet built — see the "event-booking no-op" note in `event-scheduling/CLAUDE.md` |
| Prometheus | `GET /metrics` | Scrape job `event-scheduling` |
| Orchestrator / probes | `GET /health`, `GET /ready` | Liveness / readiness |

## Outbound at Runtime

**NEW (slice 4a):** the background outbox dispatcher makes two outbound HTTP calls
per booking-lifecycle event — event-users `POST /api/users/by-ids` (resolve emails),
then event-receiver `POST /event/booking` (publish the CloudEvent). See "Runtime
Dependencies" above for the exact contracts and failure modes. Outside of that
dispatcher task, the service still makes no outbound HTTP or AMQP calls — in
particular, no request handler in `routers/` ever calls out synchronously.

## Configuration

| Env var | Required | Default | Meaning |
|---------|----------|---------|---------|
| `POSTGRES_DSN` | yes | — | asyncpg URL, e.g. `postgresql+asyncpg://event_scheduling:event_scheduling@postgres:5432/event_scheduling` |
| `SCHEDULING_API_KEY` | yes | — | Static bearer key gating `/api/v1/*` |
| `LOG_LEVEL` | no | `INFO` | One of DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `DEBUG` | no | `false` | Console (vs JSON) log rendering |
| `EVENT_RECEIVER_URL` | no | `http://event-receiver:8888` | Dispatcher-only: base URL for `POST /event/booking` |
| `BOOKING_API_KEY` | no* | `dev-booking-api-key` | Dispatcher-only: raw shared secret sent as `Authorization` to event-receiver — *must match event-receiver's own `BOOKING_API_KEY`* for publishing to succeed at all (a mismatch is a hard `401` → outbox row `failed`, not a retry) |
| `EVENT_USERS_URL` | no | `http://event-users:8888` | Dispatcher-only: base URL for `POST /api/users/by-ids` |
| `EVENT_USERS_TOKEN` | no* | `dev-admin-token` | Dispatcher-only: `Bearer` token for event-users' `require_admin`-gated endpoint — needs to be a **real admin token** for email resolution to succeed in any non-toy environment (see "Admin token prerequisite" above) |
| `OUTBOX_DISPATCH_INTERVAL` | no | `5.0` | Seconds between dispatcher poll ticks |
| `OUTBOX_BATCH_SIZE` | no | `50` | Max outbox rows claimed per tick |
| `OUTBOX_MAX_BACKOFF_SECONDS` | no | `300` | Cap on the exponential retry backoff |

\* No field validator enforces these at startup (unlike `POSTGRES_DSN`/`SCHEDULING_API_KEY`,
which are required) — the service starts fine with the defaults, but the dispatcher will
never successfully publish/resolve anything against a real event-receiver/event-users
until they're set to matching real values.

## Build / Deploy

- Dockerfile build context is the `event-scheduling` directory (no dependency on
  `event-schemas` — pure HTTP service, no RabbitMQ).
- `entrypoint.sh` runs `alembic upgrade head`, then
  `uvicorn event_scheduling.main:app --host 0.0.0.0 --port 8888`.
- The service owns its schema; the container is the single migration runner.

## BusyTimesSource (Stub)

`interfaces/busy_times.py` defines the `BusyTimesSource` Protocol — the extension
point for real busy-time data. The `StubBusyTimesSource` always returns `[]`.
Slice 3 (write-side bookings) will provide a real implementation backed by the
`booking` table. Until then, the slot engine (`GET /api/v1/slots`) reports no
organizer conflicts — all schedule time is considered free.

**Consequence for `GET /api/v1/slots` callers (slice 2 maturity):** slots are
computed from schedule data only. No existing bookings are subtracted. Do not
rely on slot results to detect double-booking until slice 3 is shipped.
