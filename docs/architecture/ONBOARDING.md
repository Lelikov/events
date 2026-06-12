# Onboarding Guide

Welcome to the events monorepo. This guide covers everything you need to know before making your first change.

---

## The 5 Most Important Things to Understand

### 1. CloudEvents binary mode is the universal message format

Every inter-service message uses CloudEvents binary mode. Event metadata lives in headers (`ce-type`, `ce-source`, `ce-id`, `ce-time`, `ce-booking_id`, `ce-specversion`) and the payload is the JSON body. If you are adding a new event type, you must produce and consume messages in this format -- there are no exceptions.

### 2. event-saver owns the database; event-admin is read-only

event-saver is the sole writer to the main PostgreSQL database. All schema migrations live in `event-saver/alembic/`. event-admin connects to the same database but is strictly read-only. Never create migrations in event-admin.

### 3. Routing rules are first-match and order matters

`EventRouter.resolve_routing_key_by_fields()` returns the first matching rule. If you add a new routing rule, its position relative to existing rules determines whether it will ever fire. Rules added above more specific patterns will shadow them. This is the root cause of the most critical known bug in the system (see audit finding C-1).

### 4. All Python services share the same architectural patterns

Every Python service uses:
- **Dishka** for dependency injection (DI container in `ioc.py`)
- **Protocol-based interfaces** in `interfaces/` for loose coupling
- **SqlExecutor** (`adapters/sql.py`) for raw SQL via `AsyncSession`
- **Frozen dataclasses** as DTOs between layers
- **Ruff** (line length 120) for linting/formatting

### 5. event-notifier is new and incomplete

event-notifier is the newest service. It has no migration framework (raw SQL bootstrap only), no delivery result publishing (documented but not implemented), and requires FCM credentials even though push notifications are disabled. The queue name mismatch with event-receiver (C-3) has been resolved — it now correctly defaults to `events.notification.commands`. Treat it as pre-alpha.

---

## How to Run the Full System Locally

### Quick start: one command (recommended)

The root `docker-compose.yml` starts everything — all 9 services, RabbitMQ,
four PostgreSQL containers (saver/users/notifier/cal.com fixture) and a
WireMock container mocking every external HTTP API (Shortify, UniSender Go,
Telegram, GetStream):

```bash
docker compose up -d --build    # from the repo root; no .env required
docker compose ps               # wait until everything is (healthy)
docker compose down -v          # tear down, including volumes
```

Dev-grade defaults for every variable are baked into `docker-compose.yml`
(mirrored in `.env.example`). Copy `.env.example` to `.env` and override only
what you change — e.g. set `CALCOM_DATABASE_URL` to a real cal.com PostgreSQL
DSN to integrate with an external cal.com instead of the seeded `pg-calcom`
fixture, or swap the `mocks` endpoints (`SHORTENER_URL`, `UNISENDER_BASE_URL`,
`TELEGRAM_BASE_URL`, `CHAT_BASE_URL`) for real APIs.

Entry points (defaults): event-receiver `:8888`, event-users `:8001`,
event-admin `:8002`, admin frontend `:3000`, jitsi-chat `:8080`, WireMock
request journal `:8089/__admin/requests`, RabbitMQ management `:15672`,
Prometheus `:9090` (127.0.0.1 only), Grafana `:3001` (admin/admin).

Every published host port is an env var with the default above — override in
`.env` (or inline) without touching the compose file:

| Variable | Default | Service |
|---|---|---|
| `RECEIVER_PORT` | 8888 | event-receiver |
| `USERS_PORT` | 8001 | event-users |
| `ADMIN_PORT` | 8002 | event-admin |
| `ADMIN_FRONTEND_PORT` | 3000 | event-admin-frontend |
| `JITSI_CHAT_PORT` | 8080 | jitsi-chat |
| `RABBITMQ_AMQP_PORT` | 5672 | rabbitmq (127.0.0.1 only) |
| `RABBITMQ_MGMT_PORT` | 15672 | rabbitmq management (127.0.0.1 only) |
| `PG_CALCOM_PORT` | 5433 | pg-calcom fixture DB (127.0.0.1 only) |
| `MOCKS_PORT` | 8089 | WireMock (127.0.0.1 only) |
| `PROMETHEUS_PORT` | 9090 | prometheus (127.0.0.1 only) |
| `GRAFANA_PORT` | 3001 | grafana (login `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD`, default admin/admin) |

