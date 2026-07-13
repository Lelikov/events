# event-booker public booking BFF (срез 4b.1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new stateless FastAPI service `event-booker` exposing an unauthenticated public API that turns a guest (name+email) into a scheduling booking, holding the scheduling + users service keys server-side.

**Architecture:** BFF pattern. Two httpx clients (`SchedulingClient` Bearer `SCHEDULING_API_KEY` → event-scheduling; `UsersClient` Bearer `EVENT_USERS_TOKEN` → event-users). `GuestBookingService` resolves-or-creates a `client` user by email, then creates the booking. Four public `/api/public/*` routes. No DB, no background tasks. Mirrors event-scheduling's app skeleton (telemetry/metrics/logger/routes/config), minus DB/alembic/lifespan.

**Tech Stack:** Python 3.14, uv, FastAPI, Dishka DI, httpx, Pydantic/pydantic-settings, structlog, prometheus-client, OpenTelemetry (gated), pytest + httpx.MockTransport.

## Global Constraints

- New service `event-booker`, tracked by the ROOT repo `/Users/alexandrlelikov/PycharmProjects/events`. Commit in ROOT on branch `feat/event-booker-bff` (create off `main` before Task 1).
- Additive ONLY: do NOT modify event-scheduling, event-users, event-booking, event-receiver. This slice creates one new service + docker-compose/docs edits.
- Code style: **NO `elif`**; **avoid `else`** (early returns/guards/mapping dicts); Ruff line length 120; `target-version = py314`; frozen-dataclass DTOs; Pydantic only in `schemas/`; Protocol interfaces in `interfaces/`; httpx clients in `adapters/`.
- Trust boundary: public routes have NO auth. The service holds keys server-side ONLY (never in responses/logs). Outward responses MUST NOT leak `client_user_id` / `host_user_id` / raw upstream bodies. Guest user role is HARD-FIXED to `"client"`.
- Upstream contracts (exact): event-scheduling (Bearer `SCHEDULING_API_KEY`): `GET /api/v1/event-types` → `{"items":[{"id","slug","title","duration_minutes",…}]}`; `GET /api/v1/event-types/{id}` → EventTypeResponse (same fields); `GET /api/v1/slots?event_type_id=&start=&end=&time_zone=` → `{"event_type_id","time_zone","slots":{"<date>":["<iso>"]}}`; `POST /api/v1/bookings` body `{"event_type_id","client_user_id","start_time","attendee_time_zone"}` header `actor_source: booker` → 201 `{"id","event_type_id","host_user_id","client_user_id","start_time","end_time","status","attendee_time_zone","created_at"}`, 409 on slot conflict, 404 on missing type. event-users (Bearer `EVENT_USERS_TOKEN`): `GET /api/users/by-identity?email=&role=client` → 200 `{"id","email","name","role","time_zone"}` | 404; `POST /api/users` body `{"email","name","role":"client","time_zone"}` → 201 UserResponse | 409 on duplicate.
- Error→HTTP mapping: `ValidationError`→422, `NotFoundError`→404, `ConflictError`→409, `SlotUnavailableError`→409, `UpstreamError`→502.
- Dev key defaults (match `docker-compose.services.yml`): `scheduling_api_key="dev-scheduling-api-key-3f9c2e1a7b64d508"`, `event_users_token="dev-users-bearer-2a7d9e4f8c1b6350"`, `event_scheduling_url="http://event-scheduling:8888"`, `event_users_url="http://event-users:8888"`. Host port 8005 (internal 8888).

---

## File Structure

New package `event-booker/` (root-tracked):
- `pyproject.toml`, `Dockerfile`, `entrypoint.sh`, `uvicorn_config.json`, `.dockerignore`
- `event_booker/__init__.py`
- `event_booker/config.py` — `Settings`
- `event_booker/errors.py` — domain error types
- `event_booker/telemetry.py`, `event_booker/metrics.py`, `event_booker/logger.py` — copied verbatim from event-scheduling (imports renamed to `event_booker`)
- `event_booker/routes.py` — `root_router` (`/health`,`/ready`,`/metrics`)
- `event_booker/dto.py` — frozen DTOs
- `event_booker/interfaces/__init__.py`, `event_booker/interfaces/clients.py` — `ISchedulingClient`, `IUsersClient`
- `event_booker/adapters/__init__.py`, `event_booker/adapters/scheduling_client.py`, `event_booker/adapters/users_client.py`
- `event_booker/services/__init__.py`, `event_booker/services/guest_booking.py`
- `event_booker/schemas/__init__.py`, `event_booker/schemas/public.py`
- `event_booker/routers/__init__.py`, `event_booker/routers/public.py`
- `event_booker/ioc.py` — Dishka providers
- `event_booker/main.py` — app assembly + error handlers
- `tests/` — `conftest.py`, `test_health.py`, `test_scheduling_client.py`, `test_users_client.py`, `test_guest_booking.py`, `test_public_api.py`
- Modify: `docker-compose.services.yml`, root `CLAUDE.md` (port table), root `docs/architecture/ONBOARDING.md` + `ARCHITECTURE.md`

---

## Task 1: Scaffold — bootable service (config, errors, telemetry/metrics/logger, health)

**Files:**
- Create: `event-booker/pyproject.toml`, `event-booker/Dockerfile`, `event-booker/entrypoint.sh`, `event-booker/uvicorn_config.json`, `event-booker/.dockerignore`
- Create: `event_booker/__init__.py`, `event_booker/config.py`, `event_booker/errors.py`, `event_booker/telemetry.py`, `event_booker/metrics.py`, `event_booker/logger.py`, `event_booker/routes.py`, `event_booker/ioc.py`, `event_booker/main.py`
- Test: `event-booker/tests/conftest.py`, `event-booker/tests/test_health.py`

**Interfaces:**
- Produces: `Settings` (fields below); `errors.py` types `DomainError, ValidationError, NotFoundError, ConflictError, SlotUnavailableError, UpstreamError`; `main.app`; the error→HTTP handler mapping `{ValidationError:422, NotFoundError:404, ConflictError:409, SlotUnavailableError:409, UpstreamError:502}`.

