# Prometheus Metrics + Grafana — Design

**Date:** 2026-06-13
**Status:** Approved

## Goal

Prometheus metrics across all services with two provisioned Grafana dashboards, integrated
into the root docker-compose stack.

## Decisions (interview 2026-06-13)

| Question | Decision |
|---|---|
| Metric scope | Technical (RED) + business counters |
| Infra exporters | RabbitMQ built-in `rabbitmq_prometheus` + `postgres_exporter` over the 4 DBs |
| Dashboards | Two: System Overview + Booking Flow (JSON provisioning from the repo) |

## Service instrumentation (prometheus-client)

Common pattern per Python service: `metrics.py` module, `GET /metrics` on the same port as
`/health` (consumers already run an HTTP health app). HTTP path labels use the **route
template**, never the raw URL (cardinality).

| Service | Technical | Business |
|---|---|---|
| event-receiver | HTTP RED middleware | webhooks by source/type, publish failures, unknown types |
| event-saver | consumer RED (queue, event_type, outcome ok/retried/rejected) | events by type, bookings by status transitions |
| event-booking | consumer RED | rejections by `rejection_type` (incl. `blacklisted`), blacklist hits/cache state, chats/meeting URLs created, reminders sent |
| event-notifier | consumer RED | deliveries by channel/trigger/outcome, outbox depth by status (gauge), oldest-pending age (gauge) |
| event-users | HTTP RED | CRM sync results (synced/quarantined/errors) |
| event-admin | HTTP RED | logins (success/failure), blacklist CRUD ops |

Naming: `<service>_` prefix or shared names with `service` label — pick one convention and
apply uniformly (recommended: standard names like `http_requests_total` + per-job `job`
label from Prometheus; business metrics service-prefixed).

## Infrastructure (root compose)

- RabbitMQ: enable `rabbitmq_prometheus` plugin, scrape `rabbitmq:15692` (queue/DLQ depths).
- `postgres_exporter`: multi-target or one container per DB — implementer's choice, all 4 DBs
  (saver, users, notifier, calcom) covered.
- `prometheus` container: config `docker/prometheus/prometheus.yml`, scrape all services by
  container name, 15 s interval; host port `127.0.0.1:${PROMETHEUS_PORT:-9090}`.
- `grafana` container: provisioning from `docker/grafana/provisioning/` (Prometheus
  datasource + two dashboard JSONs from `docker/grafana/dashboards/`); host port
  `${GRAFANA_PORT:-3001}`; dev login admin/admin.

## Dashboards

1. **System Overview** — per-service up/RED panels, queue + DLQ depths (thresholds), DB
   connection stats.
2. **Booking Flow** — funnel: webhooks → events → bookings created/rejected (by reason) →
   notifications delivered/failed; outbox depth + oldest-pending age; blacklist hits;
   processing duration by queue.

## Verification

- Unit tests: `/metrics` endpoint exposed and counters increment in each touched service.
- Live: full stack up → `calcom_sim.py lifecycle` (+ a blacklisted create) → Prometheus API
  shows business counters incremented; both dashboards load via Grafana API and their
  queries return data; teardown.

## Out of scope

- Alerting rules / Alertmanager (future).
- Frontend (nginx) metrics — `/health` suffices for now.
- Tracing (OTel) — separate effort.
