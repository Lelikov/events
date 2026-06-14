# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Monorepo Overview

This is a **multi-service event-driven system** for managing bookings and participants. Ten independent packages share this root directory; each has its own `CLAUDE.md` with service-specific commands and architecture.

| Service | Language/Stack | Role |
|---|---|---|
| `event-receiver/` | Python, FastAPI | Ingress: validates webhooks (incl. cal.com `POST /event/calcom`), wraps payloads in the `{original, normalized}` envelope, publishes CloudEvents to RabbitMQ |
| `event-saver/` | Python, FastAPI, FastStream | Consumes RabbitMQ, **owns and writes** the main PostgreSQL database |
| `event-booking/` | Python, FastAPI, FastStream | Booking orchestrator: consumes lifecycle events, reads/writes the cal.com DB, creates GetStream chats + Jitsi meeting URLs, schedules reminders, publishes follow-up events via event-receiver |
| `event-admin/` | Python, FastAPI | Read-only API over `event-saver`'s DB; publishes admin actions via event-receiver |
| `event-admin-frontend/` | TypeScript, React, Vite | Admin UI for bookings and participants |
| `event-users/` | Python, FastAPI | Separate user/contact management service with CRM sync; consumes `events.user.email` |
| `event-notifier/` | Python, FastAPI, FastStream, asyncpg | Notification dispatcher: consumes `events.notification.commands`, outbox + email/Telegram delivery, publishes delivery-result events |
| `event-shortener/` | Python, FastAPI | URL shortener (REST, own PostgreSQL); event-booking shortens meeting links via it. Replaced the `/shortify` WireMock stub |
| `event-schemas/` | Python, Pydantic | Shared schema library (payloads, envelope, **canonical RabbitMQ topology**); no runtime service |
| `jitsi-chat/` | TypeScript, React, Vite | Participant-facing video meeting + chat SPA |

## System Data Flow

```
cal.com webhooks / external clients     jitsi-chat SPA (Jitsi iframe events)
        │                                   │
        ▼                                   ▼
  event-receiver          (validates, normalizes → CloudEvent {original, normalized})
        │ RabbitMQ topic exchange "events" (DLX: events.dlx)
        │
        ├──► events.booking.lifecycle.saver ──► event-saver  (writes PostgreSQL)
        │                                            │
        │                                            ├──► event-admin (read-only API, same DB)
        │                                            │         └──► event-admin-frontend
        │                                            └──► [events, bookings, participants, projections]
        │
        ├──► events.booking.lifecycle.booking ──► event-booking
        │         (cal.com DB; GetStream chat; Jitsi meeting URLs; reminders)
        │         ├──► REST ──► event-shortener (shortens meeting links; own PostgreSQL)
        │         └──► follow-up events (chat.*, meeting.url_*, booking.rejected,
        │              notification.send_requested) ──► HTTP POST back to event-receiver
        │
        ├──► events.notification.commands ──► event-notifier
        │         (outbox → UniSender email / Telegram)
        │         └──► notification.*.message_sent ──► HTTP POST back to event-receiver
        │
        └──► events.user.email ──► event-users (separate DB: users, user_contacts; CRM sync)
```

- **Database ownership**: `event-saver` owns all main-DB schema migrations (`alembic/` lives there). `event-admin` is read-only — never create migrations in `event-admin`. `event-users` and `event-notifier` own their separate DBs. `event-booking` writes to the cal.com DB but NEVER migrates it (cal.com owns its schema).
- **Shared schemas**: `event-schemas` (v0.2.0) is a local pip package imported by `event-receiver`, `event-saver`, `event-booking`, and `event-notifier`. Its `queues.py` is the single source of truth for the RabbitMQ topology; `envelope.py` defines the mandatory `{original, normalized}` consumer unwrap.
- **participants.user_id** in `event-saver`'s DB references the UUID PK from `event-users`; event-receiver resolves it at ingress into `normalized.participants`.

## Quick Start (Docker Compose)

The whole system — 10 services, RabbitMQ, 5 PostgreSQL instances, and WireMock
stand-ins for the remaining external HTTP APIs — runs with one command from the
repo root:

```bash
docker compose up -d --build     # 10 services + infra; no .env needed (dev defaults baked in)
docker compose --profile observability up -d --build   # + Prometheus/Grafana/Alertmanager/exporters
cp .env.example .env             # optional: copy + edit only what you change
docker compose down -v           # tear down (incl. volumes; add --profile observability to also stop it)
```

The observability stack lives in the **`observability` compose profile** and is OFF
by default — the bare `up` starts only the 16 app/infra containers. Enable it per the
second command above, or set `COMPOSE_PROFILES=observability` in `.env` to make it part
of the default `up`.

Host ports:

| Port | Service |
|---|---|
| 8888 | event-receiver (ingress webhooks: `/event/calcom`, `/event/jitsi`, …) |
| 8001 | event-users API |
| 8002 | event-admin API |
| 8000 | event-shortener API (REST URL shortener; event-booking calls it) |
| 3000 | event-admin-frontend (nginx, same-origin proxy to event-admin) |
| 8080 | jitsi-chat SPA |
| 8089 | WireMock mocks (journal: `http://localhost:8089/__admin/requests`) |
| 5672 / 15672 | RabbitMQ (AMQP / management UI) |
| 5433 | pg-calcom (fixture cal.com DB, used by `scripts/calcom_sim.py`) |
| 9090 | Prometheus *(observability profile; 127.0.0.1 only; scrapes services + RabbitMQ + postgres exporters)* |
| 3001 | Grafana *(observability profile; admin/admin; dashboards: System Overview, Booking Flow)* |
| 9093 | Alertmanager *(observability profile; 127.0.0.1 only; routes Prometheus alerts → ops Telegram)* |
| 9428 | VictoriaLogs *(observability profile; 127.0.0.1 only; container logs collected by Vector; LogsQL UI + API)* |
| 3200 | Tempo *(observability profile; 127.0.0.1 only; distributed trace storage; Grafana datasource uid `tempo`)* |
| 4317 | OTel Collector *(observability profile; 127.0.0.1 only; OTLP/gRPC; Python services → collector → Tempo)* |

Observability (in the `observability` profile): every Python service serves `GET /metrics` (prometheus-client);
Prometheus config lives in `docker/prometheus/prometheus.yml`, the
provisioned dashboards in `docker/grafana/dashboards/` (uids
`events-system-overview`, `events-booking-flow`, `events-logs`). Alert rules live in
`docker/prometheus/rules/` (infra + business) and route through Alertmanager
(`docker/alertmanager/`) to a dedicated ops Telegram chat — set
`ALERT_TELEGRAM_BOT_TOKEN`/`ALERT_TELEGRAM_CHAT_ID` in `.env` for real delivery.
Logs: **Vector** collects every container's stdout via the Docker socket into
**VictoriaLogs** (7d retention), queryable in Grafana via the `victorialogs`
datasource and the Logs dashboard; config in `docker/vector/vector.yaml`.
Tracing: every Python service exports OpenTelemetry spans via OTLP/gRPC to **otel-collector**
→ **Tempo** (datasource uid `tempo`); off by default (`OTEL_SDK_DISABLED=true`); enable with
`OTEL_SDK_DISABLED=false docker compose --profile observability up -d --build`. Configs:
`docker/tempo/tempo.yaml`, `docker/otel-collector/config.yaml`.
See `docs/architecture/ONBOARDING.md` § Observability for what's collected, the
alert set, how to add a metric or rule, and the full tracing guide (TraceQL, manual spans,
logs↔traces correlation).

### Симуляция событий cal.com

`scripts/calcom_sim.py` генерирует реалистичные подписанные вебхуки cal.com
(по образцу `event-booking/requests.jsonl`) и пишет фикстурные строки в pg-calcom:

```bash
uv run scripts/calcom_sim.py create [--starts-in 1h] [--locale en] [--dry-run]
uv run scripts/calcom_sim.py lifecycle          # created -> rescheduled -> cancelled
uv run scripts/calcom_sim.py cancel <uid>; uv run scripts/calcom_sim.py reschedule <uid>
```

Mock vs real external APIs: UniSender Go, Telegram Bot API and GetStream default
to the WireMock container (`http://mocks:8080/<prefix>`, mappings in
`docker/mocks/mappings/`). Point the corresponding `*_URL`/key variables in
`.env` at real endpoints to integrate for real. (URL shortening is now the real
in-contour `event-shortener` service, not a mock.)