- [ ] **Step 1: Copy the runtime boilerplate verbatim** from event-scheduling, renaming the package in imports. From `/Users/alexandrlelikov/PycharmProjects/events/event-scheduling/` copy into `event-booker/`:
  - `event_scheduling/telemetry.py` → `event_booker/telemetry.py` (no import changes needed — it imports only stdlib/otel).
  - `event_scheduling/metrics.py` → `event_booker/metrics.py` (change the module docstring's service name to event-booker; no internal imports to rename).
  - `event_scheduling/logger.py` → `event_booker/logger.py` (change `from event_scheduling.telemetry import add_otel_trace_context` → `from event_booker.telemetry import add_otel_trace_context`).
  - `uvicorn_config.json` → `event-booker/uvicorn_config.json` (verbatim).
  Verify no remaining `event_scheduling` references: `grep -rn event_scheduling event-booker/event_booker/ event-booker/uvicorn_config.json` returns nothing.

- [ ] **Step 2: `event-booker/pyproject.toml`** (no DB deps — drop alembic/asyncpg/sqlalchemy/greenlet/ujson; keep the otel asyncpg instrumentation OUT):

```toml
[project]
name = "event-booker"
version = "0.1.0"
description = "Public booking BFF: guest -> client resolution + booking, holding scheduling/users keys server-side"
requires-python = ">=3.14"
dependencies = [
    "dishka>=1.8.0",
    "fastapi>=0.135.1",
    "httpx>=0.28.0",
    "opentelemetry-sdk>=1.30.0",
    "opentelemetry-exporter-otlp-proto-grpc>=1.30.0",
    "opentelemetry-instrumentation-fastapi>=0.51b0",
    "opentelemetry-instrumentation-httpx>=0.51b0",
    "prometheus-client>=0.25.0",
    "pydantic[email]>=2.10.0",
    "pydantic-settings>=2.13.1",
    "structlog>=25.5.0",
    "uvicorn>=0.41.0",
]

[dependency-groups]
dev = [
    "httpx>=0.28.0",
    "pytest>=8.0.0",
    "pytest-asyncio>=0.25.0",
    "ruff>=0.15.4",
]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
pythonpath = ["."]

[tool.ruff]
exclude = [".env", ".git", ".mypy_cache", ".pytest_cache", ".venv"]
fix = true
unsafe-fixes = true
show-fixes = true
target-version = "py314"
line-length = 120

[tool.ruff.format]
docstring-code-format = true

[tool.ruff.lint]
ignore = ["ANN401", "COM812", "D1", "D203", "D213", "EM", "FA", "FBT", "G004", "INP001", "ISC001", "PERF203", "PLR", "RET501", "RUF001", "S311", "TC001", "TC002", "TC003", "TRY003", "UP040"]
select = ["ALL"]

[tool.ruff.lint.extend-per-file-ignores]
"__init__.py" = ["F401", "F403"]
"tests/*.py" = ["ANN001", "ANN002", "ANN003", "ANN201", "ANN202", "ANN401", "ARG002", "E402", "PLC0415", "PLR2004", "PT019", "S101", "S311", "S603", "S607"]

[tool.ruff.lint.isort]
no-lines-before = ["local-folder", "standard-library"]
lines-after-imports = 2
```

Then generate the lockfile: `cd event-booker && uv sync` (creates `.venv` + `uv.lock`).

- [ ] **Step 3: `event_booker/config.py`**:

```python
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    debug: bool = False
    log_level: str = "INFO"

    # Upstreams (server-side keys — never exposed to the browser).
    event_scheduling_url: str = "http://event-scheduling:8888"
    scheduling_api_key: str = "dev-scheduling-api-key-3f9c2e1a7b64d508"  # noqa: S105 - dev default; real via env/Vault
    event_users_url: str = "http://event-users:8888"
    event_users_token: str = "dev-users-bearer-2a7d9e4f8c1b6350"  # noqa: S105 - dev default; real via env/Vault

    # Comma-separated allowed CORS origins for the public SPA (4b.2). Empty = none (same-origin proxy).
    booker_cors_origins: str = ""

    @property
    def cors_origins_list(self) -> list[str]:
        return [o.strip() for o in self.booker_cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: `event_booker/errors.py`**:

```python
class DomainError(Exception):
    """Base for domain errors mapped to HTTP status codes in main.py."""


class ValidationError(DomainError):
    """Invalid input — HTTP 422."""


class NotFoundError(DomainError):
    """Missing resource (e.g. event type) — HTTP 404."""


class ConflictError(DomainError):
    """Uniqueness/state conflict from an upstream — HTTP 409 (usually handled internally)."""


class SlotUnavailableError(DomainError):
    """Requested slot is no longer bookable — HTTP 409."""


class UpstreamError(DomainError):
    """An upstream service failed or returned an unexpected status — HTTP 502."""
```

- [ ] **Step 5: `event_booker/routes.py`** (health/ready/metrics — mirror event-scheduling):

```python
from fastapi import APIRouter
from starlette.responses import Response

from event_booker import metrics


root_router = APIRouter()


@root_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@root_router.get("/ready")
async def ready() -> dict[str, str]:
    return {"status": "ready"}


@root_router.get("/metrics")
async def metrics_endpoint() -> Response:
    return metrics.metrics_response()
```

- [ ] **Step 6: `event_booker/ioc.py`** (Settings provider only for now):

```python
from dishka import Provider, Scope, provide

from event_booker.config import Settings, get_settings


class AppProvider(Provider):
    @provide(scope=Scope.APP)
    def provide_settings(self) -> Settings:
        return get_settings()
```

- [ ] **Step 7: `event_booker/main.py`** (app + error handler; NO lifespan/DB/background):

```python
from logging import getLevelNamesMapping

import structlog
from dishka import make_async_container
from dishka.integrations.fastapi import FastapiProvider, setup_dishka
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from event_booker.config import get_settings
from event_booker.errors import (
    ConflictError,
    NotFoundError,
    SlotUnavailableError,
    UpstreamError,
    ValidationError,
)
from event_booker.ioc import AppProvider
from event_booker.logger import setup_logger
from event_booker.metrics import HttpMetricsMiddleware
from event_booker.routes import root_router
from event_booker.telemetry import instrument_fastapi, setup_tracing


container = make_async_container(AppProvider(), FastapiProvider())
logger = structlog.get_logger(__name__)

_settings = get_settings()
setup_logger(log_level=getLevelNamesMapping().get(_settings.log_level), console_render=_settings.debug)

app = FastAPI(title="event-booker", version="0.1.0")
setup_tracing()
instrument_fastapi(app)
setup_dishka(container=container, app=app)
app.include_router(root_router)
app.add_middleware(HttpMetricsMiddleware)

if _settings.cors_origins_list:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_settings.cors_origins_list,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

_STATUS = {
    ValidationError: 422,
    NotFoundError: 404,
    ConflictError: 409,
    SlotUnavailableError: 409,
    UpstreamError: 502,
}


async def _domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    status = next((code for typ, code in _STATUS.items() if isinstance(exc, typ)), 500)
    return JSONResponse(status_code=status, content={"detail": str(exc)})


for _err in (ValidationError, NotFoundError, ConflictError, SlotUnavailableError, UpstreamError):
    app.add_exception_handler(_err, _domain_error_handler)
```

- [ ] **Step 8: `event_booker/__init__.py`** empty; also create empty `event_booker/interfaces/__init__.py`, `event_booker/adapters/__init__.py`, `event_booker/services/__init__.py`, `event_booker/schemas/__init__.py`, `event_booker/routers/__init__.py` (packages used by later tasks).

- [ ] **Step 9: `event-booker/tests/conftest.py`** (a plain app builder + TestClient; no DB):

```python
from collections.abc import Generator

import pytest


@pytest.fixture
def client() -> Generator:
    from starlette.testclient import TestClient

    from event_booker.main import app

    with TestClient(app) as test_client:
        yield test_client
```

- [ ] **Step 10: `event-booker/tests/test_health.py`**:

```python
def test_health_ok(client) -> None:
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


def test_ready_ok(client) -> None:
    assert client.get("/ready").status_code == 200


def test_metrics_exposed(client) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200
    assert "http_requests_total" in r.text
```

- [ ] **Step 11: Run — expect PASS.** `cd /Users/alexandrlelikov/PycharmProjects/events/event-booker && uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — 3 passed, ruff clean.

- [ ] **Step 12: `Dockerfile`, `entrypoint.sh`, `.dockerignore`.**

`event-booker/entrypoint.sh` (no migrations — stateless):
```sh
#!/bin/sh
set -e
exec uvicorn event_booker.main:app --host 0.0.0.0 --port 8888 --log-config uvicorn_config.json
```

`event-booker/Dockerfile`:
```dockerfile
# Build context is the event-booker directory itself (no event-schemas dependency):
#   docker build -f event-booker/Dockerfile event-booker
ARG BASE_IMAGE="python:3.14.0"

FROM ${BASE_IMAGE} AS base
ENV APP_PATH="/app/event-booker"
ENV PATH="${APP_PATH}/.venv/bin:${PATH}"
WORKDIR ${APP_PATH}

FROM base AS deps
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir --upgrade uv==0.11.3
COPY pyproject.toml uv.lock ${APP_PATH}/
RUN uv sync --frozen --no-install-project --no-dev

FROM deps AS development
COPY event_booker ${APP_PATH}/event_booker
COPY uvicorn_config.json ${APP_PATH}/
COPY entrypoint.sh ${APP_PATH}/entrypoint.sh
RUN chmod +x ${APP_PATH}/entrypoint.sh
EXPOSE 8888
ENTRYPOINT ["./entrypoint.sh"]
```

`event-booker/.dockerignore`:
```
.venv
__pycache__
tests
.pytest_cache
.ruff_cache
```

- [ ] **Step 13: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker/
git commit -m "feat(booker): scaffold event-booker BFF service (config, errors, health) (slice 4b.1)"
```

---

## Task 2: DTOs + interfaces + SchedulingClient

**Files:**
- Create: `event_booker/dto.py`, `event_booker/interfaces/clients.py`, `event_booker/adapters/scheduling_client.py`
- Test: `event-booker/tests/test_scheduling_client.py`

**Interfaces:**
- Consumes: `errors.py` (`NotFoundError`, `SlotUnavailableError`, `UpstreamError`).
- Produces:
  - `dto.py`: `EventTypeDTO(id: UUID, slug: str, title: str, duration_minutes: int)`; `SlotsResult(event_type_id: UUID, time_zone: str, slots: dict[str, list[str]])`; `BookingResult(id: UUID, start_time: datetime, end_time: datetime, status: str)`; `BookingConfirmation(booking_id: UUID, event_type_title: str, start_time: datetime, end_time: datetime, status: str, time_zone: str)` — all frozen.
  - `interfaces/clients.py`: `ISchedulingClient` (`list_event_types()`, `get_event_type(id)`, `get_slots(event_type_id, start, end, time_zone)`, `create_booking(event_type_id, client_user_id, start_time, attendee_time_zone)`), `IUsersClient` (`get_client_by_email(email)`, `create_client(email, name, time_zone)`).
  - `adapters/scheduling_client.py`: `SchedulingClient(base_url, api_key, *, transport=None)` implementing `ISchedulingClient`.

- [ ] **Step 1: `event_booker/dto.py`**:

```python
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class EventTypeDTO:
    id: UUID
    slug: str
    title: str
    duration_minutes: int


@dataclass(frozen=True)
class SlotsResult:
    event_type_id: UUID
    time_zone: str
    slots: dict[str, list[str]]


@dataclass(frozen=True)
class BookingResult:
    id: UUID
    start_time: datetime
    end_time: datetime
    status: str


@dataclass(frozen=True)
class BookingConfirmation:
    booking_id: UUID
    event_type_title: str
    start_time: datetime
    end_time: datetime
    status: str
    time_zone: str
```

- [ ] **Step 2: `event_booker/interfaces/clients.py`**:

```python
from datetime import datetime
from typing import Protocol
from uuid import UUID

from event_booker.dto import BookingResult, EventTypeDTO, SlotsResult


class ISchedulingClient(Protocol):
    async def list_event_types(self) -> list[EventTypeDTO]: ...
    async def get_event_type(self, event_type_id: UUID) -> EventTypeDTO: ...
    async def get_slots(
        self, event_type_id: UUID, start: datetime, end: datetime, time_zone: str
    ) -> SlotsResult: ...
    async def create_booking(
        self, event_type_id: UUID, client_user_id: UUID, start_time: datetime, attendee_time_zone: str
    ) -> BookingResult: ...


class IUsersClient(Protocol):
    async def get_client_by_email(self, email: str) -> UUID | None: ...
    async def create_client(self, email: str, name: str, time_zone: str) -> UUID: ...
```

- [ ] **Step 3: Write the failing test** `event-booker/tests/test_scheduling_client.py`:

```python
import datetime as dt
import json
from uuid import uuid4

import httpx
import pytest

from event_booker.adapters.scheduling_client import SchedulingClient
from event_booker.errors import NotFoundError, SlotUnavailableError, UpstreamError

BASE = "http://scheduling.test"
KEY = "sched-key"


def _client(handler) -> SchedulingClient:
    return SchedulingClient(BASE, KEY, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_list_event_types_projects_public_fields() -> None:
    et_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {KEY}"
        assert request.url.path == "/api/v1/event-types"
        return httpx.Response(
            200,
            json={"items": [{"id": str(et_id), "slug": "intro", "title": "Intro", "duration_minutes": 30,
                             "scheduling_type": "collective", "hosts": [], "booking_limits": []}]},
        )

    out = await _client(handler).list_event_types()
    assert len(out) == 1
    assert out[0].id == et_id and out[0].slug == "intro" and out[0].title == "Intro" and out[0].duration_minutes == 30


@pytest.mark.asyncio
async def test_get_event_type_404_raises_not_found() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "nope"})

    with pytest.raises(NotFoundError):
        await _client(handler).get_event_type(uuid4())