CORS origins, `MEETING_HOST_URL` and `VITE_WEBHOOK_URL` defaults are derived
from these vars inside `docker-compose.yml`, and `scripts/calcom_sim.py`
reads `RECEIVER_PORT` / `PG_CALCOM_PORT` from `.env` — overriding a port in
`.env` keeps the whole stack (and the simulator) consistent.

### Health probes (k8s liveness / readiness / startup)

Every service follows one convention, designed to map 1:1 onto Kubernetes
probes:

| Probe | Endpoint | Semantics |
|---|---|---|
| `livenessProbe` | `GET /health` | Process is up and serving HTTP. **Never** calls dependencies; always a cheap `200 {"status": "ok"}`. |
| `readinessProbe` | `GET /ready` | Checks critical dependencies. `200 {"status": "ready", "checks": {...}}` or `503 {"status": "not_ready", "checks": {...}}` with a per-check boolean map. |
| `startupProbe` | — | In compose, modeled by the healthcheck `start_period` (no failures counted while the service boots). The compose healthchecks hit `/health`. |

Per-service endpoints and what `/ready` verifies:

| Service | `/health` | `/ready` checks |
|---|---|---|
| event-receiver | shallow 200 | `rabbitmq` (broker ping) |
| event-saver | shallow 200 | `database` (PostgreSQL `SELECT 1`) |
| event-booking | shallow 200 | `database` (cal.com PostgreSQL), `rabbitmq` (broker ping) |
| event-users | shallow 200 | `database` (PostgreSQL `SELECT 1`) |
| event-admin | shallow 200 | `database` (PostgreSQL `SELECT 1`) |
| event-notifier | shallow 200 | `consumer` (started), `outbox_sender` (task alive), `database` |
| event-admin-frontend | nginx returns `200 "ok"` | — (static SPA; no readiness deps) |
| jitsi-chat | Caddy returns `200 "ok"` | — (static SPA; no readiness deps) |

All `/health` and `/ready` endpoints are unauthenticated by design (probes
cannot carry tokens).

The manual per-service workflow below is still useful when iterating on a
single service against the rest of the stack.

### Prerequisites

- Python 3.14 with `uv` installed
- Node.js (for event-admin-frontend)
- Docker and Docker Compose (for PostgreSQL and RabbitMQ)

### Step 1: Start infrastructure

```bash
# Start RabbitMQ (from event-receiver)
cd event-receiver && docker-compose up -d rabbit

# Start PostgreSQL for event-saver (main DB)
cd event-saver && docker-compose up -d postgres

# Start PostgreSQL for event-users (separate DB)
# NOTE: uses the same default port 5432 -- change POSTGRES_PORT in .env
cd event-users && POSTGRES_PORT=5446 docker-compose up -d postgres
```

### Step 2: Install dependencies (each service)

```bash
# Python services
cd event-receiver && uv sync
cd event-saver && uv sync
cd event-admin && uv sync
cd event-users && uv sync
cd event-notifier && uv sync
cd event-schemas && pip install -e ".[dev]"

# Frontend
cd event-admin-frontend && npm install
```

### Step 3: Run database migrations

```bash
# Main DB (event-saver owns this)
cd event-saver && alembic upgrade head

# Users DB
cd event-users && alembic upgrade head
```

### Step 4: Start services

```bash
# event-receiver (port 8888)
cd event-receiver && uvicorn event_receiver.main:app --host 0.0.0.0 --port 8888

# event-saver (port 8889 or any available -- mainly a consumer, HTTP is secondary)
cd event-saver && uvicorn event_saver.main:app --host 0.0.0.0 --port 8889

# event-admin (port 8000)
cd event-admin && uvicorn event_admin.main:app --reload

# event-users (port 8001)
cd event-users && uvicorn event_users.main:app --reload --port 8001

# event-notifier (port 8002)
cd event-notifier && uvicorn event_notifier.main:app --reload --port 8002

# event-admin-frontend (port 5173 by default)
cd event-admin-frontend && npm run dev
```

