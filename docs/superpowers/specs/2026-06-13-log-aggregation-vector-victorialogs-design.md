# Log Aggregation (Vector + VictoriaLogs) — Design

**Date:** 2026-06-13
**Status:** Approved

## Goal

Centralized collection and storage of container logs for the events stack, viewable in the
existing Grafana alongside metrics. Added to the `observability` compose profile; no service
code changes (services already emit structlog JSON to stdout).

## Decisions (interview 2026-06-13)

| Question | Decision |
|---|---|
| Stack | **VictoriaLogs + Vector** (lightweight, fast, LogsQL, Grafana datasource) |
| Collection | Vector reads **container stdout** via the Docker socket — zero app changes |
| Scope | **All containers** (10 services + RabbitMQ, 5× Postgres, nginx/Caddy frontends, mocks) |
| Integration | In the existing **`observability` profile**; VictoriaLogs datasource + Logs dashboard in the current Grafana; 7-day retention |

## Components (root repo only)

### vector (collector)
- Image `timberio/vector`, in `profiles: ["observability"]`.
- Source `docker_logs` — mounts `/var/run/docker.sock:ro`; collects all containers, **excludes
  itself** (and optionally victorialogs/grafana to cut noise — keep them, just exclude vector).
- Transform: parse the JSON `message` (structlog already emits JSON for the Python services)
  into structured fields; non-JSON infra logs (Postgres/RabbitMQ/nginx) pass through as `_msg`.
  Add fields: `service` (from compose service label), `container`, `stream`, `level` (from the
  parsed JSON when present).
- Sink → VictoriaLogs via its JSON-line / Elasticsearch-bulk ingestion endpoint, with
  `_stream_fields=service,container`, `_msg_field`/`_time_field` mapped to the structlog
  `event`/`timestamp`. Config: `docker/vector/vector.yaml`.

### victorialogs (storage)
- Image `victoriametrics/victoria-logs`, `profiles: ["observability"]`, persistent volume
  `victorialogs-data`, `-retentionPeriod=7d`, host port `127.0.0.1:${VICTORIALOGS_PORT:-9428}`
  (built-in UI + LogsQL `/select/logsql/query` API), healthcheck on `/health`.

### Grafana
- Provision the VictoriaLogs datasource (plugin `victoriametrics-logs-datasource` via
  `GF_INSTALL_PLUGINS`), uid e.g. `victorialogs`, url `http://victorialogs:9428`.
- New dashboard `docker/grafana/dashboards/logs.json` (uid `events-logs`): `service` template
  variable, log volume by level over time, a live logs panel filterable by service/level.
  Derived/data-link field `trace_id` for logs↔metrics correlation. Explore works out of the box.

### Compose / env / docs
- Add `vector` + `victorialogs` services and `victorialogs-data` volume.
- `.env.example`: `VICTORIALOGS_PORT` (9428), `LOGS_RETENTION_PERIOD` (7d).
- Docs: ONBOARDING observability section gains a Logging subsection (what's collected, LogsQL
  query examples, retention, where config lives, the docker.sock note); root CLAUDE.md +
  README ports/observability mentions.

## Security note

Vector mounts the Docker socket read-only; VictoriaLogs is loopback-only with no auth.
Acceptable for this dev/integration stack; documented as a hardening item for production.

## Verification

`docker compose --profile observability up -d --build` → all healthy incl. vector +
victorialogs. Generate traffic (`scripts/calcom_sim.py lifecycle`). Query VictoriaLogs API
(`/select/logsql/query`) and confirm: logs stored from multiple distinct `service` values,
JSON fields parsed (filter `level:error`, filter by `service`), infra logs present as `_msg`.
Grafana: datasource health OK, Logs dashboard renders, Explore returns rows. Teardown
`down -v`.

## Out of scope

- Distributed tracing (OTel traces) — separate effort.
- Log-based alerting.
- Production auth/TLS for VictoriaLogs.
