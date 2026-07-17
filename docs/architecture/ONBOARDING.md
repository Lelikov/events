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

### 5. event-notifier manages channel config in the database

event-notifier's `notification_bindings` table controls which channels fire for each trigger
event and which template to use. The table is seeded by Alembic migration
`003_notification_bindings` (from `UNISENDER_TEMPLATE_IDS` env + repo `.j2` files) and is
then edited at runtime through the event-admin "Уведомления" UI — no redeploy needed. An
in-memory cache (`BINDINGS_CACHE_TTL_SECONDS`, default 30 s) keeps delivery fast; admin edits
invalidate it immediately.

---

## How to Run the Full System Locally

### Quick start: one command (recommended)

The root `docker-compose.yml` starts everything — all 10 services, RabbitMQ,
five PostgreSQL containers (saver/users/notifier/shortener/cal.com fixture) and
a WireMock container mocking the remaining external HTTP APIs (UniSender Go,
Telegram, GetStream). The former `/shortify` WireMock stub was replaced by the
real `event-shortener` service:

```bash
docker compose up -d --build    # 10 services + infra; no .env required
docker compose --profile observability up -d --build   # + monitoring stack (see below)
docker compose ps               # wait until everything is (healthy)
docker compose down -v          # tear down, including volumes
```

The monitoring stack (Prometheus, Grafana, Alertmanager, postgres exporters) is
in the **`observability` compose profile**, OFF by default. Start it with the
second command, or set `COMPOSE_PROFILES=observability` in `.env` so the bare
`up` includes it. RabbitMQ always exposes its `/metrics` plugin on `:15692`
regardless of the profile (nothing scrapes it until Prometheus is up).

Dev-grade defaults for every variable are baked into `docker-compose.yml`
(mirrored in `.env.example`). Copy `.env.example` to `.env` and override only
what you change — e.g. set `CALCOM_DATABASE_URL` to a real cal.com PostgreSQL
DSN to integrate with an external cal.com instead of the seeded `pg-calcom`
fixture, or swap the `mocks` endpoints (`SHORTENER_URL`, `UNISENDER_BASE_URL`,
`TELEGRAM_BASE_URL`, `CHAT_BASE_URL`) for real APIs.

Entry points (defaults): event-receiver `:8888`, event-users `:8001`,
event-admin `:8002`, event-shortener `:8000`, admin frontend `:3000`,
jitsi-chat `:8080`, WireMock
request journal `:8089/__admin/requests`, RabbitMQ management `:15672`,
Prometheus `:9090` (127.0.0.1 only), Grafana `:3001` (admin/admin),
Alertmanager `:9093` (127.0.0.1 only), VictoriaLogs `:9428` (127.0.0.1 only) —
the last four only with the `observability` profile enabled.

Every published host port is an env var with the default above — override in
`.env` (or inline) without touching the compose file:

| Variable | Default | Service |
|---|---|---|
| `RECEIVER_PORT` | 8888 | event-receiver |
| `USERS_PORT` | 8001 | event-users |
| `ADMIN_PORT` | 8002 | event-admin |
| `SHORTENER_PORT` | 8000 | event-shortener |
| `ADMIN_FRONTEND_PORT` | 3000 | event-admin-frontend |
| `JITSI_CHAT_PORT` | 8080 | jitsi-chat |
| `RABBITMQ_AMQP_PORT` | 5672 | rabbitmq (127.0.0.1 only) |
| `RABBITMQ_MGMT_PORT` | 15672 | rabbitmq management (127.0.0.1 only) |
| `PG_CALCOM_PORT` | 5433 | pg-calcom fixture DB (127.0.0.1 only) |
| `MOCKS_PORT` | 8089 | WireMock (127.0.0.1 only) |
| `PROMETHEUS_PORT` | 9090 | prometheus (127.0.0.1 only) |
| `GRAFANA_PORT` | 3001 | grafana (login `GRAFANA_ADMIN_USER`/`GRAFANA_ADMIN_PASSWORD`, default admin/admin) |
| `ALERTMANAGER_PORT` | 9093 | alertmanager (127.0.0.1 only) |
| `VICTORIALOGS_PORT` | 9428 | victorialogs (127.0.0.1 only; logs UI + LogsQL API) |
| `LOGS_RETENTION_PERIOD` | 7d | victorialogs log retention |
| `TEMPO_PORT` | 3200 | Tempo (127.0.0.1 only; trace storage + Grafana datasource) |
| `OTEL_COLLECTOR_PORT` | 4317 | OTel Collector (127.0.0.1 only; OTLP/gRPC; services → collector → Tempo) |

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
| event-notifier | `RABBIT_URL`, `EVENT_RECEIVER_URL`, `EVENT_USERS_URL`, `UNISENDER_API_KEY`, `TELEGRAM_BOT_TOKEN`, `NOTIFIER_ADMIN_TOKEN`, `FCM_PROJECT_ID`, `FCM_SERVICE_ACCOUNT_JSON` |
| event-admin-frontend | `VITE_API_BASE_URL`, `VITE_USERS_API_BASE_URL` |

