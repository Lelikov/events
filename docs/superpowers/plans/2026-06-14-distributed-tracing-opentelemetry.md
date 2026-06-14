# Distributed Tracing (OpenTelemetry) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give the 10-service events platform end-to-end distributed tracing — one trace follows a cal.com webhook through HTTP, RabbitMQ, DBs and external calls — viewable in Grafana (Tempo) with logs↔traces correlation.

**Architecture:** Each Python service gets an identical `telemetry.py` bootstrap (OTLP → OpenTelemetry Collector → Tempo) plus auto-instrumentation (FastAPI, httpx, asyncpg, FastStream-RabbitMQ). OTel owns the W3C trace/span IDs; `ce-traceid`/`ce-spanid` and structlog logs are derived from the active span. Backend (Tempo + Collector) lives in the `observability` compose profile and the `events-observability` Helm umbrella. Disabled by default via `OTEL_SDK_DISABLED=true`; the observability profile enables it.

**Tech Stack:** Python 3.14, opentelemetry-sdk + OTLP/gRPC exporter, opentelemetry-instrumentation-{fastapi,httpx,asyncpg}, FastStream `RabbitTelemetryMiddleware`, Grafana Tempo, otel/opentelemetry-collector-contrib, Docker Compose, Helm.

---

## Reference: service matrix

| Service | Package | App entry | Broker | DB (asyncpg) | httpx out |
|---|---|---|---|---|---|
| event-receiver | `event_receiver` | `create_app()` + lifespan (`main.py:105`,`:61`) | yes (FastStream `fastapi.RabbitRouter`, `ioc.py:53`) | no | no |
| event-saver | `event_saver` | module `app` + lifespan (`main.py:56`,`:28`) | yes (consumer) | yes | yes |
| event-booking | `event_booking` | module `app` + lifespan (`main.py:64`,`:27`) | yes (`ioc.py:102`) | yes | yes |
| event-notifier | `event_notifier` | module `app` + lifespan (`main.py:81`,`:28`) | yes (`ioc.py:66`) | yes | yes |
| event-users | `event_users` | module `app` + lifespan (`main.py:75`,`:27`) | yes (consumer) | yes | yes |
| event-admin | `event_admin` | `create_app()` + lifespan (`main.py:50`,`:56`) | no | yes (`ioc.py:59`) | yes |
| event-shortener | `event_shortener` | module `app` + lifespan (`main.py:34`,`:22`) | no | yes | no |

"Broker" services add the FastStream telemetry middleware. "DB" services call `instrument_asyncpg()`. All services call `setup_tracing()` + `instrument_fastapi(app)` and add the structlog processor.

---

# Phase 1 — Tracing backend (observability profile)

### Task 1.1: Tempo service config

**Files:**
- Create: `docker/tempo/tempo.yaml`

- [ ] **Step 1: Write the Tempo config**

```yaml
# docker/tempo/tempo.yaml — single-binary Tempo for local/dev tracing.
server:
  http_listen_port: 3200
  grpc_listen_port: 9096

distributor:
  receivers:
    otlp:
      protocols:
        grpc:
          endpoint: 0.0.0.0:4317
        http:
          endpoint: 0.0.0.0:4318

ingester:
  max_block_duration: 5m

compactor:
  compaction:
    block_retention: 168h   # 7 days, matches VictoriaLogs

storage:
  trace:
    backend: local
    local:
      path: /var/tempo/blocks
    wal:
      path: /var/tempo/wal

usage_report:
  reporting_enabled: false
```

- [ ] **Step 2: Commit**

```bash
git add docker/tempo/tempo.yaml
git commit -m "feat(tracing): add Tempo config for local trace storage"
```

### Task 1.2: OpenTelemetry Collector config

**Files:**
- Create: `docker/otel-collector/config.yaml`

- [ ] **Step 1: Write the collector config**