@pytest.mark.asyncio
async def test_get_slots_passthrough() -> None:
    et_id = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/api/v1/slots"
        assert request.url.params["event_type_id"] == str(et_id)
        assert request.url.params["time_zone"] == "Europe/Berlin"
        return httpx.Response(200, json={"event_type_id": str(et_id), "time_zone": "Europe/Berlin",
                                         "slots": {"2026-10-01": ["2026-10-01T09:00:00Z"]}})

    out = await _client(handler).get_slots(
        et_id, dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC), "Europe/Berlin"
    )
    assert out.time_zone == "Europe/Berlin"
    assert out.slots == {"2026-10-01": ["2026-10-01T09:00:00Z"]}


@pytest.mark.asyncio
async def test_create_booking_success_and_sets_actor_header() -> None:
    et_id, client_id, booking_id = uuid4(), uuid4(), uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/api/v1/bookings"
        assert request.headers["actor_source"] == "booker"
        body = json.loads(request.content)
        assert body == {"event_type_id": str(et_id), "client_user_id": str(client_id),
                        "start_time": "2026-10-01T09:00:00+00:00", "attendee_time_zone": "Europe/Berlin"}
        return httpx.Response(201, json={"id": str(booking_id), "event_type_id": str(et_id),
                                         "host_user_id": str(uuid4()), "client_user_id": str(client_id),
                                         "start_time": "2026-10-01T09:00:00Z", "end_time": "2026-10-01T09:30:00Z",
                                         "status": "confirmed", "attendee_time_zone": "Europe/Berlin",
                                         "created_at": "2026-09-01T00:00:00Z"})

    out = await _client(handler).create_booking(
        et_id, client_id, dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC), "Europe/Berlin"
    )
    assert out.id == booking_id and out.status == "confirmed"