---

## Observability (Prometheus + Grafana)

The compose stack ships a full metrics pipeline (design spec:
`docs/superpowers/specs/2026-06-13-prometheus-grafana-metrics-design.md`),
bundled in the **`observability` compose profile** (Prometheus, Grafana,
Alertmanager, and the five postgres exporters). The same profile also ships
centralized **logging** — Vector + VictoriaLogs (see the *Logging* subsection
below). It is off by default; start it with
`docker compose --profile observability up -d` or set
`COMPOSE_PROFILES=observability` in `.env`. The app services always expose
`/metrics`, so enabling the profile later needs no rebuild of them.

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
  enabled in `docker/rabbitmq/20-prometheus.conf`) and the five PostgreSQL DBs
  via one `postgres_exporter` container each
  (`db` label: saver/users/notifier/shortener/calcom).

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

### Alerting (Alertmanager + Telegram)

Prometheus evaluates alert rules and pushes firing alerts to **Alertmanager**
(`alertmanager:9093`, exposed loopback-only at http://localhost:9093), which
routes them to a dedicated **ops** Telegram chat. Design spec:
`docs/superpowers/specs/2026-06-13-alertmanager-telegram-design.md`.

- **Where rules live**: `docker/prometheus/rules/` (mounted read-only into
  Prometheus; `rule_files: /etc/prometheus/rules/*.yml`).
  - `infra.yml` — `ServiceDown` (up==0, 1m, crit), `HighErrorRate` (5xx >5% /5m,
    warn), `HighLatencyP95` (p95 >1s /10m, warn), `DLQGrowing` (`*.dlq` >0 /5m,
    warn), `OutboxBacklog` (pending >100 /10m, warn), `OutboxStalled` (oldest
    pending >5m, crit), `RabbitMQDown`/`PostgresDown` (1m, crit).
  - `business.yml` — `BookingRejectionSpike` (rejections >0.2/s /10m, warn),
    `NotificationDeliveryFailures` (failed deliveries >0 /5m, warn).
- **Routing / severity**: alerts group by `alertname`+`job`. `severity=critical`
  notifies fast (`group_wait` 10s, `repeat_interval` 1h); `severity=warning`
  batches (`group_wait` 1m, `repeat_interval` 12h). Single Telegram receiver,
  HTML message with a severity emoji and a Grafana link. Config template:
  `docker/alertmanager/alertmanager.tmpl.yml` (rendered at container start by
  `docker/alertmanager/entrypoint.sh`, which `sed`-substitutes the env vars —
  Alertmanager does no env interpolation in its YAML).
- **Set the ops bot token + chat** (real delivery): create a dedicated bot via
  @BotFather (separate from event-notifier's client-facing `TELEGRAM_BOT_TOKEN`),
  then set `ALERT_TELEGRAM_BOT_TOKEN` and `ALERT_TELEGRAM_CHAT_ID` in `.env`.
  **Left blank → graceful degrade**: Alertmanager still starts and alerts still
  fire and route, but Telegram sends fail with a logged `401` (visible via
  `docker compose logs alertmanager`).
- **How to add a rule**: drop it in the right `docker/prometheus/rules/*.yml`
  with a `severity: warning|critical` label and `summary`/`description`
  annotations (use `{{ $labels.* }}` / `{{ $value }}`). Validate with
  `docker run --rm --entrypoint promtool -v $PWD/docker/prometheus/rules:/rules
  prom/prometheus promtool check rules /rules/*.yml`; Prometheus reloads on
  restart (or `POST /-/reload`). Verify in Prometheus → Alerts and at
  http://localhost:9093.

### Tracing (OpenTelemetry → Tempo)

Distributed tracing is bundled in the same `observability` profile and is **off by default**
(`OTEL_SDK_DISABLED=true` on every Python service). To enable it, start the profile with the
flag flipped:

```bash
OTEL_SDK_DISABLED=false docker compose --profile observability up -d --build
```

**Pipeline:** each Python service → OTLP/gRPC → **otel-collector** (batches, rate-limits,
stamps `deployment.environment=docker-compose`) → **Tempo** → **Grafana** (datasource uid
`tempo`). Tempo stores trace data locally for 7 days (same retention as VictoriaLogs).

**New loopback ports** (observability profile):

| Variable | Default | Component |
|---|---|---|
| `TEMPO_PORT` | 3200 | Tempo HTTP API + Grafana data source |
| `OTEL_COLLECTOR_PORT` | 4317 | OTel Collector OTLP/gRPC receiver (services → collector) |

Configs: `docker/tempo/tempo.yaml`, `docker/otel-collector/config.yaml`.

**Per-service env knobs** (all have defaults; only override when needed):

| Variable | Default | Meaning |
|---|---|---|
| `OTEL_SDK_DISABLED` | `true` | Set to `false` to activate tracing |
| `OTEL_SERVICE_NAME` | *(service name)* | Identifies the service in Tempo |
| `OTEL_EXPORTER_OTLP_ENDPOINT` | `http://otel-collector:4317` | Collector endpoint |
| `OTEL_TRACES_SAMPLER` | `parentbased_always_on` | Dev: sample everything; prod: use `parentbased_traceidratio` + `OTEL_TRACES_SAMPLER_ARG=0.1` |

**Instrumentation:** every Python service has an identical `telemetry.py` bootstrap that
auto-instruments FastAPI (HTTP SERVER/CLIENT spans), httpx (outbound HTTP CLIENT spans),
asyncpg (DB CLIENT spans), and FastStream RabbitMQ (PRODUCER/CONSUMER spans). Tracing is
fully no-op when disabled — no overhead in the base stack.

**Manual spans** are added around key business operations using the same `telemetry.py`
tracer. Pattern:

```python
from opentelemetry import trace
_tracer = trace.get_tracer(__name__)

with _tracer.start_as_current_span("booking.blacklist_check") as span:
    span.set_attribute("booking.uid", booking_uid)
    ... existing logic ...
```

Named manual spans in this codebase: `booking.blacklist_check`, `booking.chat_create`,
`booking.meeting_url_mint`, `booking.publish_followup` (event-booking);
`notifier.outbox_claim`, `notifier.channel_send` (event-notifier);
`saver.projection_execute` (event-saver); `receiver.validate_webhook` (event-receiver).

**Querying traces in Grafana:** open http://localhost:3001 → Explore → select the
**Tempo** datasource → Search. Example TraceQL queries:

```
{ resource.service.name = "event-receiver" }
{ resource.service.name = "event-booking" && span.booking.uid = "<uid>" }
{ duration > 500ms }
```

A verified end-to-end trace spans event-receiver → event-saver → event-booking →
event-shortener → event-notifier with HTTP SERVER/CLIENT, RabbitMQ PRODUCER/CONSUMER, and
DB CLIENT spans.

**Logs ↔ traces correlation:** structlog stamps `trace_id` and `span_id` (W3C 32-hex / 16-hex)
on every log line when a span is active. The VictoriaLogs datasource has a `trace_id` derived
field configured to jump from any log line directly to the matching Tempo trace. In practice
this means: find an error in the Logs dashboard → click the `trace_id` link → see the full
distributed trace.

Known minor limitation: a small number of event-receiver controller log calls are emitted
outside an active span (e.g. early startup lines); those lines retain a legacy UUID-format
`trace_id` rather than a W3C hex id. All log lines emitted during request handling carry the
correct W3C `trace_id` because a FastAPI server span is always active then.

**Trace context propagation:** W3C `traceparent` is injected by the OTel instrumentation on
every outbound HTTP call and every RabbitMQ publish, and extracted on every inbound HTTP
request and every RabbitMQ consume. It travels alongside the `ce-*` CloudEvent headers (see
`docs/architecture/MESSAGE_CONTRACTS.md` § Overview). `ce-traceid`/`ce-spanid` are derived
from the active span whenever one exists.

### Frontend observability (Sentry)

Both React/Vite SPAs (`event-admin-frontend`, `jitsi-chat`) ship `@sentry/react` for
**errors + performance monitoring** (no session replay). Key facts:

- **What's captured**: unhandled JS exceptions (via `ErrorBoundary.componentDidCatch` +
  `Sentry.captureException`) and page-load / fetch performance transactions
  (`browserTracingIntegration`). `sendDefaultPii: false` throughout.
- **Gating**: Sentry is initialized only when `VITE_SENTRY_ENABLED=true` AND a non-empty
  `VITE_SENTRY_DSN` are both present. The default (`VITE_SENTRY_ENABLED` absent / `false`)
  is fully off — no Sentry network calls in local dev or tests.
- **Runtime config via `window._env_`**: all `VITE_SENTRY_*` knobs are delivered at
  container start (nginx/Caddy entrypoint regenerates `env-config.js` / `env-config.js`
  from `^VITE_*` env vars), so one image serves every environment without a rebuild. In
  Kubernetes, the vars arrive via Vault → ESO → `envFrom` (same pattern as backend services).
- **Source-map upload in CI**: each SPA's `publish-image.yml` passes three repo secrets
  (`SENTRY_ORG`, `SENTRY_PROJECT`, `SENTRY_AUTH_TOKEN`) to the Docker build stage. The
  `@sentry/vite-plugin` uploads hidden source maps (`build.sourcemap: "hidden"`) to Sentry
  keyed to `release=<git sha>`, then deletes them from the output. Without a token (local
  build) the plugin is disabled and the build still succeeds.
- **Best-effort Tempo correlation**: `jitsi-chat` sets `VITE_SENTRY_BACKEND_URL` to the
  event-receiver public origin; `event-admin-frontend` relies on the same-origin nginx proxy
  so `VITE_SENTRY_BACKEND_URL` may be left empty (the `window.location.origin` target covers
  it). Sentry's `browserTracingIntegration` attaches a `sentry-trace` header to matching
  fetch calls. On the backend, a `SentryTracePropagator` in the shared `telemetry.py` (all
  seven Python services) extracts the `sentry-trace` header and continues the same 128-bit
  trace id into the OTel span, so the backend span is visible in Tempo under the Sentry
  transaction's trace id. This is best-effort: the link surfaces only for `event-admin` and
  `event-receiver` (the two services the SPAs call); the propagator is a no-op everywhere
  else.

**Runtime knobs** (all `VITE_*` so the container entrypoint picks them up):

| Variable | Purpose |
|---|---|
| `VITE_SENTRY_ENABLED` | `"true"` to activate; anything else → off |
| `VITE_SENTRY_DSN` | Project DSN from sentry.io (public-safe; empty placeholder in seed-vault.sh) |
| `VITE_SENTRY_ENVIRONMENT` | Environment tag on every event (`"production"`, `"staging"`, …) |
| `VITE_SENTRY_TRACES_SAMPLE_RATE` | 0–1 performance-sampling rate (prod default: `0.1`) |
| `VITE_SENTRY_BACKEND_URL` | *(jitsi-chat only)* event-receiver public origin for `sentry-trace` propagation |

**CI secrets** (build-time only, never shipped to the browser, never in Vault):
`SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT` — add as GitHub Actions repo secrets in
`Lelikov/event-admin-frontend` and `Lelikov/jitsi-chat` for source-map uploads to activate.

### Frontend design system (`events-design-system`)

The three SPAs (`event-admin-frontend`, `event-booker-frontend`, `jitsi-chat`) share
a single **light** visual language via `events-design-system`, a small library package
(CSS + generic React components — Icon, Switch, ErrorBoundary, Badge, UserInfoView).
Key facts:

- **Two CSS entry points, deliberately split**: `styles.css` is the full light
  stylesheet — tokens, reset, and component rules — used by `event-admin-frontend`
  and `event-booker-frontend`. `tokens.css` is CSS variables + font only, no reset
  and no component rules; `jitsi-chat` imports this narrower entry point because it
  embeds `stream-chat-react`, and the full sheet's global `button {}` element rule
  would collide with stream-chat's own buttons. `jitsi-chat` recolors its own chrome
  with the shared tokens and switches `stream-chat-react` to its light theme —
  it dropped its former dark theme to join the shared light brand.
- **Build**: components are built with `tsup` to `dist/` (both ESM output and type
  declarations); consumers import compiled output, not source.
- **Distribution**: consumed as a git-tag npm dependency
  (`github:Lelikov/events-design-system#vX.Y.Z`), with a `file:../events-design-system`
  link for local dev — the same model as `event-schemas`.

### Logging (Vector + VictoriaLogs)

In the same `observability` profile, **Vector** tails every container's
stdout/stderr via the Docker socket and ships the lines to **VictoriaLogs**,
which you query in Grafana alongside the metrics (design spec:
`docs/superpowers/specs/2026-06-13-log-aggregation-vector-victorialogs-design.md`).
No application changes — the seven Python services already emit structlog JSON
when `DEBUG=false`.

- **What's collected**: all 16 default containers. Vector's `docker_logs`
  source reads the lot (it excludes only itself to avoid a feedback loop). The
  Python services' JSON is parsed into structured fields (`level`, `_msg` from
  the structlog `event`, the structlog `timestamp`, plus callsite fields);
  plain-text infra logs (Postgres, RabbitMQ, nginx, Caddy, WireMock) pass
  through with the raw line as `_msg`. Vector also tags each line with
  `service` (from the compose service label), `container`, and `stream`.