```yaml
# docker/otel-collector/config.yaml — receives OTLP from services, exports to Tempo.
receivers:
  otlp:
    protocols:
      grpc:
        endpoint: 0.0.0.0:4317
      http:
        endpoint: 0.0.0.0:4318

processors:
  memory_limiter:
    check_interval: 1s
    limit_percentage: 80
    spike_limit_percentage: 20
  batch:
    timeout: 2s
  resource:
    attributes:
      - key: deployment.environment
        value: docker-compose
        action: upsert

exporters:
  otlp/tempo:
    endpoint: tempo:4317
    tls:
      insecure: true

service:
  pipelines:
    traces:
      receivers: [otlp]
      processors: [memory_limiter, batch, resource]
      exporters: [otlp/tempo]
```

- [ ] **Step 2: Commit**

```bash
git add docker/otel-collector/config.yaml
git commit -m "feat(tracing): add OpenTelemetry Collector config (OTLP -> Tempo)"
```

### Task 1.3: Add Tempo + collector to docker-compose (observability profile)

**Files:**
- Modify: `docker-compose.yml` (services block near the other observability services; volumes block)

- [ ] **Step 1: Add the two services**

Add under the observability services (alongside `victorialogs`/`vector`), each with `profiles: ["observability"]`:

```yaml
  tempo:
    image: grafana/tempo:2.7.1
    command: ["-config.file=/etc/tempo/tempo.yaml"]
    profiles: ["observability"]
    volumes:
      - ./docker/tempo/tempo.yaml:/etc/tempo/tempo.yaml:ro
      - tempo-data:/var/tempo
    ports:
      - "127.0.0.1:${TEMPO_PORT:-3200}:3200"
    healthcheck:
      test: ["CMD-SHELL", "wget -q -O- http://localhost:3200/ready | grep -q ready || exit 1"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 20s
    restart: unless-stopped

  otel-collector:
    image: otel/opentelemetry-collector-contrib:0.121.0
    command: ["--config=/etc/otelcol/config.yaml"]
    profiles: ["observability"]
    volumes:
      - ./docker/otel-collector/config.yaml:/etc/otelcol/config.yaml:ro
    ports:
      - "127.0.0.1:${OTEL_COLLECTOR_PORT:-4317}:4317"
    depends_on:
      tempo:
        condition: service_healthy
    restart: unless-stopped
```

- [ ] **Step 2: Add the volume**

In the top-level `volumes:` block add:

```yaml
  tempo-data:
```

- [ ] **Step 3: Verify the stack boots**

Run: `docker compose --profile observability up -d tempo otel-collector`
Then: `docker compose ps tempo otel-collector`
Expected: both `running`; `tempo` `healthy`.

