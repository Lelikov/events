# Distributed Tracing (OpenTelemetry) — Design

**Date:** 2026-06-14
**Status:** Approved

## Goal

End-to-end distributed tracing across the 10-service events platform: one trace follows a
cal.com webhook from ingress through RabbitMQ, the cal.com DB, GetStream/meeting/shortener
calls, and notification delivery. Traces are viewable in the existing Grafana alongside
metrics and logs, with logs↔traces↔metrics correlation. Added to the `observability`
compose profile and the `events-observability` Helm umbrella; docker-compose stays the
local-dev surface.

## Decisions (interview 2026-06-14)

| Topic | Decision |
|---|---|
| Instrumentation | **Auto-instrumentation** (FastAPI, httpx, SQLAlchemy/asyncpg, FastStream) **+ targeted manual spans** for key business operations |
| Backend | **Grafana Tempo** — native Grafana trace view, TraceQL, built-in logs↔traces↔metrics correlation |
| Export pipeline | **Services → OTLP → OpenTelemetry Collector → Tempo** (central batching/sampling; services are backend-agnostic; mirrors Vector's role for logs) |
| Trace IDs | **OTel is the source of truth (W3C)**. W3C `traceparent` propagates over HTTP and RabbitMQ headers; `ce-traceid`/`ce-spanid` are populated from the active span (hex) for backward compat; the UUID generators in event-receiver are retired; structlog binds the real (Tempo) trace_id |
| Scope | **Backend only** — 10 Python/infra services. Browser tracing for the two React SPAs is out of scope (belongs with frontend RUM/Sentry) |
| Sampling | dev: `parentbased_always_on`; prod: `parentbased_traceidratio` via `OTEL_TRACES_SAMPLER_ARG` |
| Enablement gate | `OTEL_SDK_DISABLED=true` by default (no-op; bare `up` stays clean); the `observability` profile sets it `false` + the collector endpoint. Services run fine with no collector present |

## Current state (what already exists)

- `event-schemas/event_schemas/attributes.py` — canonical `ce-traceid`/`ce-spanid` header names.
- `event-receiver/event_receiver/utils.py` — generates **UUID v4** trace/span IDs (not W3C);
  `extract_trace_id_from_headers()` already understands W3C `traceparent`.
- `event-receiver/.../publisher.py` — sets `ce-traceid`/`ce-spanid` on outgoing CloudEvents.
- Consumers read the IDs from CE headers; **event-saver** binds `trace_id` into structlog,
  **event-booking**/**event-notifier** do not. Follow-up events from booking/notifier do **not**
  propagate the inbound trace context (chain breaks).
- httpx clients set **no** trace headers (HTTP is a black hole to tracing).
- No OpenTelemetry SDK anywhere; no tracing backend in the observability stack.
- Observability profile: Prometheus 3.4.1, Grafana 11.6.3, Alertmanager, VictoriaLogs, Vector,
  5× postgres-exporter. Grafana datasources: `prometheus`, `victorialogs`.
- OpenTelemetry-Python supports Python 3.14 (May 2026 releases) — no version blocker.

## Pipeline

```
[10 services] --OTLP gRPC :4317--> [otel-collector] --OTLP--> [tempo] <-- Grafana datasource "tempo"
                                    batch / memory_limiter / resource        TraceQL + Explore + Service Graph
```

## Components

### otel-collector (new, observability profile + Helm)
- Image `otel/opentelemetry-collector-contrib`.
- Receivers: OTLP gRPC (4317) + HTTP (4318). Processors: `memory_limiter`, `batch`,
  `resource` (adds deployment env). Exporter: OTLP → Tempo.
- Config: `docker/otel-collector/config.yaml`. Loopback host port for debugging.

### tempo (new, observability profile + Helm)
- Image `grafana/tempo`. OTLP receiver enabled; filesystem (local) storage; configurable
  retention. Persistent volume `tempo-data`. Loopback host port.
- Config: `docker/tempo/tempo.yaml`.

### Grafana provisioning
- Tempo datasource (uid `tempo`, url `http://tempo:3200`).
- Correlation: VictoriaLogs datasource gains a `trace_id` **derived field** linking to Tempo;
  Tempo datasource gets **traces→logs** (query VictoriaLogs by `trace_id`) and, where cheap,
  traces→metrics. Explore + Service Graph work out of the box. No bespoke dashboard required
  beyond the datasource wiring (a small "Traces" nav entry optional).

### Per-service `telemetry.py` (new, in each Python service)
Follows the existing per-service `logger.py`/`metrics.py` convention (copied, not shared —
keeps `event-schemas` dependency-light). Builds: `TracerProvider` with a `Resource`
(`service.name` from `OTEL_SERVICE_NAME`), W3C `tracecontext`+`baggage` propagators, OTLP span
exporter (→ collector), `BatchSpanProcessor`, env-driven sampler. A `setup_tracing()` called
from the app lifespan / `create_app`. No-op when `OTEL_SDK_DISABLED=true`.

### Auto-instrumentation (each service startup)
- `FastAPIInstrumentor.instrument_app(app)` — server spans + inbound `traceparent` extraction.
- `HTTPXClientInstrumentor` — outbound `traceparent` injection on every httpx call
  (booking→receiver, booking→shortener, admin→receiver, saver→users, notifier→users,
  notifier→UniSender/Telegram).
- DB: `SQLAlchemyInstrumentor` where an engine exists (saver, users, admin, booking,
  notifier, shortener); `AsyncPGInstrumentor` as the fallback for direct-asyncpg paths.
- RabbitMQ: FastStream's native `RabbitTelemetryMiddleware` on the broker — span creation +
  `traceparent` propagation through AMQP headers on publish and consume.

### Trace-context reconciliation (W3C)
- `traceparent` rides **alongside** the `ce-*` headers on both HTTP and RabbitMQ messages;
  FastAPI/FastStream instrumentors extract it and continue the trace across service hops.
- event-receiver publisher: set `ce-traceid`/`ce-spanid` from the **active span** (hex) instead
  of generating UUIDs; retire `generate_trace_id`/`generate_span_id` (keep the inbound W3C
  `extract_*` reader). Existing consumers and the logs dashboard keep working, now keyed on the
  real Tempo trace id.
- structlog: a shared `add_otel_trace_context` processor in each `logger.py` injects the active
  span's `trace_id`/`span_id` into every log line, replacing the ad-hoc `bind_contextvars` in
  event-saver and filling the gap in booking/notifier. VictoriaLogs entries then carry the
  Tempo trace id and link to the trace.

### Targeted manual spans (minimal)
- event-booking: blacklist check, chat create, meeting-URL mint, follow-up publish.
- event-notifier: outbox claim, channel send (email/telegram).
- event-saver: projection execution.
- event-receiver: webhook validation.
Consistent span names; a thin helper in `telemetry.py` (`tracer.start_as_current_span`).

## Config & env (standard OTEL_*)

Per service: `OTEL_SERVICE_NAME`, `OTEL_EXPORTER_OTLP_ENDPOINT` (collector), `OTEL_TRACES_SAMPLER`
(`parentbased_always_on` dev / `parentbased_traceidratio` prod) + `OTEL_TRACES_SAMPLER_ARG`,
`OTEL_RESOURCE_ATTRIBUTES`, `OTEL_SDK_DISABLED` (default `true`; observability profile → `false`).
`.env.example` documents them; compose sets per-service `OTEL_SERVICE_NAME` and, in the
observability profile, the endpoint + `OTEL_SDK_DISABLED=false`.

## Dependencies (Python, per service)

`opentelemetry-sdk`, `opentelemetry-exporter-otlp-proto-grpc`, and the relevant instrumentation
packages (`-fastapi`, `-httpx`, `-sqlalchemy`/`-asyncpg`). FastStream tracing uses its built-in
`faststream.opentelemetry` middleware (no separate instrumentor). Re-locks each service's
`uv.lock` (independent of the event-schemas git dependency).

## Kubernetes / Helm (phase 4)

Add Tempo + OTel Collector to the `events-observability` umbrella (official
`grafana/tempo` and `open-telemetry/opentelemetry-collector` Helm charts). Services receive
the `OTEL_*` env via Vault/ESO (endpoint = in-cluster collector Service). Grafana datasource +
correlation provisioned the same way as the metrics/logs stack.

## Testing

Per service: an `InMemorySpanExporter`-based test asserting (a) the app boots with tracing
bootstrapped, (b) a span is produced for an inbound HTTP request / RabbitMQ message, and (c) an
inbound `traceparent` is continued (same trace id on the child span). Follows existing patterns
(`TestRabbitBroker`, httpx mocks, Dishka container resolution). No real collector/Tempo in tests.

## Documentation

- `docs/architecture/ONBOARDING.md` § Observability gains a **Tracing** subsection (what's traced,
  the pipeline, TraceQL examples, how to add a manual span).
- Root `CLAUDE.md` + `README.md`: observability mentions + new loopback ports (Tempo, collector).
- `docs/architecture/MESSAGE_CONTRACTS.md`: note `traceparent` rides alongside the `ce-*` headers.
- Per-service `docs/` touched where a manual span or instrumentor is added.

## Phased implementation

1. **Backend** — Tempo + OTel Collector in the observability profile + Grafana datasource and
   logs↔traces↔metrics correlation. Verify with a manual OTLP emit.
2. **Instrumentation** — per-service `telemetry.py` + auto-instrumentation (HTTP/DB/MQ) across all
   10 services; `ce-traceid`/`ce-spanid` from the active span; the structlog processor.
3. **Manual spans** — the targeted business-operation spans.
4. **k8s/Helm** — Tempo + collector in `events-observability`; `OTEL_*` via Vault.
5. **Docs**.

## Verification

`docker compose --profile observability up -d --build`; run `scripts/calcom_sim.py lifecycle`.
In Grafana Explore → Tempo, confirm a **single trace** spanning event-receiver → event-saver →
event-booking → event-shortener → event-notifier with child spans for HTTP calls, RabbitMQ
publish/consume, and DB queries. Confirm VictoriaLogs entries for that run carry the same
`trace_id` and link to the Tempo trace. Teardown `down -v`.

## Out of scope

- Browser/RUM tracing for the React SPAs (separate effort with frontend Sentry).
- Metrics derived from spans (span metrics connector) — can be added at the collector later.
- Tail-based sampling tuning and production retention sizing (collector supports it; defaults now).
- Log-based or trace-based alerting.