@pytest.mark.asyncio
async def test_create_booking_409_raises_slot_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "slot taken"})

    with pytest.raises(SlotUnavailableError):
        await _client(handler).create_booking(uuid4(), uuid4(), dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC), "UTC")


@pytest.mark.asyncio
async def test_create_booking_5xx_raises_upstream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, text="boom")

    with pytest.raises(UpstreamError):
        await _client(handler).create_booking(uuid4(), uuid4(), dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC), "UTC")
```

- [ ] **Step 4: Run — verify FAIL** (`ModuleNotFoundError: event_booker.adapters.scheduling_client`).

- [ ] **Step 5: `event_booker/adapters/scheduling_client.py`**:

```python
from datetime import datetime
from uuid import UUID

import httpx

from event_booker.dto import BookingResult, EventTypeDTO, SlotsResult
from event_booker.errors import NotFoundError, SlotUnavailableError, UpstreamError


def _dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class SchedulingClient:
    def __init__(self, base_url: str, api_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport, timeout=10.0, headers={"authorization": f"Bearer {self._api_key}"}
        )

    async def list_event_types(self) -> list[EventTypeDTO]:
        async with self._http() as client:
            resp = await client.get(f"{self._base_url}/api/v1/event-types")
        self._raise_for_status(resp)
        return [self._to_event_type(item) for item in resp.json()["items"]]

    async def get_event_type(self, event_type_id: UUID) -> EventTypeDTO:
        async with self._http() as client:
            resp = await client.get(f"{self._base_url}/api/v1/event-types/{event_type_id}")
        if resp.status_code == httpx.codes.NOT_FOUND:
            raise NotFoundError("event type not found")
        self._raise_for_status(resp)
        return self._to_event_type(resp.json())

    async def get_slots(
        self, event_type_id: UUID, start: datetime, end: datetime, time_zone: str
    ) -> SlotsResult:
        params = {
            "event_type_id": str(event_type_id),
            "start": start.isoformat(),
            "end": end.isoformat(),
            "time_zone": time_zone,
        }
        async with self._http() as client:
            resp = await client.get(f"{self._base_url}/api/v1/slots", params=params)
        self._raise_for_status(resp)
        data = resp.json()
        return SlotsResult(event_type_id=event_type_id, time_zone=data["time_zone"], slots=data["slots"])

    async def create_booking(
        self, event_type_id: UUID, client_user_id: UUID, start_time: datetime, attendee_time_zone: str
    ) -> BookingResult:
        body = {
            "event_type_id": str(event_type_id),
            "client_user_id": str(client_user_id),
            "start_time": start_time.isoformat(),
            "attendee_time_zone": attendee_time_zone,
        }
        async with self._http() as client:
            resp = await client.post(
                f"{self._base_url}/api/v1/bookings", json=body, headers={"actor_source": "booker"}
            )
        if resp.status_code == httpx.codes.CONFLICT:
            raise SlotUnavailableError("slot no longer available")
        if resp.status_code == httpx.codes.NOT_FOUND:
            raise NotFoundError("event type not found")
        self._raise_for_status(resp)
        data = resp.json()
        return BookingResult(
            id=UUID(data["id"]),
            start_time=_dt(data["start_time"]),
            end_time=_dt(data["end_time"]),
            status=data["status"],
        )

    @staticmethod
    def _to_event_type(item: dict) -> EventTypeDTO:
        return EventTypeDTO(
            id=UUID(item["id"]),
            slug=item["slug"],
            title=item["title"],
            duration_minutes=item["duration_minutes"],
        )

    @staticmethod
    def _raise_for_status(resp: httpx.Response) -> None:
        if resp.is_success:
            return
        raise UpstreamError(f"event-scheduling returned {resp.status_code}")
```

- [ ] **Step 6: Run — verify PASS.** `cd event-booker && uv run pytest tests/test_scheduling_client.py -v` — 6 passed. Then `uv run ruff check . && uv run ruff format --check .`.

- [ ] **Step 7: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker/event_booker/dto.py event-booker/event_booker/interfaces/clients.py \
        event-booker/event_booker/adapters/scheduling_client.py event-booker/tests/test_scheduling_client.py
git commit -m "feat(booker): DTOs + interfaces + SchedulingClient (slice 4b.1)"
```

---

## Task 3: UsersClient (guest → client_user_id)

**Files:**
- Create: `event_booker/adapters/users_client.py`
- Test: `event-booker/tests/test_users_client.py`

**Interfaces:**
- Consumes: `IUsersClient` Protocol (Task 2), `errors.py` (`ConflictError`, `UpstreamError`).
- Produces: `UsersClient(base_url, token, *, transport=None)` with `get_client_by_email(email) -> UUID | None` (404→None) and `create_client(email, name, time_zone) -> UUID` (409→ConflictError, 5xx→UpstreamError). Role hard-fixed to `"client"`.

- [ ] **Step 1: Write the failing test** `event-booker/tests/test_users_client.py`:

```python
from uuid import uuid4

import httpx
import pytest

from event_booker.adapters.users_client import UsersClient
from event_booker.errors import ConflictError, UpstreamError

BASE = "http://users.test"
TOKEN = "users-token"


def _client(handler) -> UsersClient:
    return UsersClient(BASE, TOKEN, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_get_client_by_email_found() -> None:
    uid = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == f"Bearer {TOKEN}"
        assert request.url.path == "/api/users/by-identity"
        assert request.url.params["email"] == "a@b.io"
        assert request.url.params["role"] == "client"
        return httpx.Response(200, json={"id": str(uid), "email": "a@b.io", "name": "A", "role": "client",
                                         "time_zone": "UTC"})

    assert await _client(handler).get_client_by_email("a@b.io") == uid


@pytest.mark.asyncio
async def test_get_client_by_email_404_returns_none() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"detail": "not found"})

    assert await _client(handler).get_client_by_email("x@y.io") is None


@pytest.mark.asyncio
async def test_create_client_success() -> None:
    uid = uuid4()

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST" and request.url.path == "/api/users"
        import json
        body = json.loads(request.content)
        assert body == {"email": "a@b.io", "name": "A", "role": "client", "time_zone": "Europe/Berlin"}
        return httpx.Response(201, json={"id": str(uid), "email": "a@b.io", "name": "A", "role": "client",
                                         "time_zone": "Europe/Berlin"})

    assert await _client(handler).create_client("a@b.io", "A", "Europe/Berlin") == uid


@pytest.mark.asyncio
async def test_create_client_409_raises_conflict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "dup"})

    with pytest.raises(ConflictError):
        await _client(handler).create_client("a@b.io", "A", "UTC")


@pytest.mark.asyncio
async def test_create_client_5xx_raises_upstream() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(UpstreamError):
        await _client(handler).create_client("a@b.io", "A", "UTC")
```

- [ ] **Step 2: Run — verify FAIL** (`ModuleNotFoundError`).

- [ ] **Step 3: `event_booker/adapters/users_client.py`**:

```python
from uuid import UUID

import httpx

from event_booker.errors import ConflictError, UpstreamError

_CLIENT_ROLE = "client"


class UsersClient:
    def __init__(self, base_url: str, token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._transport = transport

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport, timeout=10.0, headers={"authorization": f"Bearer {self._token}"}
        )

    async def get_client_by_email(self, email: str) -> UUID | None:
        async with self._http() as client:
            resp = await client.get(
                f"{self._base_url}/api/users/by-identity", params={"email": email, "role": _CLIENT_ROLE}
            )
        if resp.status_code == httpx.codes.NOT_FOUND:
            return None
        if not resp.is_success:
            raise UpstreamError(f"event-users returned {resp.status_code}")
        return UUID(resp.json()["id"])

    async def create_client(self, email: str, name: str, time_zone: str) -> UUID:
        body = {"email": email, "name": name, "role": _CLIENT_ROLE, "time_zone": time_zone}
        async with self._http() as client:
            resp = await client.post(f"{self._base_url}/api/users", json=body)
        if resp.status_code == httpx.codes.CONFLICT:
            raise ConflictError("client already exists")
        if resp.status_code != httpx.codes.CREATED:
            raise UpstreamError(f"event-users returned {resp.status_code}")
        return UUID(resp.json()["id"])
```

- [ ] **Step 4: Run — verify PASS.** `cd event-booker && uv run pytest tests/test_users_client.py -v` — 5 passed; ruff clean.

- [ ] **Step 5: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker/event_booker/adapters/users_client.py event-booker/tests/test_users_client.py
git commit -m "feat(booker): UsersClient guest->client resolution (slice 4b.1)"
```

---

## Task 4: GuestBookingService

**Files:**
- Create: `event_booker/services/guest_booking.py`
- Test: `event-booker/tests/test_guest_booking.py`

**Interfaces:**
- Consumes: `ISchedulingClient`, `IUsersClient` (Task 2), `dto.BookingConfirmation`/`BookingResult`/`EventTypeDTO`, `errors.SlotUnavailableError`.
- Produces: `GuestBookingService(scheduling: ISchedulingClient, users: IUsersClient)` with `book(event_type_id: UUID, name: str, email: str, start_time: datetime, time_zone: str) -> BookingConfirmation`.

- [ ] **Step 1: Write the failing test** `event-booker/tests/test_guest_booking.py`:

```python
import datetime as dt
from uuid import UUID, uuid4

import pytest

from event_booker.dto import BookingResult, EventTypeDTO
from event_booker.errors import SlotUnavailableError
from event_booker.services.guest_booking import GuestBookingService

ET_ID = uuid4()
START = dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC)
END = dt.datetime(2026, 10, 1, 9, 30, tzinfo=dt.UTC)


class _Scheduling:
    def __init__(self, *, conflict: bool = False) -> None:
        self.created_with: tuple | None = None
        self._conflict = conflict

    async def get_event_type(self, event_type_id):
        return EventTypeDTO(id=event_type_id, slug="intro", title="Intro call", duration_minutes=30)

    async def create_booking(self, event_type_id, client_user_id, start_time, attendee_time_zone):
        if self._conflict:
            raise SlotUnavailableError("slot no longer available")
        self.created_with = (event_type_id, client_user_id, start_time, attendee_time_zone)
        return BookingResult(id=uuid4(), start_time=START, end_time=END, status="confirmed")

    async def list_event_types(self): ...
    async def get_slots(self, *a, **k): ...


class _Users:
    def __init__(self, existing: UUID | None) -> None:
        self._existing = existing
        self.created = False

    async def get_client_by_email(self, email):
        return self._existing

    async def create_client(self, email, name, time_zone):
        self.created = True
        return uuid4()


@pytest.mark.asyncio
async def test_books_for_existing_client_without_create() -> None:
    existing = uuid4()
    sched, users = _Scheduling(), _Users(existing=existing)
    conf = await GuestBookingService(sched, users).book(ET_ID, "A", "a@b.io", START, "Europe/Berlin")
    assert users.created is False
    assert sched.created_with[1] == existing
    assert conf.event_type_title == "Intro call"
    assert conf.time_zone == "Europe/Berlin" and conf.status == "confirmed"


@pytest.mark.asyncio
async def test_creates_client_when_absent_then_books() -> None:
    sched, users = _Scheduling(), _Users(existing=None)
    conf = await GuestBookingService(sched, users).book(ET_ID, "A", "a@b.io", START, "UTC")
    assert users.created is True
    assert conf.booking_id is not None


@pytest.mark.asyncio
async def test_slot_conflict_propagates() -> None:
    sched, users = _Scheduling(conflict=True), _Users(existing=uuid4())
    with pytest.raises(SlotUnavailableError):
        await GuestBookingService(sched, users).book(ET_ID, "A", "a@b.io", START, "UTC")