- **Storage / retention**: VictoriaLogs keeps logs for `LOGS_RETENTION_PERIOD`
  (default **7d**) in the `victorialogs-data` volume. Built-in UI + LogsQL API
  at http://localhost:9428 (127.0.0.1 only). Config:
  `docker/vector/vector.yaml`.
- **Querying in Grafana**: the **VictoriaLogs** datasource (uid `victorialogs`,
  plugin `victoriametrics-logs-datasource` installed via `GF_INSTALL_PLUGINS`)
  powers the **Events — Logs** dashboard (uid `events-logs`: a `service`
  template variable, log volume by level, and a live logs panel) and Grafana
  **Explore**. Example LogsQL:
  - `service:event-booking level:error` — booking errors only
  - `service:event-saver _msg:~"projection"` — saver lines mentioning projection
  - `level:error` — every error across the stack
  - direct API: `curl -s 'http://127.0.0.1:9428/select/logsql/query'
    --data-urlencode 'query=service:event-receiver' --data-urlencode 'limit=5'`
- **Security note**: Vector mounts `/var/run/docker.sock` **read-only**, and
  VictoriaLogs is loopback-only with no auth. Fine for this dev/integration
  stack; for production, harden both (least-privilege socket proxy or a
  log-driver instead of the raw socket, and auth/TLS in front of VictoriaLogs).