External cal.com: by default `event-booking` reads the seeded `pg-calcom`
fixture DB (`docker/calcom-init/`). Set `CALCOM_DATABASE_URL` in `.env` to a
real cal.com PostgreSQL DSN to use an external instance (the fixture container
keeps running but is unused).

Notes:
- `admin_users` (event-admin panel logins) is created by event-saver's alembic
  but not seeded — seed rows manually if you need to log in to the admin UI.
- event-receiver dedupes identical webhook payloads in-memory for 10 minutes
  ("Duplicate event suppressed by idempotency cache") — restart it when
  replaying the same payload during testing.

## Common Patterns Across Python Services

All Python services share the same conventions:

- **Python 3.14**, `uv` for dependency management
- **FastAPI** for HTTP, **Dishka** for dependency injection
- **Ruff** (line length 120) for linting/formatting; `pre-commit` hooks
- **Protocol-based interfaces** in `interfaces/` — loose coupling between layers
- **Frozen dataclasses** as DTOs for inter-layer communication
- **`adapters/sql.py`** (`SqlExecutor`) — raw `text()` SQL via `AsyncSession`; ORM models exist only for Alembic

### Code Style Rules

- **No `elif`** — use early returns, guard clauses, or mapping dicts instead of `elif` chains
- **Avoid `else`** — prefer early returns. Use `else` only when both branches are truly symmetric and an early return would hurt readability

```bash
# Standard commands (run inside each service directory)
uv sync                      # install deps
ruff check --fix .           # lint
ruff format .                # format
pre-commit run --all-files   # all hooks
```

## Documentation Structure

### Cross-Service Documentation (`docs/`)

The `docs/` directory at the monorepo root contains **cross-service** documentation about the system as a whole:
- `docs/architecture/ARCHITECTURE.md` — system topology, C4 diagrams, architectural decisions
- `docs/architecture/MESSAGE_CONTRACTS.md` — CloudEvent contracts between services
- `docs/architecture/CODING_STANDARDS.md` — shared coding conventions
- `docs/architecture/ONBOARDING.md` — developer onboarding guide
- `docs/architecture/INDEX.md` — FAQ-style documentation index
- `docs/audit/v2/` — **current** audit (audit-v2, 2026-06-11): `AUDIT_REPORT_V2.md`, `CONTRACT_DECISIONS.md` (canonical contract rules D1–D8), `INTEGRATION_REPORT.md`, findings + fix manifests
- `docs/audit/` — superseded April 2026 audit report, dependency graph, scalability gaps (historical)

### Per-Service Documentation

Each service has its own `CLAUDE.md` (commands, architecture) and `docs/` directory (detailed documentation):

| Service | `CLAUDE.md` | `docs/` contents |
|---|---|---|
| `event-receiver/` | ingress endpoints, auth, RabbitMQ | SERVICE_OVERVIEW, API_CONTRACTS, DEPENDENCIES, AUDIT |
| `event-saver/` | clean architecture, projections, DB schema | SERVICE_OVERVIEW, API_CONTRACTS, DATA_MODEL, DEPENDENCIES, AUDIT |
| `event-booking/` | orchestrator, cal.com DB invariants, chat/meeting flows | SERVICE_OVERVIEW, API_CONTRACTS, DEPENDENCIES, AUDIT |
| `event-admin/` | read-only API, DI scopes, endpoint pattern | SERVICE_OVERVIEW, API_CONTRACTS, DATA_MODEL, DEPENDENCIES, AUDIT |
| `event-admin-frontend/` | Vite/React, routing, auth flow | SERVICE_OVERVIEW, API_CONTRACTS, DEPENDENCIES, AUDIT |
| `event-users/` | user/contact CRUD, CRM sync | SERVICE_OVERVIEW, API_CONTRACTS, DATA_MODEL, DEPENDENCIES, AUDIT |
| `event-notifier/` | notification dispatch, channels | SERVICE_OVERVIEW, API_CONTRACTS, DEPENDENCIES, AUDIT |
| `event-shortener/` | REST URL shortener, idents, redirect | SERVICE_OVERVIEW, API_CONTRACTS, DEPENDENCIES, AUDIT |
| `event-schemas/` | event types, priorities, versioning | SERVICE_OVERVIEW, API_CONTRACTS, DEPENDENCIES, AUDIT |
| `jitsi-chat/` | Jitsi video + Stream Chat SPA | SERVICE_OVERVIEW, API_CONTRACTS, DEPENDENCIES |