```

- [ ] **Step 2: Run — verify FAIL.**

- [ ] **Step 3: `event_booker/services/guest_booking.py`**:

```python
from datetime import datetime
from uuid import UUID

from event_booker.dto import BookingConfirmation
from event_booker.interfaces.clients import ISchedulingClient, IUsersClient


class GuestBookingService:
    def __init__(self, scheduling: ISchedulingClient, users: IUsersClient) -> None:
        self._scheduling = scheduling
        self._users = users

    async def book(
        self, event_type_id: UUID, name: str, email: str, start_time: datetime, time_zone: str
    ) -> BookingConfirmation:
        client_user_id = await self._resolve_client(email, name, time_zone)
        booking = await self._scheduling.create_booking(event_type_id, client_user_id, start_time, time_zone)
        event_type = await self._scheduling.get_event_type(event_type_id)
        return BookingConfirmation(
            booking_id=booking.id,
            event_type_title=event_type.title,
            start_time=booking.start_time,
            end_time=booking.end_time,
            status=booking.status,
            time_zone=time_zone,
        )

    async def _resolve_client(self, email: str, name: str, time_zone: str) -> UUID:
        existing = await self._users.get_client_by_email(email)
        if existing is not None:
            return existing
        return await self._users.create_client(email, name, time_zone)
```

> Note on the create-409 race (spec §3): `create_client` raises `ConflictError` on 409. In the common path `get_client_by_email` already returns the existing id so `create_client` is not called. The residual race (two concurrent first-time bookings for the same new email) is rare; a 409 there surfaces as HTTP 409 and the guest retries (the second attempt finds the now-existing client). A refetch-on-409 loop is deferred (spec §7 anti-abuse hardening) to keep this service minimal — do NOT add it here.

- [ ] **Step 4: Run — verify PASS.** `cd event-booker && uv run pytest tests/test_guest_booking.py -v` — 3 passed; ruff clean.

- [ ] **Step 5: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker/event_booker/services/guest_booking.py event-booker/tests/test_guest_booking.py
git commit -m "feat(booker): GuestBookingService (resolve-or-create client + book) (slice 4b.1)"
```

---

## Task 5: public schemas + routers + DI wiring

**Files:**
- Create: `event_booker/schemas/public.py`, `event_booker/routers/public.py`
- Modify: `event_booker/ioc.py` (add client + service providers), `event_booker/main.py` (include `public_router`)
- Test: `event-booker/tests/test_public_api.py`

**Interfaces:**
- Consumes: `GuestBookingService`, `ISchedulingClient`, `IUsersClient`, `SchedulingClient`, `UsersClient`, `Settings`, all DTOs.
- Produces: 4 public routes under `/api/public` (see spec §2); Dishka providers binding `ISchedulingClient`→`SchedulingClient`, `IUsersClient`→`UsersClient` (APP scope, from Settings), and `GuestBookingService`.

- [ ] **Step 1: `event_booker/schemas/public.py`**:

```python
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, EmailStr

from event_booker.dto import BookingConfirmation, EventTypeDTO, SlotsResult


class EventTypeModel(BaseModel):
    id: UUID
    slug: str
    title: str
    duration_minutes: int

    @classmethod
    def from_dto(cls, d: EventTypeDTO) -> EventTypeModel:
        return cls(id=d.id, slug=d.slug, title=d.title, duration_minutes=d.duration_minutes)


class EventTypeListResponse(BaseModel):
    items: list[EventTypeModel]


class SlotsPublicResponse(BaseModel):
    event_type_id: UUID
    time_zone: str
    slots: dict[str, list[str]]

    @classmethod
    def from_result(cls, r: SlotsResult) -> SlotsPublicResponse:
        return cls(event_type_id=r.event_type_id, time_zone=r.time_zone, slots=r.slots)


class CreateBookingPublicRequest(BaseModel):
    event_type_id: UUID
    name: str
    email: EmailStr
    start_time: datetime
    time_zone: str


class BookingConfirmationResponse(BaseModel):
    booking_id: UUID
    event_type_title: str
    start_time: datetime
    end_time: datetime
    status: str
    time_zone: str

    @classmethod
    def from_confirmation(cls, c: BookingConfirmation) -> BookingConfirmationResponse:
        return cls(
            booking_id=c.booking_id,
            event_type_title=c.event_type_title,
            start_time=c.start_time,
            end_time=c.end_time,
            status=c.status,
            time_zone=c.time_zone,
        )
```

- [ ] **Step 2: `event_booker/routers/public.py`** (NO auth dependency):

```python
from datetime import datetime
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter

from event_booker.interfaces.clients import ISchedulingClient
from event_booker.schemas.public import (
    BookingConfirmationResponse,
    CreateBookingPublicRequest,
    EventTypeListResponse,
    EventTypeModel,
    SlotsPublicResponse,
)
from event_booker.services.guest_booking import GuestBookingService


public_router = APIRouter(prefix="/api/public", tags=["public"], route_class=DishkaRoute)


@public_router.get("/event-types", response_model=EventTypeListResponse)
async def list_event_types(scheduling: FromDishka[ISchedulingClient]) -> EventTypeListResponse:
    items = await scheduling.list_event_types()
    return EventTypeListResponse(items=[EventTypeModel.from_dto(d) for d in items])


@public_router.get("/event-types/{event_type_id}", response_model=EventTypeModel)
async def get_event_type(event_type_id: UUID, scheduling: FromDishka[ISchedulingClient]) -> EventTypeModel:
    return EventTypeModel.from_dto(await scheduling.get_event_type(event_type_id))


@public_router.get("/slots", response_model=SlotsPublicResponse)
async def get_slots(
    event_type_id: UUID,
    start: datetime,
    end: datetime,
    time_zone: str,
    scheduling: FromDishka[ISchedulingClient],
) -> SlotsPublicResponse:
    return SlotsPublicResponse.from_result(await scheduling.get_slots(event_type_id, start, end, time_zone))


@public_router.post("/bookings", response_model=BookingConfirmationResponse, status_code=201)
async def create_booking(
    body: CreateBookingPublicRequest, service: FromDishka[GuestBookingService]
) -> BookingConfirmationResponse:
    confirmation = await service.book(body.event_type_id, body.name, body.email, body.start_time, body.time_zone)
    return BookingConfirmationResponse.from_confirmation(confirmation)
```

- [ ] **Step 3: Extend `event_booker/ioc.py`** — add providers (append imports + methods to `AppProvider`):