### Step 5: Configure .env files

Each service needs its own `.env`. Copy from `.env.example` where available. Key variables:

| Service | Critical env vars |
|---|---|
| event-receiver | `RABBIT_URL`, `RABBIT_EXCHANGE`, JWT keys |
| event-saver | `POSTGRES_DSN`, `RABBIT_URL` |
| event-admin | `POSTGRES_DSN` (same DB as event-saver), `JWT_SECRET_KEY` |
| event-users | `POSTGRES_DSN` (separate DB), `JWT_SECRET_KEY`, `CRM_ENCRYPTION_KEY` |
| event-notifier | `RABBIT_URL`, `EVENT_RECEIVER_URL`, `EVENT_USERS_URL`, `UNISENDER_API_KEY`, `TELEGRAM_BOT_TOKEN`, `FCM_PROJECT_ID`, `FCM_SERVICE_ACCOUNT_JSON` |
| event-admin-frontend | `VITE_API_BASE_URL`, `VITE_USERS_API_BASE_URL` |

---

## Observability (Prometheus + Grafana)

The compose stack ships a full metrics pipeline (design spec:
`docs/superpowers/specs/2026-06-13-prometheus-grafana-metrics-design.md`).

### What is collected

- **HTTP RED** (every FastAPI service): `http_requests_total{method, route, status}`
  and `http_request_duration_seconds` — `route` is always the route template,
  never the raw path (cardinality). `/health` and `/metrics` are excluded.
- **Consumer RED** (saver / booking / notifier / users):
  `messages_processed_total{queue, event_type, outcome}` (outcome ok/retried/rejected)
  and `message_processing_seconds{queue}`. Services share metric names and are
  distinguished by the Prometheus `job` label.
- **Business counters** (service-prefixed): `receiver_webhooks_total{source,result}`,
  `saver_events_total{event_type}`, `saver_booking_lifecycle_total{action}`,
  `booking_rejections_total{rejection_type}`, `booking_blacklist_checks_total{result}`,
  `notifier_deliveries_total{channel,trigger,outcome}`, `notifier_outbox_depth{status}`
  (+ oldest-pending-age gauge), `users_crm_sync_*`, `admin_logins_total{outcome}`,
  `admin_blacklist_ops_total{op}`, and more — see each service's `*/metrics.py`.
- **Infrastructure**: RabbitMQ via the `rabbitmq_prometheus` plugin
  (`rabbitmq_queue_messages{queue}` incl. `*.dlq` depths; per-object metrics
  enabled in `docker/rabbitmq/20-prometheus.conf`) and the four PostgreSQL DBs
  via one `postgres_exporter` container each (`db` label: saver/users/notifier/calcom).

Every Python service serves `GET /metrics` on the same port as `/health` (8888
in-container). Scrape config: `docker/prometheus/prometheus.yml` (15s interval).

### Dashboards

Grafana provisions a `Prometheus` datasource (uid `prometheus`) and two
dashboards from `docker/grafana/dashboards/` into the **Events** folder:

| Dashboard | uid | Contents |
|---|---|---|
| Events — System Overview | `events-system-overview` | per-target up, HTTP RED, consumer RED, queue + DLQ depths, PostgreSQL connections |
| Events — Booking Flow | `events-booking-flow` | funnel webhooks → events → bookings created/rejected → notifications, blacklist checks, outbox depth/age, processing p95 |

Open http://localhost:3001 (admin/admin), or query Prometheus directly at
http://localhost:9090. Dashboard JSON edits in the repo are picked up within
~30s (file provider); UI edits are not written back — export and commit them.

### How to add a metric

1. Define it in the service's `metrics.py` (module-level `prometheus_client`
   `Counter`/`Gauge`/`Histogram`; business metrics get a `<service>_` prefix,
   bounded label values only — never raw paths, emails or ids).
2. Increment it where the event happens; add a unit test that the counter moves.
3. Chart it: edit the dashboard JSON in `docker/grafana/dashboards/` (or edit in
   the Grafana UI and export back into the repo).
4. New service? Add a scrape job in `docker/prometheus/prometheus.yml`.