## Managing Notification Templates

Per-trigger-event channel enablement and template selection are **admin-managed at runtime** —
no redeploy needed. Configuration is stored in `event-notifier`'s `notification_bindings`
table and edited through the **event-admin-frontend "Уведомления" page** (accessible to any
`admin`-role user after login).

### What the admin can do

- Enable or disable email / Telegram delivery per trigger event (7 events × 2 channels = 14
  controls).
- Pick the UniSender Go email template from a dropdown populated from the UniSender API
  (cached 1 h; "Обновить" forces a fresh fetch).
- Edit the Telegram Jinja2 message body inline and preview the rendered output before saving.

### Where config lives

`notification_bindings` in `pg-notifier` (the event-notifier PostgreSQL database). The table
is seeded once by Alembic migration `003_notification_bindings` from the
`UNISENDER_TEMPLATE_IDS` env and the `.j2` template files shipped in the repo. After seeding,
the DB is authoritative — the `.j2` files are not read at runtime.

### Env knobs

| Variable | Service | Default | Purpose |
|----------|---------|---------|---------|
| `NOTIFIER_ADMIN_TOKEN` | event-notifier + event-admin | *(required)* | Static service token for the notifier admin API; must match in both services |
| `BINDINGS_CACHE_TTL_SECONDS` | event-notifier | `30` | How long the in-memory bindings cache is valid before a DB re-read; admin `PUT` invalidates it immediately |
| `NOTIFIER_SERVICE_URL` | event-admin | `http://event-notifier:8888` | URL event-admin uses to reach event-notifier's admin API |