```python
from event_booker.adapters.scheduling_client import SchedulingClient
from event_booker.adapters.users_client import UsersClient
from event_booker.interfaces.clients import ISchedulingClient, IUsersClient
from event_booker.services.guest_booking import GuestBookingService
```
```python
    @provide(scope=Scope.APP)
    def provide_scheduling_client(self, settings: Settings) -> ISchedulingClient:
        return SchedulingClient(settings.event_scheduling_url, settings.scheduling_api_key)

    @provide(scope=Scope.APP)
    def provide_users_client(self, settings: Settings) -> IUsersClient:
        return UsersClient(settings.event_users_url, settings.event_users_token)

    @provide(scope=Scope.APP)
    def provide_guest_booking_service(
        self, scheduling: ISchedulingClient, users: IUsersClient
    ) -> GuestBookingService:
        return GuestBookingService(scheduling, users)
```

- [ ] **Step 4: Wire the router** in `event_booker/main.py` — add `from event_booker.routers.public import public_router` and `app.include_router(public_router)` (after `app.include_router(root_router)`).

- [ ] **Step 5: Write the failing test** `event-booker/tests/test_public_api.py` (build the app with Dishka providers overridden by fakes — mirrors event-scheduling's `client_fake_users` pattern):

```python
import datetime as dt
from collections.abc import Generator
from uuid import UUID, uuid4

import pytest

from event_booker.dto import BookingConfirmation, BookingResult, EventTypeDTO, SlotsResult
from event_booker.errors import NotFoundError, SlotUnavailableError, UpstreamError

ET_ID = uuid4()


class _FakeScheduling:
    def __init__(self, *, mode: str = "ok") -> None:
        self._mode = mode

    async def list_event_types(self):
        return [EventTypeDTO(id=ET_ID, slug="intro", title="Intro", duration_minutes=30)]

    async def get_event_type(self, event_type_id):
        if self._mode == "missing":
            raise NotFoundError("event type not found")
        return EventTypeDTO(id=event_type_id, slug="intro", title="Intro", duration_minutes=30)

    async def get_slots(self, event_type_id, start, end, time_zone):
        if self._mode == "upstream":
            raise UpstreamError("event-scheduling returned 503")
        return SlotsResult(event_type_id=event_type_id, time_zone=time_zone,
                           slots={"2026-10-01": ["2026-10-01T09:00:00Z"]})

    async def create_booking(self, event_type_id, client_user_id, start_time, attendee_time_zone):
        if self._mode == "conflict":
            raise SlotUnavailableError("slot no longer available")
        return BookingResult(id=uuid4(), start_time=start_time,
                             end_time=start_time + dt.timedelta(minutes=30), status="confirmed")


class _FakeUsers:
    async def get_client_by_email(self, email):
        return uuid4()

    async def create_client(self, email, name, time_zone):
        return uuid4()


def _make_client(mode: str = "ok"):
    from dishka import Provider, Scope, make_async_container, provide
    from dishka.integrations.fastapi import FastapiProvider, setup_dishka
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from starlette.testclient import TestClient

    from event_booker.errors import ConflictError, NotFoundError, SlotUnavailableError, UpstreamError, ValidationError
    from event_booker.interfaces.clients import ISchedulingClient, IUsersClient
    from event_booker.ioc import AppProvider
    from event_booker.routers.public import public_router
    from event_booker.routes import root_router
    from event_booker.services.guest_booking import GuestBookingService

    class FakeProvider(Provider):
        @provide(scope=Scope.APP, override=True)
        def sched(self) -> ISchedulingClient:
            return _FakeScheduling(mode=mode)

        @provide(scope=Scope.APP, override=True)
        def users(self) -> IUsersClient:
            return _FakeUsers()

        @provide(scope=Scope.APP, override=True)
        def svc(self, scheduling: ISchedulingClient, users: IUsersClient) -> GuestBookingService:
            return GuestBookingService(scheduling, users)

    container = make_async_container(AppProvider(), FakeProvider(), FastapiProvider())
    app = FastAPI()
    setup_dishka(container=container, app=app)
    app.include_router(root_router)
    app.include_router(public_router)

    status = {ValidationError: 422, NotFoundError: 404, ConflictError: 409, SlotUnavailableError: 409, UpstreamError: 502}

    async def handler(_: Request, exc: Exception) -> JSONResponse:
        code = next((c for t, c in status.items() if isinstance(exc, t)), 500)
        return JSONResponse(status_code=code, content={"detail": str(exc)})

    for err in (ValidationError, NotFoundError, ConflictError, SlotUnavailableError, UpstreamError):
        app.add_exception_handler(err, handler)
    return TestClient(app)


@pytest.fixture
def booker() -> Generator:
    with _make_client() as c:
        yield c


def test_list_event_types(booker) -> None:
    r = booker.get("/api/public/event-types")
    assert r.status_code == 200
    assert r.json()["items"][0]["title"] == "Intro"


def test_get_slots(booker) -> None:
    r = booker.get("/api/public/slots", params={"event_type_id": str(ET_ID),
                   "start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z", "time_zone": "UTC"})
    assert r.status_code == 200
    assert r.json()["slots"] == {"2026-10-01": ["2026-10-01T09:00:00Z"]}


def test_create_booking_confirmation_hides_internal_ids(booker) -> None:
    r = booker.post("/api/public/bookings", json={"event_type_id": str(ET_ID), "name": "A",
                    "email": "a@b.io", "start_time": "2026-10-01T09:00:00Z", "time_zone": "Europe/Berlin"})
    assert r.status_code == 201
    payload = r.json()
    assert payload["event_type_title"] == "Intro" and payload["status"] == "confirmed"
    assert "client_user_id" not in payload and "host_user_id" not in payload


def test_create_booking_slot_conflict_returns_409() -> None:
    with _make_client(mode="conflict") as c:
        r = c.post("/api/public/bookings", json={"event_type_id": str(ET_ID), "name": "A",
                   "email": "a@b.io", "start_time": "2026-10-01T09:00:00Z", "time_zone": "UTC"})
    assert r.status_code == 409


def test_create_booking_bad_email_returns_422(booker) -> None:
    r = booker.post("/api/public/bookings", json={"event_type_id": str(ET_ID), "name": "A",
                    "email": "not-an-email", "start_time": "2026-10-01T09:00:00Z", "time_zone": "UTC"})
    assert r.status_code == 422


def test_get_slots_upstream_error_returns_502() -> None:
    with _make_client(mode="upstream") as c:
        r = c.get("/api/public/slots", params={"event_type_id": str(ET_ID),
                  "start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z", "time_zone": "UTC"})
    assert r.status_code == 502
```

- [ ] **Step 6: Run — verify PASS.** `cd event-booker && uv run pytest tests/test_public_api.py -v` — 6 passed. Then the FULL suite `uv run pytest -q` (health + all clients + service + api) + `uv run ruff check . && uv run ruff format --check .` — all green.

- [ ] **Step 7: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-booker/event_booker/schemas/public.py event-booker/event_booker/routers/public.py \
        event-booker/event_booker/ioc.py event-booker/event_booker/main.py event-booker/tests/test_public_api.py
git commit -m "feat(booker): public API (event-types, slots, guest bookings) + DI wiring (slice 4b.1)"
```

---

## Task 6: docker-compose + docs + final gate

**Files:**
- Modify: `docker-compose.services.yml`, root `CLAUDE.md` (port table), `docs/architecture/ONBOARDING.md`, `docs/architecture/ARCHITECTURE.md`
- Create: `event-booker/CLAUDE.md`

- [ ] **Step 1: Full suite + lint gate.** `cd /Users/alexandrlelikov/PycharmProjects/events/event-booker && uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green (health 3 + scheduling 6 + users 5 + guest 3 + public 6 = 23 tests).

- [ ] **Step 2: `docker-compose.services.yml`** — add an `event-booker` service (mirror the event-scheduling block's env/ports/healthcheck style). Read the existing `event-scheduling` block first to match indentation:

```yaml
  event-booker:
    build:
      context: ./event-booker
    environment:
      DEBUG: "false"
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      OTEL_SERVICE_NAME: event-booker
      OTEL_SDK_DISABLED: ${OTEL_SDK_DISABLED:-true}
      OTEL_EXPORTER_OTLP_ENDPOINT: ${OTEL_EXPORTER_OTLP_ENDPOINT:-http://otel-collector:4317}
      OTEL_EXPORTER_OTLP_PROTOCOL: grpc
      OTEL_TRACES_SAMPLER: ${OTEL_TRACES_SAMPLER:-parentbased_always_on}
      EVENT_SCHEDULING_URL: http://event-scheduling:8888
      # Must match event-scheduling's own SCHEDULING_API_KEY (require_api_key).
      SCHEDULING_API_KEY: ${SCHEDULING_API_KEY:-dev-scheduling-api-key-3f9c2e1a7b64d508}
      EVENT_USERS_URL: http://event-users:8888
      # Must match event-users' API_BEARER_TOKEN (require_admin) — same shared dev token as the other services.
      EVENT_USERS_TOKEN: ${USERS_API_BEARER_TOKEN:-dev-users-bearer-2a7d9e4f8c1b6350}
      BOOKER_CORS_ORIGINS: ${BOOKER_CORS_ORIGINS:-}
    ports:
      - "${BOOKER_PORT:-8005}:8888"
    depends_on:
      event-scheduling:
        condition: service_started
      event-users:
        condition: service_started
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8888/health', timeout=5)\""]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 20s
    restart: unless-stopped
```
> Verify the two dev keys byte-match the `event-scheduling` service block already in this file (`SCHEDULING_API_KEY` default and `USERS_API_BEARER_TOKEN` default) so the BFF's Bearer is accepted by both upstreams.

- [ ] **Step 3: `event-booker/CLAUDE.md`** — document: the BFF role (public guest booking, holds scheduling+users keys server-side), the 4 `/api/public/*` endpoints, the guest→client resolution (`by-identity`→`create`, role fixed `client`), error mapping, that it's stateless (no DB), the standard commands (`uv sync`, `uv run pytest`, `ruff check`), and the deferred items (frontend 4b.2, anti-abuse hardening, Helm/CI). Follow the structure/tone of `event-scheduling/CLAUDE.md`.

- [ ] **Step 4: Root docs.**
  - Root `CLAUDE.md`: add `event-booker` to the monorepo services table (row: "Python, FastAPI — Public booking BFF: guest→client resolution + booking; holds scheduling/users keys") and to the host-ports table (`8005 | event-booker (public booking BFF)`).
  - `docs/architecture/ONBOARDING.md`: add a short "event-booker (public booking BFF)" subsection — role, the 4 public endpoints, upstream calls, that the frontend (4b.2) is next.
  - `docs/architecture/ARCHITECTURE.md`: note event-booker as the public trust boundary in front of event-scheduling/event-users (data-flow line: `public browser → event-booker (holds keys) → event-scheduling + event-users`).

  Every doc claim must be TRUE against the code just built — read the relevant source if unsure.

- [ ] **Step 5: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add docker-compose.services.yml CLAUDE.md docs/architecture/ONBOARDING.md docs/architecture/ARCHITECTURE.md \
        event-booker/CLAUDE.md
git commit -m "docs(booker): compose wiring + docs for event-booker BFF (slice 4b.1)"
```

---

## Self-Review (completed during plan authoring)

**1. Spec coverage:** §0/§1 architecture (stateless BFF, two clients, service) → Tasks 1–5. §2 four public endpoints → Task 5 (list/get event-types, slots, POST bookings) + Task 2/3 clients. §3 GuestBookingService (resolve-or-create → book, title in confirmation) → Task 4. §4 error handling (409 slot, 404 type, 422 input, 502 upstream, no leaks) → error types (Task 1) + client raises (Tasks 2/3) + handler (Task 1) + public-projection tests (Task 5). §5 security (keys server-side, role fixed `client`, no id leaks, CORS flag) → config (Task 1), UsersClient `_CLIENT_ROLE` (Task 3), confirmation schema hides ids (Task 5, asserted). §6 tests → distributed across tasks (MockTransport clients, fakes for service/api). §7 deferred (frontend, anti-abuse, magic-link, Helm/CI) → noted, not built; create-409 refetch explicitly NOT added (Task 4 note). §8 DoR + Docker/compose/docs → Tasks 1 (Docker) + 6 (compose/docs).

**2. Placeholders:** All code complete. Boilerplate (telemetry/metrics/logger) is "copy verbatim from event-scheduling, rename imports" (Task 1 Step 1) with an exact grep verification — a copy instruction, not a placeholder. Docs (Task 6) are "read file, add focused section" against real files.

**3. Type consistency:** `EventTypeDTO(id, slug, title, duration_minutes)` — same fields in SchedulingClient `_to_event_type` (T2), EventTypeModel.from_dto (T5). `BookingResult(id, start_time, end_time, status)` — produced by SchedulingClient.create_booking (T2), consumed by GuestBookingService (T4). `BookingConfirmation(booking_id, event_type_title, start_time, end_time, status, time_zone)` — produced by service (T4), consumed by BookingConfirmationResponse.from_confirmation (T5). `ISchedulingClient`/`IUsersClient` method signatures (T2) match SchedulingClient (T2), UsersClient (T3), the fakes (T4/T5), and ioc providers (T5). `get_client_by_email(email)->UUID|None`, `create_client(email,name,time_zone)->UUID` consistent T2/T3/T4. Error types + `_STATUS` map identical in main.py (T1) and the public-api test harness (T5).