## Production (Kubernetes)

docker-compose is for **local dev**; production runs on Kubernetes from `deploy/`:

- `deploy/helm/` — `events-common` library chart, thin per-service charts, and the
  `events-platform` + `events-observability` umbrella charts. All config/secrets come from
  **Vault via External Secrets Operator** — ConfigMaps hold no values.
- `deploy/argocd/` — ArgoCD **app-of-apps** (`app-of-apps.yaml` → `apps/`); prereqs +
  umbrellas roll out by sync-wave (cert-manager 0 → ingress-nginx/vault 1 → ESO 2 →
  platform/observability 3). See `deploy/argocd/README.md` for the **Vault init → seed →
  sync** gotcha.
- `deploy/scripts/` — `Makefile` (`lint`/`template`/`bootstrap`/`seed`/`smoke`/`clean`),
  `smoke.sh` (kind end-to-end), `seed-vault.sh`.
- **Dual CI**: every deployable service repo builds + pushes `ghcr.io/lelikov/<service>` via
  **both** GitHub Actions (`.github/workflows/publish-image.yml`) and GitLab CI
  (`.gitlab-ci.yml`); `event-schemas` (library) gets lint/test CI only.

Deployment walkthrough: `docs/architecture/ONBOARDING.md` § "Deploying to Kubernetes".

## RabbitMQ Queue Routing

Canonical topology lives in `event-schemas/event_schemas/queues.py` (**single source of truth** — `QueueSpec`, `ALL_QUEUES`, `ROUTING_RULES`). **One queue per consumer service**; fan-out = several queues bound to the same routing key. Every queue dead-letters to `events.dlx` with a `<queue>.dlq` companion (24h TTL).

| Queue | Routing key | Consumer |
|---|---|---|
| `events.booking.lifecycle.saver` | `events.booking.lifecycle` | event-saver |
| `events.booking.lifecycle.booking` | `events.booking.lifecycle` | event-booking |
| `events.chat.lifecycle` / `events.chat.activity` / `events.chat` | (same names) | event-saver |
| `events.meeting.lifecycle` | `events.meeting.lifecycle` | event-saver |
| `events.notification.commands` | `events.notification.commands` | event-notifier |
| `events.notification.delivery` | `events.notification.delivery` | event-saver (delivery results) |
| `events.jitsi` / `events.mail` | (same names) | event-saver |
| `events.user.email` | `events.user.email` | event-users |
| `events.unrouted` | `events.unrouted` | event-saver (unknown types — never a 500) |

Removed queues (audit-v2): `events.booking.reminder`, `events.notifications`. Routing rules use glob patterns on `source` and `type` fields (`ROUTING_RULES`). See `event-receiver/QUEUES_DIGEST.md` and `event-saver/QUEUES_DIGEST.md` for full mappings.

## CloudEvents Format

All inter-service messages use **CloudEvents binary mode**:
- Headers: `ce-type`, `ce-source`, `ce-id`, `ce-time`, `ce-bookingid` (no underscore — canonical in `event_schemas.attributes`), `ce-specversion`, `ce-traceid`, `ce-spanid`, `ce-idempotencykey`
- Body: `{"original": <domain payload>, "normalized": {"participants": [...]}}` — consumers MUST unwrap via `event_schemas.envelope.unwrap_payload()`, never read domain fields at the top level

Event schemas and priorities are defined in `event-schemas/event_schemas/`:
- Priority 10 (CRITICAL): booking lifecycle
- Priority 7 (HIGH): notifications, reminders  
- Priority 5 (NORMAL): chat, meetings, external integrations

## Documentation Requirements

All code changes MUST include corresponding documentation updates:
- Architectural changes → update `docs/architecture/` files
- New event types or queue changes → update per-service `QUEUES_DIGEST.md` and `EVENTS_DIGEST.md`
- Changed interfaces or cross-service contracts → update `docs/architecture/MESSAGE_CONTRACTS.md`
- Bug fixes for audit findings → update per-service `docs/AUDIT.md`
- New services or endpoints → update `docs/architecture/ONBOARDING.md`

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
