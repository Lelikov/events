# Root docker-compose — Design

**Date:** 2026-06-11
**Status:** Approved

## Goal

One command (`docker compose up -d --build`) brings up the entire events system at the monorepo
root: all 9 services, all infrastructure, mock external APIs. Self-contained by default; real
external APIs and an external cal.com DB are opt-in via `.env`.

## Decisions (interview 2026-06-11)

| Question | Decision |
|---|---|
| Scope | Full stack: infra + all 9 services |
| cal.com DB | Both: `pg-calcom` fixture container by default, `CALCOM_DATABASE_URL` env override for an external instance |
| External APIs | WireMock stubs by default; real keys/URLs via `.env` |

## Components

### Infrastructure

| Container | Image | Notes |
|---|---|---|
| `rabbitmq` | rabbitmq:3-management | healthcheck `rabbitmq-diagnostics ping`; ports 5672/15672 |
| `pg-saver` | postgres:16 | main DB (event-saver owns migrations) |
| `pg-users` | postgres:16 | event-users DB |
| `pg-notifier` | postgres:16 | event-notifier DB |
| `pg-calcom` | postgres:16 | cal.com fixture; init SQL in `docker/calcom-init/` (Booking, Attendee, EventType, users + the columns event-booking reads/writes incl. `metadata`, `bookingReminderSentAt`); skipped when `CALCOM_DATABASE_URL` points elsewhere |
| `mocks` | wiremock/wiremock | one container; mappings in `docker/mocks/mappings/` for Shortify, UniSender Go, Telegram Bot API, GetStream (paths don't collide) |

Named volumes for all Postgres data (no bind mounts). Single default network; inter-service
URLs use container names.

### Services

| Service | Port (host) | Dockerfile | Migrations |
|---|---|---|---|
| event-receiver | 8888 | exists — fix for root context | — |
| event-saver | — | exists — fix for root context | entrypoint: `alembic upgrade head` |
| event-booking | — | **new** | — (cal.com schema external) |
| event-notifier | — | **new** | entrypoint: `alembic upgrade head` |
| event-users | 8001 | exists — fix for root context | entrypoint: `alembic upgrade head` |
| event-admin | 8002 | **new** | — (reads saver DB; `admin_users` seeded via `scripts/admin_users.sql` one-shot) |
| event-admin-frontend | 3000 | **new** (node build → nginx, `/api` proxy → event-admin) | — |
| jitsi-chat | 8080 | exists (env.sh runtime injection) | — |

### Build context rule

All Python services depend on `../event-schemas` (relative path), so **every Python service
builds with `context: .` (monorepo root)** and `dockerfile: <service>/Dockerfile`. Dockerfiles
COPY `event-schemas/` and the service directory. Existing Dockerfiles (receiver, saver, users)
are updated accordingly — they are currently broken for standalone builds after the relative-path
migration.

### Environment

Root `.env.example` with every variable grouped per service:
- dev-grade defaults that pass startup validation (admin/receiver secret-strength checks)
- RabbitMQ/Postgres dev credentials
- external API base URLs defaulting to `http://mocks:8080/...`; real keys opt-in
- where a service lacks a base-URL setting for an external API (booking/notifier), add one
  (small config change, default = real production URL so non-Docker usage is unchanged)

### Verification

1. `docker compose up -d --build` → all healthchecks green.
2. Replay a real `BOOKING_CREATED` from `event-booking/requests.jsonl` to `/event/calcom`
   (HMAC-signed) → booking row in pg-saver, notification in notifier outbox, mock hits visible
   in WireMock request journal.
3. Document Quick Start in root CLAUDE.md + docs/architecture/ONBOARDING.md.

## Out of scope

- Production deployment topology (this is a dev/integration stack).
- Mocking Jitsi itself (jitsi-chat SPA embeds the public meet.jit.si iframe).
- FCM (disabled pending credentials).
