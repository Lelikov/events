# event-scheduling: Dependencies

## Runtime Dependencies

| Dependency | Purpose | Failure mode |
|------------|---------|--------------|
| PostgreSQL (`event_scheduling` DB) | Sole datastore; all 8 domain tables | All API requests fail (5xx); `/ready` returns `503`. `/health` still `200` (liveness only). |

No RabbitMQ, no background tasks, no external HTTP calls at runtime. The service is
pure HTTP over a single database.

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
| event-booking (planned, slice 3) | Read schedule data during slot calculation | Reads DB directly or via API |
| Prometheus | `GET /metrics` | Scrape job `event-scheduling` |
| Orchestrator / probes | `GET /health`, `GET /ready` | Liveness / readiness |

## Outbound at Runtime

None. The service makes no outbound HTTP or AMQP calls during normal operation.

## Configuration

| Env var | Required | Default | Meaning |
|---------|----------|---------|---------|
| `POSTGRES_DSN` | yes | — | asyncpg URL, e.g. `postgresql+asyncpg://event_scheduling:event_scheduling@postgres:5432/event_scheduling` |
| `SCHEDULING_API_KEY` | yes | — | Static bearer key gating `/api/v1/*` |
| `LOG_LEVEL` | no | `INFO` | One of DEBUG/INFO/WARNING/ERROR/CRITICAL |
| `DEBUG` | no | `false` | Console (vs JSON) log rendering |

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
`booking` table. Until then, this service reports no organizer conflicts.