### v1 caveat — single locale

The current implementation stores one template per `trigger_event × channel`. The seed
uses `DEFAULT_LOCALE` (default `ru`). Selecting a different language or managing per-locale
templates is out of scope for v1.

---

## User sync (event-db-sync)

`event-db-sync` (the 11th service) replaces the old HTTP CRM poll with a **trigger-driven**
cal.com→event-users sync.

**What it does**
- Connects to the **cal.com DB** and idempotently applies (gated by `APPLY_TRIGGERS`) an additive
  `AFTER INSERT/UPDATE` trigger on `"Attendee"` and `"users"` that fires
  `pg_notify('user_sync', {"table","id"})`.
- On NOTIFY it re-SELECTs the row and publishes a **`user.upserted`** CloudEvent **directly to
  RabbitMQ** (source `db-sync`, priority CRITICAL, routing key `events.user.email` — it reuses the
  existing event-users queue; no event-receiver HTTP hop). Mapping: `"Attendee"` → `role=client`,
  `"users"` → `role=organizer`.
- **Reconcile**: a watermark sweep over its own small Postgres `sync_state` DB re-emits rows missed
  during downtime, and serves as the one-time cutover backfill.
- **Full-sync**: `POST /admin/full-sync` (bearer `SYNC_ADMIN_TOKEN`, `?source=attendee|users|all`)
  forces a full pass with cal.com as the source of truth.

