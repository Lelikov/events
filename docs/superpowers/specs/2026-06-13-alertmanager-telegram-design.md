# Alertmanager + Telegram Alerting — Design

**Date:** 2026-06-13
**Status:** Approved

## Goal

Operational alerting on top of the existing Prometheus/Grafana stack: Prometheus alert rules
→ Alertmanager → Telegram (dedicated ops bot/chat). Distinct from event-notifier's
client-facing Telegram channel.

## Decisions (interview 2026-06-13)

| Question | Decision |
|---|---|
| Telegram delivery | Alertmanager native `telegram_configs` (no extra bot container) |
| Bot/chat | Dedicated ops bot: `ALERT_TELEGRAM_BOT_TOKEN` + `ALERT_TELEGRAM_CHAT_ID`, separate from notifier's `TELEGRAM_BOT_TOKEN` |
| Alert set | Infra/technical + business |

## Components (all in ROOT repo)

### Alertmanager container
- `prom/alertmanager`, config `docker/alertmanager/alertmanager.yml`, host port
  `127.0.0.1:${ALERTMANAGER_PORT:-9093}` (loopback like Prometheus).
- Native `telegram_configs` receiver; `bot_token`/`chat_id` injected from env. Use the
  image's env handling (Alertmanager supports `$ENV` only in limited places — if needed,
  render the config from a template at container start via an entrypoint wrapper / envsubst).
- **Graceful degrade**: with placeholder/empty token the stack still starts; failed sends
  land in Alertmanager's log (same philosophy as the WireMock-mocked external APIs).
  Real alerts require the two env vars.

### Prometheus wiring
- `prometheus.yml`: add `alerting.alertmanagers` (target `alertmanager:9093`) and
  `rule_files: ['/etc/prometheus/rules/*.yml']`; mount `docker/prometheus/rules/`.

### Alert rules (`docker/prometheus/rules/`)
- **infra.yml**: `ServiceDown` (up==0, 1m), `HighErrorRate` (5xx ratio > 5% / 5m),
  `HighLatencyP95`, `DLQGrowing` (`rabbitmq_queue_messages` on `*.dlq` > 0),
  `OutboxBacklog` (notifier_outbox_depth pending high), `OutboxStalled`
  (notifier_outbox_oldest_pending_age_seconds > threshold), `RabbitMQDown`, `PostgresDown`.
- **business.yml**: `BookingRejectionSpike` (rate of booking_rejections_total),
  `NotificationDeliveryFailures` (notifier_deliveries_total{outcome="failed"} rate).
- Each rule: `severity: warning|critical` label + `summary`/`description` annotations using
  alert labels.

### Routing & message format
- `route`: group by `alertname`+`job`; `critical` short `group_wait`, `warning` longer;
  single Telegram receiver. Go-template message: severity emoji, alertname, job/instance,
  summary, Grafana link.

### Grafana
- Add Alertmanager as a datasource (provisioning) for alert viewing; dashboards unchanged.

## Verification
- Static: `amtool check-config docker/alertmanager/alertmanager.yml`,
  `promtool check rules docker/prometheus/rules/*.yml`.
- Live: stack up → stop one service → `ServiceDown` reaches `firing` (Alertmanager
  `/api/v2/alerts`), receiver matched. If a test bot/chat is provided, confirm real Telegram
  delivery; otherwise verify up to firing+route-match and note delivery as unverified.
- Teardown.

## Out of scope
- Interactive Telegram commands (silence/ack from chat).
- PagerDuty/email/other receivers.
- Per-team routing trees beyond severity.