## Minimum Viable Setup

Not every service is needed for every task. Use this table to decide what to run:

| Service | Depends on | Can skip if... |
|---|---|---|
| event-receiver | RabbitMQ | You are only working on the admin UI or read-only API |
| event-saver | RabbitMQ, PostgreSQL (main) | You are only working on event-users or frontend styling |
| event-admin | PostgreSQL (main, same as event-saver) | You are working on ingestion or notification pipeline |
| event-admin-frontend | event-admin, event-users | You are working on backend services only |
| event-users | PostgreSQL (users) | You are working on event ingestion without user lookups |
| event-notifier | RabbitMQ, event-users, event-receiver | You are not working on notifications |
| event-schemas | None (library, imported at install time) | Never -- always install it first |

**Minimum viable for booking data flow**: event-receiver + RabbitMQ + event-saver + PostgreSQL (main).

---

## How to Run Tests

| Service | Command | Notes |
|---|---|---|
| event-notifier | `cd event-notifier && uv run pytest` | Has test infrastructure with pytest-asyncio, respx, and mocks |
| event-receiver | No test suite | No tests exist |
| event-saver | No test suite | No tests exist |
| event-admin | No test suite | No tests exist |
| event-admin-frontend | No test runner configured | `npm run build` does type-checking only |
| event-users | No test suite | No tests exist |
| event-schemas | No test suite | Relies on strict typing; no runtime tests |

**Reality check**: Only event-notifier has any test infrastructure. The audit explicitly calls out the complete absence of tests as a systemic issue (finding L-1).

---

## How to Inspect RabbitMQ Queues Locally

RabbitMQ Management UI is exposed by the docker-compose in `event-receiver/`:

- **URL**: http://localhost:15672
- **Username**: `guest`
- **Password**: `guest`

From the management UI you can:
- View queue depths (Queues tab)
- Inspect message contents (Get Messages button on a queue)
- See exchange bindings (Exchanges tab > click exchange > Bindings)
- Purge queues during development

To verify routing, publish a test event to event-receiver and watch which queue receives it.

---

## How to Run a Database Migration

### event-saver (main database -- bookings, events, participants, projections)

```bash
cd event-saver

# Apply all pending migrations
alembic upgrade head

# Create a new migration from model changes
alembic revision --autogenerate -m "add column X to table Y"

# Downgrade one step
alembic downgrade -1

# View migration history
alembic history
```

### event-users (users database -- users, user_contacts)

```bash
cd event-users

# Apply all pending migrations
alembic upgrade head

# Create a new migration
alembic revision --autogenerate -m "description"

# Downgrade one step
alembic downgrade -1
```

### event-notifier (no migration framework)

event-notifier uses raw SQL in `db/schema.py` to bootstrap its tables. There is no Alembic setup, no version tracking, and no rollback capability.

---

## Common Mistakes New Developers Make

### 1. Creating migrations in event-admin

**Wrong.** event-admin is read-only. All migrations belong in `event-saver/alembic/`. If you need a new table that event-admin reads, create the migration in event-saver.

### 2. Assuming event-notifier still uses the old `events.notifications` queue

The queue mismatch bug (C-3) has been resolved. `event-notifier/config.py` now defaults to `events.notification.commands`, which is the correct queue. The old `events.notifications` queue is orphaned and no longer consumed. Do not configure `NOTIFICATIONS_QUEUE=events.notifications`.

### 3. Adding routing rules without understanding first-match semantics

Rules in `event-receiver/config.py:_default_route_rules()` are evaluated top-to-bottom. The first match wins. If you add a broad glob pattern above a specific one, the specific rule will never fire. Always add new rules in the correct position.

### 4. Forgetting to add EventType entries to both EVENT_PRIORITIES and EVENT_SCHEMA_VERSIONS

When adding a new event type to `event-schemas/event_schemas/types.py`, you must add entries to three places:
1. The `EventType` enum
2. `EVENT_PRIORITIES` dict
3. `EVENT_SCHEMA_VERSIONS` dict

Missing entries cause silent fallback to defaults. There is no compile-time check enforcing completeness.

### 5. Referencing `ioc_new.py` in event-saver