Downstream: event-users consumes `user.upserted`, upserts the user (`upsert_user_from_crm`,
`ON CONFLICT (email, role)`, updates `time_zone`) and publishes **`user.synced`** directly to
RabbitMQ (`events.user.synced`, saver-owned, CRITICAL) with the resolved `user_id`; event-saver
backfills `bookings.{organizer,client}_user_id` by participant email (the HTTP-poll
`UserIdBackfillService` stays as a slow safety net).

**cal.com trigger caveat**: the NOTIFY trigger is a **sanctioned additive integration hook**, NOT a
cal.com schema migration — it adds no columns/tables and does not alter cal.com's data model
(cal.com still owns its schema). This is the one place a non-cal.com service touches the cal.com DB
structure, and only via an additive trigger.

**Compose**: service `event-db-sync` (host `:8003` → container `:8888`) + `pg-db-sync`
(`127.0.0.1:5437`, the `sync_state` DB).

**Env vars**: `DATABASE_URL` (own `sync_state` DB), `CALCOM_DATABASE_URL` (plain `postgresql://`),
`RABBIT_URL`, `SYNC_ADMIN_TOKEN`, `APPLY_TRIGGERS`, `RECONCILE_*`, `FULL_SYNC_*`.

---

## event-booker (public booking BFF)

`event-booker` (slice 4b.1, port 8005) is a stateless FastAPI **BFF
(Backend-for-Frontend)** in front of `event-scheduling` and `event-users`. It exposes an
**unauthenticated public API** so an eventual public browser client can turn a guest (name +
email) into a scheduling booking, without the browser ever holding
`SCHEDULING_API_KEY`/`EVENT_USERS_TOKEN` — the BFF holds both server-side.

**The 4 public endpoints** (all under `/api/public`, no auth):
- `GET /event-types` — list bookable event types
- `GET /event-types/{id}` — single event type
- `GET /slots?event_type_id=&start=&end=&time_zone=` — available slots for an event type
- `POST /bookings` — body `{event_type_id, name, email, start_time, time_zone}`; resolves-or-creates
  the guest as a `client` user (`event-users GET /api/users/by-identity` → `POST /api/users` on
  miss), then creates the booking (`event-scheduling POST /api/v1/bookings`, header
  `actor_source: booker`); `201` with a confirmation that carries no internal user ids