- [ ] **Step 4: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(tracing): add Tempo + OTel Collector to observability profile"
```

### Task 1.4: Grafana — Tempo datasource + logs↔traces correlation

**Files:**
- Create: `docker/grafana/provisioning/datasources/tempo.yml` (or add to the existing datasources file — match the current layout)
- Modify: the VictoriaLogs datasource provisioning (add a `trace_id` derived field → Tempo)

- [ ] **Step 1: Inspect the current datasource provisioning layout**

Run: `ls docker/grafana/provisioning/datasources/ && cat docker/grafana/provisioning/datasources/*.y*ml`
Expected: see how `prometheus` and `victorialogs` datasources are declared; mirror that file/style.

- [ ] **Step 2: Add the Tempo datasource**

Add a datasource entry (in the existing file, or a new `tempo.yml` with `apiVersion: 1` + `datasources:`):

```yaml
  - name: Tempo
    type: tempo
    uid: tempo
    access: proxy
    url: http://tempo:3200
    jsonData:
      tracesToLogsV2:
        datasourceUid: victorialogs
        filterByTraceID: true
        spanStartTimeShift: -1h
        spanEndTimeShift: 1h
      serviceMap:
        datasourceUid: prometheus
      nodeGraph:
        enabled: true
```

- [ ] **Step 3: Add the trace_id derived field to the VictoriaLogs datasource**

In the `victorialogs` datasource `jsonData`, add:

```yaml
      derivedFields:
        - name: trace_id
          matcherType: label
          matcherRegex: trace_id
          url: "$${__value.raw}"
          datasourceUid: tempo
```

(Use `$$` to escape `$` in compose-mounted YAML if the file is templated; if it is a plain provisioning file, a single `$` is correct — match the existing file's convention.)

- [ ] **Step 4: Verify datasource health**

Run: `docker compose --profile observability up -d grafana && sleep 5`
Then open `http://localhost:${GRAFANA_PORT:-3001}` → Connections → Data sources → Tempo → "Test".
Expected: "Data source is working". (No traces yet — Phase 2 produces them.)

- [ ] **Step 5: Commit**

```bash
git add docker/grafana/provisioning/datasources/
git commit -m "feat(tracing): provision Grafana Tempo datasource + logs<->traces links"
```

### Task 1.5: Document the new env knobs

**Files:**
- Modify: `.env.example`

- [ ] **Step 1: Append the tracing env block**

```bash
# --- Distributed tracing (OpenTelemetry) ---
# Tracing is OFF unless the observability profile sets OTEL_SDK_DISABLED=false.
TEMPO_PORT=3200
OTEL_COLLECTOR_PORT=4317
# Per-service sampler (dev: sample everything). Prod: parentbased_traceidratio + OTEL_TRACES_SAMPLER_ARG=0.1
OTEL_TRACES_SAMPLER=parentbased_always_on
```

- [ ] **Step 2: Commit**

```bash
git add .env.example
git commit -m "docs(tracing): document tracing env vars in .env.example"
```

---

# Phase 2 — Instrumentation (per-service)

### Task 2.1: Canonical `telemetry.py` + unit test (event-receiver first)

This module is **identical** in every Python service. Build and test it in event-receiver, then copy it verbatim in Task 2.3.

**Files:**
- Create: `event-receiver/event_receiver/telemetry.py`
- Test: `event-receiver/tests/test_telemetry.py`
- Modify: `event-receiver/pyproject.toml` (deps)

- [ ] **Step 1: Add OTel dependencies**

In `event-receiver/pyproject.toml` `dependencies` add:

```toml
    "opentelemetry-sdk>=1.30.0",
    "opentelemetry-exporter-otlp-proto-grpc>=1.30.0",
    "opentelemetry-instrumentation-fastapi>=0.51b0",
    "opentelemetry-instrumentation-httpx>=0.51b0",
```

Then run: `cd event-receiver && uv lock`
Expected: resolves, adds opentelemetry packages.

- [ ] **Step 2: Write the failing test**

```python
# event-receiver/tests/test_telemetry.py
import os

import structlog
from opentelemetry import trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from event_receiver.telemetry import add_otel_trace_context, setup_tracing


def test_setup_tracing_noop_when_disabled(monkeypatch):
    monkeypatch.setenv("OTEL_SDK_DISABLED", "true")
    setup_tracing()
    assert not isinstance(trace.get_tracer_provider(), TracerProvider)


def test_log_processor_adds_trace_id_for_active_span():
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    tracer = provider.get_tracer("test")
    with tracer.start_as_current_span("op"):
        event_dict = add_otel_trace_context(None, "info", {"event": "hi"})
    assert len(event_dict["trace_id"]) == 32
    assert len(event_dict["span_id"]) == 16


def test_log_processor_skips_when_no_span():
    event_dict = add_otel_trace_context(None, "info", {"event": "hi"})
    assert "trace_id" not in event_dict
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd event-receiver && uv run pytest tests/test_telemetry.py -v`
Expected: FAIL — `ModuleNotFoundError: event_receiver.telemetry`.

- [ ] **Step 4: Write `telemetry.py`**

```python
# event-receiver/event_receiver/telemetry.py
"""OpenTelemetry tracing bootstrap. Identical across services; configured via OTEL_* env.

No-op unless OTEL_SDK_DISABLED is falsy (default in the base stack is disabled; the
observability compose profile / Helm enables it and sets OTEL_EXPORTER_OTLP_ENDPOINT).
"""

import os

from opentelemetry import trace
from opentelemetry.baggage.propagation import W3CBaggagePropagator
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.propagate import set_global_textmap
from opentelemetry.propagators.composite import CompositePropagator
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


def _disabled() -> bool:
    return os.getenv("OTEL_SDK_DISABLED", "").strip().lower() in ("true", "1", "yes")


def setup_tracing() -> None:
    """Install the global TracerProvider + W3C propagators. Idempotent; no-op when disabled."""
    if _disabled():
        return
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return
    provider = TracerProvider(resource=Resource.create())
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    set_global_textmap(
        CompositePropagator([TraceContextTextMapPropagator(), W3CBaggagePropagator()]),
    )


def instrument_fastapi(app: object) -> None:
    """Auto-instrument the FastAPI app (server spans + traceparent extraction) and httpx clients."""
    if _disabled():
        return
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
    from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

    FastAPIInstrumentor.instrument_app(app)
    HTTPXClientInstrumentor().instrument()


def instrument_asyncpg() -> None:
    """Auto-instrument asyncpg (DB query spans). Call only from services with a database."""
    if _disabled():
        return
    from opentelemetry.instrumentation.asyncpg import AsyncPGInstrumentor

    AsyncPGInstrumentor().instrument()


def rabbit_telemetry_middlewares() -> list:
    """FastStream RabbitMQ telemetry middleware(s) — span creation + traceparent over AMQP."""
    if _disabled():
        return []
    from faststream.rabbit.opentelemetry import RabbitTelemetryMiddleware

    return [RabbitTelemetryMiddleware(tracer_provider=trace.get_tracer_provider())]


def add_otel_trace_context(_logger, _method_name, event_dict):
    """structlog processor: stamp the active span's W3C trace/span id onto every log line."""
    span_context = trace.get_current_span().get_span_context()
    if span_context.is_valid:
        event_dict["trace_id"] = format(span_context.trace_id, "032x")
        event_dict["span_id"] = format(span_context.span_id, "016x")
    return event_dict
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd event-receiver && uv run pytest tests/test_telemetry.py -v`
Expected: 3 passed.

- [ ] **Step 6: Commit**

```bash
git -C event-receiver add event_receiver/telemetry.py tests/test_telemetry.py pyproject.toml uv.lock
git -C event-receiver commit -m "feat(tracing): add OpenTelemetry bootstrap module + tests"
```

### Task 2.2: Wire tracing into event-receiver (worked example)

**Files:**
- Modify: `event-receiver/event_receiver/main.py` (`create_app` `:105`, lifespan)
- Modify: `event-receiver/event_receiver/logger.py` (`:11` shared_processors)
- Modify: `event-receiver/event_receiver/ioc.py` (`:53` broker / RabbitRouter)
- Modify: `event-receiver/event_receiver/adapters/publisher.py` (`:111-113` ce-traceid)
- Modify: `event-receiver/event_receiver/utils.py` (`generate_trace_id`/`generate_span_id` → W3C hex)

- [ ] **Step 1: Bootstrap + instrument in `create_app`**

In `main.py`, inside `create_app()` right after `application = FastAPI(...)` (`:109`):

```python
    from event_receiver.telemetry import instrument_fastapi, setup_tracing

    setup_tracing()
    instrument_fastapi(application)
```

- [ ] **Step 2: Add the structlog processor**

In `logger.py`, import at top and insert into `shared_processors` (after `merge_contextvars`, `:15`):

```python
    from event_receiver.telemetry import add_otel_trace_context
    # ...
    shared_processors = [
        # ... existing entries ...
        structlog.contextvars.merge_contextvars,
        add_otel_trace_context,
        # ...
    ]
```

- [ ] **Step 3: Attach RabbitMQ telemetry middleware**

In `ioc.py`, find where `fastapi.RabbitRouter(...)` is constructed (the router that backs `provide_broker`, `:53`). Pass the middleware:

```python
    from event_receiver.telemetry import rabbit_telemetry_middlewares
    # router = fastapi.RabbitRouter(str(settings.rabbit_url), middlewares=[*rabbit_telemetry_middlewares()])
```

If `RabbitRouter` is built elsewhere, add `middlewares=[*rabbit_telemetry_middlewares()]` there. Preserve any existing middlewares.

- [ ] **Step 4: Make UUID generators emit W3C hex, prefer the active span**

In `utils.py` replace the bodies:

```python
import os

def generate_trace_id() -> str:
    """W3C trace id: 32 lowercase hex chars (128-bit)."""
    return os.urandom(16).hex()

def generate_span_id() -> str:
    """W3C span id: 16 lowercase hex chars (64-bit)."""
    return os.urandom(8).hex()
```

In `adapters/publisher.py` at the generation site (`:111-113`), prefer the active span:

```python
    from opentelemetry import trace as _trace

    _span_ctx = _trace.get_current_span().get_span_context()
    if _span_ctx.is_valid:
        trace_id = format(_span_ctx.trace_id, "032x")
        span_id = format(_span_ctx.span_id, "016x")
    else:
        trace_id = trace_id or generate_trace_id()
        span_id = generate_span_id()
```

- [ ] **Step 5: Update the trace-id test expectation**

In `event-receiver/tests/` find the test asserting `trace_id` format (UUID). UUIDs are 36 chars with dashes; W3C is 32 hex no dashes. Update any assertion like `len(trace_id) == 36`/UUID-parse to expect 32-char lowercase hex (e.g. `assert len(headers["ce-traceid"]) == 32`). If no such test exists, skip.

- [ ] **Step 6: Run the full receiver suite**

Run: `cd event-receiver && uv run pytest -q`
Expected: all pass (tracing is a no-op in tests since `OTEL_SDK_DISABLED` is unset → treated as enabled but with no exporter target; if export warnings appear, set `OTEL_SDK_DISABLED=true` in `tests/conftest.py` env or `pytest.ini` `env`). Add to `event-receiver/pyproject.toml` `[tool.pytest.ini_options]`:

```toml
env = ["OTEL_SDK_DISABLED=true"]
```

If the `pytest-env` plugin is not present, instead set it at the top of `tests/conftest.py`:

```python
import os
os.environ.setdefault("OTEL_SDK_DISABLED", "true")
```

- [ ] **Step 7: Commit**

```bash
git -C event-receiver add -A
git -C event-receiver commit -m "feat(tracing): instrument event-receiver (FastAPI/httpx/RabbitMQ, span-derived ce-traceid)"
```

### Task 2.3: Copy `telemetry.py` + wire the other 6 services

For **each** service below, do the same wiring as Task 2.2, adapted by the matrix. The `telemetry.py` file is byte-identical except the import path in the module docstring is irrelevant — copy it verbatim (only the package it lives in changes).

Per service, repeat these steps:

- [ ] **Step A: deps** — add to `<service>/pyproject.toml` `dependencies`:
  `opentelemetry-sdk>=1.30.0`, `opentelemetry-exporter-otlp-proto-grpc>=1.30.0`, `opentelemetry-instrumentation-fastapi>=0.51b0`, `opentelemetry-instrumentation-httpx>=0.51b0`, and **for DB services** `opentelemetry-instrumentation-asyncpg>=0.51b0`. Then `cd <service> && uv lock`.
- [ ] **Step B: copy** `event-receiver/event_receiver/telemetry.py` → `<service>/<package>/telemetry.py` verbatim.
- [ ] **Step C: bootstrap + FastAPI/httpx** — call `setup_tracing()` then `instrument_fastapi(app)` right after the `app = FastAPI(...)` line (`main.py`) or in `create_app` (admin).
- [ ] **Step D: DB** — DB services also call `instrument_asyncpg()` in the same place (after `setup_tracing()`).
- [ ] **Step E: broker** — broker services pass `middlewares=[*rabbit_telemetry_middlewares()]` to the `RabbitBroker(...)` constructor in `ioc.py` (preserve existing middlewares).
- [ ] **Step F: logger** — add `add_otel_trace_context` to the processor list in `<service>/<package>/logger.py` (after `merge_contextvars`).
- [ ] **Step G: test isolation** — ensure `OTEL_SDK_DISABLED=true` for the test run (pytest env or conftest, as in Task 2.2 Step 6).
- [ ] **Step H: run** `cd <service> && uv run pytest -q` → all pass.
- [ ] **Step I: commit** `git -C <service> add -A && git -C <service> commit -m "feat(tracing): instrument <service>"`.

Apply to, with their per-service specifics:

- [ ] **event-saver** (`event_saver`): broker=yes, db=yes, httpx=yes. Broker is built in its consumer/ioc wiring — add middleware where `RabbitBroker(...)` is constructed.
- [ ] **event-booking** (`event_booking`): broker=yes (`ioc.py:103`), db=yes (`ioc.py:80`), httpx=yes. Note: booking constructs ad-hoc `httpx.AsyncClient` instances in `adapters/shortener.py`/`adapters/events.py`; `HTTPXClientInstrumentor().instrument()` patches them globally, no per-client change needed.
- [ ] **event-notifier** (`event_notifier`): broker=yes (`ioc.py:67`), db=yes (`ioc.py:43`), httpx=yes.
- [ ] **event-users** (`event_users`): broker=yes (consumer), db=yes, httpx=yes (CRM client).
- [ ] **event-admin** (`event_admin`): broker=no, db=yes (`ioc.py:59`), httpx=yes. Wire in `create_app()` (`main.py:50`).
- [ ] **event-shortener** (`event_shortener`): broker=no, db=yes, httpx=no.

### Task 2.4: Propagation integration test (event-saver)

Prove an inbound `traceparent` continues the trace into a consumer span.

**Files:**
- Test: `event-saver/tests/test_trace_propagation.py`

- [ ] **Step 1: Write the test**

```python
# event-saver/tests/test_trace_propagation.py
import os
os.environ.setdefault("OTEL_SDK_DISABLED", "false")

from opentelemetry import context, trace
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator


def test_inbound_traceparent_is_continued():
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    trace.set_tracer_provider(provider)
    tracer = provider.get_tracer("test")

    # Simulate an inbound message carrying a W3C traceparent header.
    carrier = {"traceparent": "00-0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-01"}
    ctx = TraceContextTextMapPropagator().extract(carrier=carrier)
    token = context.attach(ctx)
    try:
        with tracer.start_as_current_span("consume"):
            pass
    finally:
        context.detach(token)

    spans = exporter.get_finished_spans()
    assert spans[0].context.trace_id == 0x0AF7651916CD43DD8448EB211C80319C
```

- [ ] **Step 2: Run it**

Run: `cd event-saver && uv run pytest tests/test_trace_propagation.py -v`
Expected: PASS — child span inherits the inbound trace id.

- [ ] **Step 3: Commit**

```bash
git -C event-saver add tests/test_trace_propagation.py
git -C event-saver commit -m "test(tracing): assert inbound traceparent continues the trace"
```

### Task 2.5: Compose env — enable tracing in the observability profile

**Files:**
- Modify: `docker-compose.yml` (each of the 7 Python services' `environment`)

- [ ] **Step 1: Add OTEL_* env to every Python service**

For each Python service add to its `environment` block:

```yaml
      OTEL_SERVICE_NAME: event-receiver   # <- the service's own name
      OTEL_SDK_DISABLED: ${OTEL_SDK_DISABLED:-true}
      OTEL_EXPORTER_OTLP_ENDPOINT: ${OTEL_EXPORTER_OTLP_ENDPOINT:-http://otel-collector:4317}
      OTEL_EXPORTER_OTLP_PROTOCOL: grpc
      OTEL_TRACES_SAMPLER: ${OTEL_TRACES_SAMPLER:-parentbased_always_on}
```

- [ ] **Step 2: Make the observability profile flip the switch**

The bare `up` keeps `OTEL_SDK_DISABLED=true` (default). To enable with the profile, document that `--profile observability` runs should export `OTEL_SDK_DISABLED=false` (add to `.env.example` comment from Task 1.5, and to the README in Phase 5). Verify both states:

Run (disabled): `docker compose up -d event-receiver && docker compose logs event-receiver | grep -i otel || echo "no otel export (expected)"`
Run (enabled): `OTEL_SDK_DISABLED=false docker compose --profile observability up -d --build` then drive traffic in Task 2.6.

- [ ] **Step 3: Commit**

```bash
git add docker-compose.yml
git commit -m "feat(tracing): pass OTEL_* env to all Python services (gated by OTEL_SDK_DISABLED)"
```

### Task 2.6: End-to-end verification

- [ ] **Step 1: Bring up the full stack with tracing on**

Run:
```bash
OTEL_SDK_DISABLED=false docker compose --profile observability up -d --build
```
Expected: all services healthy incl. `tempo`, `otel-collector`.

- [ ] **Step 2: Generate a trace**

Run: `uv run scripts/calcom_sim.py lifecycle`
Expected: booking created → rescheduled → cancelled chain processed.

- [ ] **Step 3: Confirm the trace in Grafana**

Open `http://localhost:${GRAFANA_PORT:-3001}` → Explore → Tempo → "Search". Pick a recent trace.
Expected: one trace spanning **event-receiver → event-saver → event-booking → event-shortener → event-notifier**, with child spans for HTTP POSTs, RabbitMQ publish/consume, and DB queries.

- [ ] **Step 4: Confirm logs↔traces correlation**

In Explore → VictoriaLogs, filter to that run; confirm log lines carry a `trace_id` field equal to the Tempo trace id, and the derived `trace_id` link opens the Tempo trace.

- [ ] **Step 5: Tear down**

Run: `docker compose --profile observability down -v`

---

# Phase 3 — Targeted manual spans

Add a handful of business-operation spans. Pattern (same everywhere): get a module tracer and wrap the operation.

```python
from opentelemetry import trace
_tracer = trace.get_tracer(__name__)

# inside the method:
with _tracer.start_as_current_span("booking.blacklist_check") as span:
    span.set_attribute("booking.uid", booking_uid)
    ... existing logic ...
```

When `OTEL_SDK_DISABLED=true` the tracer is a no-op — zero overhead, safe in tests.

### Task 3.1: event-booking spans

**Files:** Modify `event-booking/event_booking/controllers/booking.py` (and `controllers/meeting.py`, `controllers/chat.py`, `adapters/events.py` as noted).

- [ ] **Step 1** Wrap: blacklist check (in the `booking.created` path), chat create (`chat.py`), meeting-URL mint (`meeting.py`), follow-up publish (`adapters/events.py`). Span names: `booking.blacklist_check`, `booking.chat_create`, `booking.meeting_url_mint`, `booking.publish_followup`. Add 1-2 attributes each (booking uid, recipient role).
- [ ] **Step 2** Run `cd event-booking && uv run pytest -q` → pass.
- [ ] **Step 3** Commit `git -C event-booking commit -am "feat(tracing): manual spans for blacklist/chat/meeting/publish"`.

### Task 3.2: event-notifier spans

**Files:** Modify `event-notifier/event_notifier/adapters/outbox_sender.py`, `infrastructure/channels/*`.

- [ ] **Step 1** Wrap: outbox claim (`outbox_sender.py`), channel send (each `INotificationChannel.send`). Span names: `notifier.outbox_claim`, `notifier.channel_send` with attribute `channel` (email/telegram).
- [ ] **Step 2** Run `cd event-notifier && uv run pytest -q` → pass.
- [ ] **Step 3** Commit `git -C event-notifier commit -am "feat(tracing): manual spans for outbox claim + channel send"`.

### Task 3.3: event-saver + event-receiver spans

**Files:** Modify `event-saver/event_saver/application/services/projection_executor.py`; `event-receiver/event_receiver/controllers/` (validation).

- [ ] **Step 1** saver: wrap projection execution (`projection.execute`, attribute = projection name). receiver: wrap webhook validation (`receiver.validate_webhook`, attribute = source).
- [ ] **Step 2** Run both suites → pass.
- [ ] **Step 3** Commit in each repo: `feat(tracing): manual span for projection execution` / `... webhook validation`.

---

# Phase 4 — Kubernetes / Helm

### Task 4.1: Tempo + OTel Collector in events-observability umbrella

**Files:**
- Modify: `deploy/helm/umbrella/events-observability/Chart.yaml` (add dependencies)
- Modify: `deploy/helm/umbrella/events-observability/values*.yaml`

- [ ] **Step 1: Add chart dependencies**

In `Chart.yaml` `dependencies` add (pin to current stable):

```yaml
  - name: tempo
    version: 1.18.2
    repository: https://grafana.github.io/helm-charts
  - name: opentelemetry-collector
    version: 0.111.0
    repository: https://open-telemetry.github.io/opentelemetry-helm-charts
```

- [ ] **Step 2: Values for collector + Tempo**

In `values.yaml` add a collector config mirroring `docker/otel-collector/config.yaml` (OTLP receivers → `otlp/tempo` exporter pointing at the in-cluster Tempo service), and Tempo with the OTLP receiver enabled. Mode `deployment` for the collector.

- [ ] **Step 3: Lint + template**

Run: `make -C deploy/scripts lint`
Expected: 0 failures (helm lint + kubeconform green).

- [ ] **Step 4: Commit**

```bash
git add deploy/helm/umbrella/events-observability/
git commit -m "feat(tracing): add Tempo + OTel Collector to events-observability umbrella"
```

### Task 4.2: Service OTEL_* env via Vault/ESO

**Files:**
- Modify: `deploy/scripts/seed-vault.sh` (or the per-service Vault seed source)
- Modify: per-service Helm values / ExternalSecret mapping if the env list is explicit

- [ ] **Step 1** Add `OTEL_SDK_DISABLED=false`, `OTEL_EXPORTER_OTLP_ENDPOINT=http://<release>-opentelemetry-collector:4317`, `OTEL_SERVICE_NAME=<service>`, `OTEL_TRACES_SAMPLER=parentbased_traceidratio`, `OTEL_TRACES_SAMPLER_ARG=0.1` to each service's Vault path (`secret/events/<service>`).
- [ ] **Step 2** Verify with the kind smoke (optional, heavy): `make -C deploy/scripts smoke` and confirm pods Ready + collector receiving. If skipped, note it.
- [ ] **Step 3** Commit `git add deploy/ && git commit -m "feat(tracing): seed OTEL_* env for services via Vault"`.

---

# Phase 5 — Documentation

### Task 5.1: Onboarding + root docs

**Files:**
- Modify: `docs/architecture/ONBOARDING.md` (Observability § → add Tracing subsection)
- Modify: `README.md` + `CLAUDE.md` (ports table + observability paragraph)
- Modify: `docs/architecture/MESSAGE_CONTRACTS.md` (traceparent rides alongside ce-*)

- [ ] **Step 1** Write the Tracing subsection: pipeline (services → collector → Tempo), how to enable (`OTEL_SDK_DISABLED=false` + observability profile), a TraceQL example, how to add a manual span, the `traceparent`-beside-`ce-*` propagation note, and the new loopback ports (Tempo 3200, collector 4317).
- [ ] **Step 2** Update the ports tables in `README.md` and root `CLAUDE.md`.
- [ ] **Step 3** Commit `git add docs README.md CLAUDE.md && git commit -m "docs(tracing): document distributed tracing setup and usage"`.

### Task 5.2: Per-service doc touches

**Files:** each instrumented service's `docs/SERVICE_OVERVIEW.md` (one line: "Tracing: OTel auto-instrumentation + manual spans, exported via OTLP").

- [ ] **Step 1** Add the line to the 7 services.
- [ ] **Step 2** Commit per repo.

---

## Self-Review notes

- **Spec coverage:** Phase 1 ↔ backend; Phase 2 ↔ telemetry.py + auto-instrumentation + ce-traceid-from-span + structlog processor; Phase 3 ↔ manual spans; Phase 4 ↔ k8s/Helm; Phase 5 ↔ docs. Sampling (`OTEL_TRACES_SAMPLER`) and the `OTEL_SDK_DISABLED` gate are in Tasks 1.5/2.5/4.2. Verification (Task 2.6) matches the spec's verification.
- **Type/name consistency:** `setup_tracing()`, `instrument_fastapi(app)`, `instrument_asyncpg()`, `rabbit_telemetry_middlewares()`, `add_otel_trace_context(...)` are defined in Task 2.1 and used by the same names in 2.2/2.3. The structlog field is `trace_id` everywhere (telemetry.py, Grafana derived field in Task 1.4, logs correlation in 2.6).
- **Known follow-up to confirm during execution:** exact `fastapi.RabbitRouter` construction site in event-receiver `ioc.py` (Task 2.2 Step 3) and the per-service `RabbitBroker(...)` middleware kwarg — verify the constructor accepts `middlewares=` in the installed FastStream (it does for `RabbitBroker`; the router wraps it).