The file `ioc_new.py` does not exist and was never created. `ioc.py` is the current and only DI container. The CLAUDE.md for event-saver incorrectly references `ioc_new.py` as a planned file -- ignore those references.

### 6. Using `event-saver`'s EventType enum values in event-receiver or event-schemas

event-saver has its own `EventType` enum with different string values (e.g., `"booking.events.v1.booking.created.create"`) compared to event-schemas (`"booking.created"`). These are incompatible. Use the event-schemas definitions for new work.

### 7. Expecting event-users `.env.example` to have correct values

The `.env.example` in event-users has wrong database name (`zhivaya-admin` instead of `zhivaya-users`) and wrong port (`5439` instead of `5446`). Do not blindly copy it.

### 8. Assuming SqlExecutor provides transactional safety

`SqlExecutor.execute()` auto-commits after every statement. Multiple SQL operations in a single use case are NOT atomic. Each call commits independently. This is a known critical bug (C-5) that has not yet been fixed.

### 9. Adding write methods to event-admin adapters

event-admin should have zero write operations. Any write method you add will bypass the intended data ownership model where event-saver is the sole writer.

### 10. Pointing event-admin-frontend at wrong user API URL

The frontend calls `GET /api/users/${id}` for user lookups but event-users expects `GET /api/users/id/{user_id}`. The URL mismatch is a known bug (H-15).

---

## Glossary

| Term | Definition |
|---|---|
| **CloudEvent** | A standardized event envelope (CNCF spec). In this system, always used in binary mode: metadata in HTTP/AMQP headers, payload in the body. |
| **ce-* headers** | CloudEvents headers: `ce-type` (event type string), `ce-source` (origin system), `ce-id` (unique event ID), `ce-time` (ISO timestamp), `ce-booking_id` (correlation ID), `ce-specversion` (always "1.0"). |
| **booking_id** | The primary correlation ID linking events to a specific booking session. Carried in `ce-booking_id` header and stored in the events table. |
| **participant** | A user involved in a booking -- either an organizer or a client. Stored in the `participants` table with email as unique key. |
| **projection** | A derived/denormalized view computed from raw events. event-saver computes projections for meetings, notifications, chat, and video as events arrive. Each projection is an independent handler. |
| **trigger_event** | A string (e.g., `"BOOKING_CREATED"`) passed in notification payloads that maps to channel-specific templates (email template codes, Telegram message bodies). |
| **ChannelContact** | A domain model in event-notifier representing a user's contact point on a specific channel (email address, Telegram chat ID, push token). |
| **DLQ (Dead Letter Queue)** | A RabbitMQ queue where messages that fail processing are sent. event-receiver declares DLQs for its queues; event-saver and event-notifier do not (known gap). |
| **outbox pattern** | event-notifier writes pending notifications to an `outbox` table, then a background sender delivers them and updates status. Provides at-least-once delivery semantics. |
| **SqlExecutor** | A thin wrapper around SQLAlchemy `AsyncSession` used in all Python services. Executes raw `text()` SQL and returns `RowMapping` results. Currently auto-commits (known bug). |
| **Dishka** | The dependency injection framework used across all Python services. Containers are defined in `ioc.py` with APP and REQUEST scopes. |
| **Protocol** | Python `typing.Protocol` classes used as interfaces in `interfaces/` directories. Enables loose coupling between layers without abstract base classes. |
| **EventType** | An enum mapping event names to string values. Defined in event-schemas (canonical) and duplicated with different values in event-saver (legacy). |
| **EventPriority** | Integer priority (1-10) assigned to event types for RabbitMQ queue priority. Defined in `EVENT_PRIORITIES` dict in event-schemas. |
| **routing key** | The RabbitMQ routing key used to direct messages to queues. In this system, routing keys equal queue names (e.g., `events.booking.lifecycle`). |
| **topology** | The set of RabbitMQ exchanges, queues, and bindings. Managed by `ITopologyManager` in event-receiver; declared at startup. |
| **CRM sync** | Background task in event-users that periodically fetches encrypted user data from an external CRM API, decrypts it (AES-256-CBC), and upserts into the local database. |
| **TOTP** | Time-based One-Time Password. Used for admin login in event-admin alongside email/password. |