Upstream calls: `SchedulingClient` (Bearer `SCHEDULING_API_KEY` → event-scheduling's
`/api/v1/event-types*`, `/api/v1/slots`, `/api/v1/bookings`) and `UsersClient` (Bearer
`EVENT_USERS_TOKEN` → event-users' `/api/users/by-identity`, `/api/users`). No database, no
RabbitMQ, no background tasks.

The public-facing frontend that calls these endpoints is `event-booker-frontend` (slice 4b.2, below).

---

## event-booker-frontend (public Booker SPA)

`event-booker-frontend` (slice 4b.2, port 3002) is a public React/Vite SPA that lets a guest
self-book a meeting. It talks **only** to `event-booker` — never directly to
event-scheduling or event-users — via a same-origin nginx proxy (`/api/*` → `event-booker:8888`),
mirroring `event-admin-frontend`'s Dockerfile/nginx/`window._env_` pattern but with **no
auth**: the fetch wrapper never attaches an `Authorization` header or redirects to a login page,
since `event-booker` is the trust boundary.

Two routes, hand-rolled (no react-router, same `modules/shared/routing.ts` pattern as
event-admin-frontend):
- `/` — list of bookable event types (`GET /api/public/event-types`)
- `/book/{id}` — 3-step wizard: pick a slot (`GET /api/public/slots`) → enter name + email →
  confirmation (`POST /api/public/bookings`)

Full public booking chain: **browser → event-booker-frontend → event-booker → event-scheduling
/ event-users**. No cancel/reschedule, no payments, no i18n framework (UI copy is hardcoded
Russian) — all deferred.

---

## event-organizer (organizer cabinet BFF)

`event-organizer` (slice 6.1, port 8006) is a FastAPI **BFF** in front of
`event-scheduling` and `event-users`, the counterpart to `event-booker` but for an
**authenticated organizer** instead of an anonymous guest: `organizer browser
(slice 6.2 SPA — not built yet) → event-organizer (holds keys, verifies password/JWT)
→ event-scheduling + event-users`. It owns one small PostgreSQL table,
`organizer_credential` (own DB `event_organizer`), holding only login state
(`user_id`, `email`, bcrypt `password_hash`, `disabled`) — the organizer's actual
schedule/booking/profile data still lives in event-scheduling and event-users.

**Auth**: `POST /auth/login` (body `{email, password}`) verifies against
`organizer_credential` and returns a session JWT (`access_token`, HS256, 60min
default expiry). Every `/api/me/*` route requires `Authorization: Bearer
<access_token>` and decodes it into `OrganizerIdentity{user_id, email}`
(`auth/identity.py::require_organizer`).

**Ownership by construction — closes the slice-5 IDOR class here.** Every
`/api/me/*` handler uses `me.user_id` (from the decoded JWT) as the resource id
passed to event-scheduling/event-users. No route accepts an owner/host/user id as a
path or body parameter, so there is no id an organizer could substitute to read or
write another organizer's schedule, bookings, or profile — the same class of bug
(a caller-supplied id trusted without an ownership check) called out in the slice-5
audit for other surfaces is structurally impossible on this BFF's surface.

**The `/api/me` endpoints:**
- `GET/PUT /api/me/schedule`, `PUT /api/me/schedule/travel` — proxy
  `GET/PUT event-scheduling /api/v1/schedules/{me.user_id}` (+ `/travel`)
- `GET /api/me/bookings` — proxies `GET event-scheduling /api/v1/bookings?host_user_id={me.user_id}`,
  returns only `{id, start_time, end_time, status}` per booking (no other
  participant's id or contact info)
- `GET/PUT /api/me/profile` — proxies `GET/PATCH event-users /api/users/id/{me.user_id}`;
  `PUT` forwards **only** `name`/`time_zone` — never `email`/`role`, even though
  event-users' PATCH accepts them
- `PUT /api/me/password` — re-verifies the old password, then updates the stored
  bcrypt hash; `204` on success

**Provisioning**: there is no self-registration. An operator calls `POST
/admin/organizers` (body `{user_id, email, password}`, static `Authorization: Bearer
ORGANIZER_ADMIN_KEY`) which first confirms the email is a real organizer in
event-users (`GET /api/users/by-identity?email=&role=organizer`) before creating
the credential row.

Upstream calls: `SchedulingClient` (Bearer `SCHEDULING_API_KEY` → event-scheduling's
`/api/v1/schedules*`, `/api/v1/bookings`) and `UsersClient` (Bearer
`EVENT_USERS_TOKEN` → event-users' `/api/users/id/*`, `/api/users/by-identity`). No
RabbitMQ, no background tasks. **Deferred**: TOTP/2FA, password reset,
self-registration, login rate-limiting, and the organizer-facing frontend (slice
6.2). event-scheduling's `/api/v1/event-types` and `/api/v1/calendars` are **not**
fronted here — those remain admin/other-owned surfaces.

---

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
| event-notifier | `cd event-notifier && uv run pytest` | Has test infrastructure with pytest-asyncio, respx, and mocks; includes bindings + admin API tests |
| event-receiver | No test suite | No tests exist |
| event-saver | No test suite | No tests exist |
| event-admin | `cd event-admin && uv run pytest` | Notifications proxy tests added in `tests/test_notifications_proxy.py` |
| event-admin-frontend | `cd event-admin-frontend && npm run test` | vitest; includes `NotificationsPage.test.tsx` |
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

### event-notifier (Alembic)

event-notifier uses Alembic for schema management (migrations in `alembic/versions/`). Apply with:

```bash
cd event-notifier && uv run alembic upgrade head
```

Migration `003_notification_bindings` creates the `notification_bindings` table and seeds it
from the `UNISENDER_TEMPLATE_IDS` env and `event_notifier/templates/<locale>/telegram/` files.
Run it whenever deploying the notification-template-config feature for the first time.

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

## Deploying to Kubernetes

docker-compose is for local development. Production runs on Kubernetes from the root
[`deploy/`](../../deploy/) directory. The full design is in
`docs/superpowers/specs/2026-06-14-kubernetes-infrastructure-design.md`.

### Layout

```
deploy/
  helm/
    library/events-common/         # named templates (Deployment/Service/Ingress/HPA/ExternalSecret/migration Job)
    charts/<service>/              # 9 thin per-service charts
    umbrella/events-platform/      # the 9 services + values-{prod,staging,kind}.yaml
    umbrella/events-observability/ # kube-prometheus-stack + VictoriaLogs + Vector
    prereqs/                       # cert-manager / ingress-nginx / Vault / ESO values + manifests
  argocd/                          # app-of-apps.yaml + apps/ (sync-waved Applications)
  scripts/                         # Makefile + smoke.sh + seed-vault.sh
```

### Config & secrets (Vault-only)

**No ConfigMap holds config values.** Every runtime env var (secret AND non-secret) lives in
Vault under `secret/events/<service>`. Each service has one ESO `ExternalSecret` that maps the
Vault path → a k8s Secret consumed via `envFrom`. Seed Vault with `deploy/scripts/seed-vault.sh`
(dev defaults from `.env.example`; DB DSNs / RabbitMQ URL are managed/external placeholders in
prod, overridable via env for the kind smoke).

### Prerequisites — install order

cert-manager → ingress-nginx → Vault → ESO. With GitOps this is the ArgoCD sync-wave order
(0 → 1/1 → 2); manually it is `make -C deploy/scripts bootstrap`. See
`deploy/helm/prereqs/README.md`.

### Vault init → seed → sync gotcha

ArgoCD (or `helm install`) brings Vault **up** but cannot init/unseal/seed it. After Vault is
running an operator must, **once**: init + unseal, enable KV-v2, write the `events-read` policy,
enable + bind the `kubernetes` auth role, apply the `ClusterSecretStore` + `ClusterIssuers`, then
run `seed-vault.sh`. Until Vault is seeded the platform pods stay in
`CreateContainerConfigError`/`CrashLoopBackOff` — expected, self-resolves on the next ESO refresh.
Dev-mode Vault (the kind smoke) auto-initializes + unseals, so those steps are skipped there.
Full commands: `deploy/helm/prereqs/manifests/vault-bootstrap.md` and `deploy/argocd/README.md`.

### ArgoCD sync

Apply the root app once (`kubectl apply -n argocd -f deploy/argocd/app-of-apps.yaml`); it creates
every child Application in `deploy/argocd/apps/`. Sync-waves: cert-manager (0) →
ingress-nginx + vault (1) → ESO (2) → events-platform + events-observability (3). CI bumps image
tags by writing the new sha into `values-prod.yaml` (PR) → ArgoCD syncs.

### kind smoke

`make -C deploy/scripts smoke` (needs `kind` + a running Docker). It creates a throwaway cluster,
installs the prereqs + dev-mode Vault, enables the platform's `devDependencies` (in-cluster
Bitnami Postgres/RabbitMQ — managed/external in prod), deploys `events-platform` with
`values-kind.yaml`, waits (bounded) for all Deployments, POSTs a real HMAC-signed cal.com
`BOOKING_CREATED` to event-receiver expecting **202**, then tears the cluster down.

### Dual CI

Every deployable service repo builds + pushes `ghcr.io/lelikov/<service>:{sha,latest}` on push to
`main` + version tags via **both** GitHub Actions (`.github/workflows/publish-image.yml`) and
GitLab CI (`.gitlab-ci.yml`) — functionally identical pipelines. `event-schemas` is a library, so
it gets a lint+test pipeline only (no image).

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
