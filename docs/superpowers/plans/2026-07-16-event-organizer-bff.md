# event-organizer cabinet BFF (срез 6.1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new `event-organizer` BFF where an organizer logs in with email+password and manages their OWN schedule, views their bookings, and edits their profile (name/timezone) + password — every id taken from the JWT session, never from the request.

**Architecture:** Password auth (bcrypt + JWT HS256, own `event_organizer` DB with `organizer_credential`) mirroring event-admin's auth stack. Session-gated `/api/me/*` endpoints proxy to event-scheduling (schedule, bookings) and event-users (profile), holding those services' keys server-side and injecting the authenticated organizer's `user_id`. Admin-seeded provisioning. Ownership is by construction — no request-supplied ids, so the slice-5 IDOR class is impossible here.

**Tech Stack:** Python 3.14, uv, FastAPI, Dishka DI, SqlExecutor (raw `:param` SQL), alembic, httpx, bcrypt, PyJWT, pydantic-settings, structlog, prometheus-client, OpenTelemetry (gated), pytest (Docker Postgres + httpx.MockTransport).

## Global Constraints

- New service `event-organizer`, tracked by the ROOT repo `/Users/alexandrlelikov/PycharmProjects/events`. Commit in ROOT on branch `feat/event-organizer-bff` (create off `main` before Task 1).
- Additive: do NOT modify event-scheduling, event-users, event-admin, or any other service. This slice creates one new service + docker-compose/docs edits.
- Code style: **NO `elif`**; **avoid `else`** (early returns/guards); Ruff line length 120; `target-version = py314`; frozen-dataclass DTOs; Pydantic only in `schemas/`; Protocol interfaces in `interfaces/`; httpx clients in `adapters/`; raw SQL via `SqlExecutor` (`:param` binds).
- **Ownership by construction:** every `/api/me/*` endpoint uses `me.user_id` (from the decoded JWT) as the resource id. NO endpoint accepts an owner/host/user id in its path or body. Profile PUT forwards ONLY `name` + `time_zone` to event-users (never `email`/`role`).
- Secrets (`SCHEDULING_API_KEY`, `EVENT_USERS_TOKEN`, `JWT_SECRET_KEY`, passwords, bcrypt hashes) live server-side only — never in responses or logs.
- Auth reuse (copy + adapt from event-admin): `PasswordService` (bcrypt), the JWT create/decode pattern (HS256, `exp`, optional `aud`/`iss`). NO TOTP, NO login-guard in 6.1 (deferred).
- Upstream contracts (exact): event-scheduling (Bearer `SCHEDULING_API_KEY`): `GET/PUT /api/v1/schedules/{owner_user_id}` (+ `PUT …/travel`), `GET /api/v1/bookings?host_user_id=`. event-users (Bearer `EVENT_USERS_TOKEN`): `GET /api/users/id/{user_id}` → `{id,email,name,role,time_zone}`; `PATCH /api/users/id/{user_id}` (body `{email?,name?,role?,time_zone?}`); `GET /api/users/by-identity?email=&role=organizer` → 200|404.
- Error map: `Unauthorized`→401, `Forbidden`→403, `NotFoundError`→404, `ConflictError`→409, `ValidationError`→422, `UpstreamError`→502.
- Host port **8006** (internal 8888). Own DB `event_organizer` on the shared postgres. DB tests: Docker Postgres — `docker run -d --rm --name org-testpg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=event_organizer -p 5601:5432 postgres:16`, then `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5601/event_organizer' uv run pytest`.
- Dev key defaults (match compose): `scheduling_api_key="dev-scheduling-api-key-3f9c2e1a7b64d508"`, `event_users_token="dev-users-bearer-2a7d9e4f8c1b6350"`, `event_scheduling_url="http://event-scheduling:8888"`, `event_users_url="http://event-users:8888"`, `organizer_admin_key="dev-organizer-admin-key"`, `jwt_secret_key="dev-organizer-jwt-secret"`.

---

## File Structure

New package `event-organizer/` (root-tracked):
- Config/deploy: `pyproject.toml`, `Dockerfile`, `entrypoint.sh`, `uvicorn_config.json`, `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_organizer_credential.py`
- `event_organizer/__init__.py`, `config.py`, `errors.py`, `routes.py`, `main.py`, `ioc.py`, `logger.py`, `metrics.py`, `telemetry.py`, `adapters/sql.py`
- `event_organizer/auth/`: `password.py`, `jwt.py`, `identity.py`
- `event_organizer/credentials/`: `dto.py`, `interfaces.py`, `adapter.py`
- `event_organizer/adapters/`: `scheduling_client.py`, `users_client.py`
- `event_organizer/services/`: `login_service.py`, `provisioning_service.py`, `profile_service.py`, `password_change_service.py`
- `event_organizer/schemas/`: `auth.py`, `admin.py`, `me.py`
- `event_organizer/routers/`: `auth.py`, `admin.py`, `me.py`
- Tests under `event-organizer/tests/`
- Modify: `docker-compose.services.yml`, root `CLAUDE.md`, `docs/architecture/ONBOARDING.md`, `docs/architecture/ARCHITECTURE.md`; create `event-organizer/CLAUDE.md`

Copy verbatim from `event-scheduling/` (service-agnostic): `telemetry.py`, `metrics.py`, `logger.py`, `uvicorn_config.json`, `adapters/sql.py` (the `SqlExecutor`), `alembic.ini` + `alembic/env.py` (adjust the DSN/model import). Copy from `event-admin/event_admin/services/password.py` → `event_organizer/auth/password.py` (rename import).

---

## Task 1: Scaffold — bootable service + credential table

**Files:**
- Create: `event-organizer/pyproject.toml`, `Dockerfile`, `entrypoint.sh`, `uvicorn_config.json`, `.dockerignore`, `.gitignore`, `alembic.ini`, `alembic/env.py`, `alembic/versions/0001_organizer_credential.py`
- Create: `event_organizer/__init__.py`, `config.py`, `errors.py`, `routes.py`, `ioc.py`, `main.py`, `logger.py`, `metrics.py`, `telemetry.py`, `adapters/__init__.py`, `adapters/sql.py`, `interfaces/__init__.py`, `interfaces/sql.py`
- Test: `event-organizer/tests/conftest.py`, `tests/test_health.py`, `tests/test_credential_schema.py`

**Interfaces:**
- Produces: `Settings` (fields below); error types; `app`; `SqlExecutor`/`ISqlExecutor`; the `organizer_credential` table.

- [ ] **Step 1: Copy boilerplate** from `event-scheduling/`: `event_scheduling/telemetry.py`→`event_organizer/telemetry.py` (imports are stdlib/otel — no rename needed), `metrics.py`→`event_organizer/metrics.py` (change the docstring service name), `logger.py`→`event_organizer/logger.py` (change `from event_scheduling.telemetry` → `from event_organizer.telemetry`), `uvicorn_config.json`→`event-organizer/uvicorn_config.json`, `adapters/sql.py`→`event_organizer/adapters/sql.py` (imports `event_scheduling.interfaces.sql` → change to `event_organizer.interfaces.sql`), `interfaces/sql.py`→`event_organizer/interfaces/sql.py` (verbatim), `alembic.ini`→`event-organizer/alembic.ini` (verbatim), `alembic/env.py`→`event-organizer/alembic/env.py` (change the target metadata import + any `event_scheduling` model import to a no-op/`event_organizer`; the env just needs `target_metadata` — set it to `None` or a bare `MetaData()` since migrations are hand-written and autogenerate isn't used). Verify: `grep -rn event_scheduling event-organizer/` returns nothing.

- [ ] **Step 2: `pyproject.toml`**:

```toml
[project]
name = "event-organizer"
version = "0.1.0"
description = "Organizer cabinet BFF: password auth + ownership-scoped schedule/bookings/profile over event-scheduling & event-users"
requires-python = ">=3.14"
dependencies = [
    "alembic>=1.16.0",
    "asyncpg>=0.31.0",
    "bcrypt>=4.2.0",
    "dishka>=1.8.0",
    "fastapi>=0.135.1",
    "greenlet>=3.2.4",
    "httpx>=0.28.0",
    "opentelemetry-sdk>=1.30.0",
    "opentelemetry-exporter-otlp-proto-grpc>=1.30.0",
    "opentelemetry-instrumentation-fastapi>=0.51b0",
    "opentelemetry-instrumentation-httpx>=0.51b0",
    "opentelemetry-instrumentation-asyncpg>=0.51b0",
    "prometheus-client>=0.25.0",
    "pydantic[email]>=2.10.0",
    "pydantic-settings>=2.13.1",
    "pyjwt>=2.10.0",
    "sqlalchemy>=2.0.48",
    "structlog>=25.5.0",
    "ujson>=5.11.0",
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
exclude = [".env", ".git", ".mypy_cache", ".pytest_cache", ".venv", "alembic"]
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
"tests/*.py" = ["ANN001", "ANN002", "ANN003", "ANN201", "ANN202", "ANN401", "ARG001", "ARG002", "E402", "PLC0415", "PLR2004", "PT019", "S101", "S105", "S106", "S311", "S603", "S607"]
```
Then `cd event-organizer && uv sync`.

- [ ] **Step 3: `event_organizer/config.py`**:

```python
from functools import lru_cache

from pydantic import Field, PostgresDsn
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", case_sensitive=False, extra="ignore"
    )

    debug: bool = False
    log_level: str = "INFO"

    postgres_dsn: PostgresDsn = Field(strict=True)

    # Session JWT
    jwt_secret_key: str = "dev-organizer-jwt-secret"  # noqa: S105 - dev default; real via env/Vault
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 60
    jwt_audience: str | None = None
    jwt_issuer: str | None = None

    # Admin provisioning key (static bearer for POST /admin/organizers)
    organizer_admin_key: str = "dev-organizer-admin-key"  # noqa: S105 - dev default

    # Upstreams (server-side keys — never exposed to the browser)
    event_scheduling_url: str = "http://event-scheduling:8888"
    scheduling_api_key: str = "dev-scheduling-api-key-3f9c2e1a7b64d508"  # noqa: S105 - dev default
    event_users_url: str = "http://event-users:8888"
    event_users_token: str = "dev-users-bearer-2a7d9e4f8c1b6350"  # noqa: S105 - dev default


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 4: `event_organizer/errors.py`**:

```python
class DomainError(Exception):
    """Base for domain errors mapped to HTTP status codes in main.py."""


class Unauthorized(DomainError):
    """Bad/absent credentials or token — HTTP 401."""


class Forbidden(DomainError):
    """Authenticated but not allowed — HTTP 403."""


class NotFoundError(DomainError):
    """Missing resource — HTTP 404."""


class ConflictError(DomainError):
    """Uniqueness/state conflict — HTTP 409."""


class ValidationError(DomainError):
    """Invalid input — HTTP 422."""


class UpstreamError(DomainError):
    """Upstream service failed/returned an unexpected status — HTTP 502."""
```

- [ ] **Step 5: `event_organizer/routes.py`** (health/ready/metrics):

```python
from fastapi import APIRouter
from starlette.responses import Response

from event_organizer import metrics


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

- [ ] **Step 6: `event_organizer/ioc.py`** (Settings + DB engine/session/sql — mirror event-scheduling's AppProvider DB providers):

```python
from collections.abc import AsyncGenerator

from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from event_organizer.adapters.sql import SqlExecutor
from event_organizer.config import Settings, get_settings
from event_organizer.interfaces.sql import ISqlExecutor


class AppProvider(Provider):
    @provide(scope=Scope.APP)
    def provide_settings(self) -> Settings:
        return get_settings()

    @provide(scope=Scope.APP)
    async def provide_db_engine(self, settings: Settings) -> AsyncGenerator[AsyncEngine]:
        engine = create_async_engine(str(settings.postgres_dsn), pool_pre_ping=True)
        yield engine
        await engine.dispose()

    @provide(scope=Scope.APP)
    def provide_sessionmaker(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    @provide(scope=Scope.REQUEST)
    async def provide_session(self, sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncGenerator[AsyncSession]:
        async with sessionmaker() as session:
            yield session

    @provide(scope=Scope.REQUEST)
    def provide_sql_executor(self, session: AsyncSession) -> ISqlExecutor:
        return SqlExecutor(session)
```
> Compare `event-scheduling/event_scheduling/ioc.py` DB providers and match the exact `create_async_engine`/`async_sessionmaker`/session-generator shape (including how it commits — event-scheduling's `provide_session` may `commit()`; mirror it).

- [ ] **Step 7: `event_organizer/main.py`** (app + error handlers; no lifespan/background tasks):

```python
from logging import getLevelNamesMapping

import structlog
from dishka import make_async_container
from dishka.integrations.fastapi import FastapiProvider, setup_dishka
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from event_organizer.config import get_settings
from event_organizer.errors import (
    ConflictError,
    Forbidden,
    NotFoundError,
    Unauthorized,
    UpstreamError,
    ValidationError,
)
from event_organizer.ioc import AppProvider
from event_organizer.logger import setup_logger
from event_organizer.metrics import HttpMetricsMiddleware
from event_organizer.routes import root_router
from event_organizer.telemetry import instrument_fastapi, setup_tracing


container = make_async_container(AppProvider(), FastapiProvider())
logger = structlog.get_logger(__name__)

_settings = get_settings()
setup_logger(log_level=getLevelNamesMapping().get(_settings.log_level), console_render=_settings.debug)

app = FastAPI(title="event-organizer", version="0.1.0")
setup_tracing()
instrument_fastapi(app)
setup_dishka(container=container, app=app)
app.include_router(root_router)
app.add_middleware(HttpMetricsMiddleware)

_STATUS = {
    Unauthorized: 401,
    Forbidden: 403,
    NotFoundError: 404,
    ConflictError: 409,
    ValidationError: 422,
    UpstreamError: 502,
}


async def _domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    status = next((code for typ, code in _STATUS.items() if isinstance(exc, typ)), 500)
    return JSONResponse(status_code=status, content={"detail": str(exc)})


for _err in (Unauthorized, Forbidden, NotFoundError, ConflictError, ValidationError, UpstreamError):
    app.add_exception_handler(_err, _domain_error_handler)
```
> Additional routers (`auth`, `admin`, `me`) are included in later tasks. `logger.py` imports `ujson` — that's why `ujson` is in deps.

- [ ] **Step 8: Empty package markers** — `event_organizer/__init__.py`, `event_organizer/auth/__init__.py`, `event_organizer/credentials/__init__.py`, `event_organizer/services/__init__.py`, `event_organizer/schemas/__init__.py`, `event_organizer/routers/__init__.py`, `tests/__init__.py` not needed.

- [ ] **Step 9: Migration `alembic/versions/0001_organizer_credential.py`**:

```python
"""organizer_credential (slice 6.1)

Revision ID: 0001
Revises:
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "organizer_credential",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("user_id", _UUID, nullable=False),
        sa.Column("email", sa.Text(), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("disabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("email", name="uq_organizer_credential_email"),
        sa.UniqueConstraint("user_id", name="uq_organizer_credential_user"),
    )


def downgrade() -> None:
    op.drop_table("organizer_credential")
```

- [ ] **Step 10: `entrypoint.sh`** (migrate then serve — mirror event-scheduling):
```sh
#!/bin/sh
set -e
alembic upgrade head
exec uvicorn event_organizer.main:app --host 0.0.0.0 --port 8888 --log-config uvicorn_config.json
```

- [ ] **Step 11: `tests/conftest.py`** (DB fixtures — copy event-scheduling's session-scoped `postgres_dsn` + `_migrated` + `sessionmaker_fixture` pattern; adapt DSN env). At minimum provide `sessionmaker_fixture` (runs `alembic upgrade head` against `TEST_POSTGRES_DSN`, yields an `async_sessionmaker`) and a `client` fixture (TestClient over `event_organizer.main.app`). Read `event-scheduling/tests/conftest.py` and mirror the `_migrated`/`sessionmaker_fixture` fixtures, replacing table names in any TRUNCATE with `organizer_credential`. Set `os.environ.setdefault("OTEL_SDK_DISABLED", "true")` at the top.

- [ ] **Step 12: `tests/test_health.py`**:
```python
def test_health_ok(client) -> None:
    r = client.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_metrics_exposed(client) -> None:
    r = client.get("/metrics")
    assert r.status_code == 200 and "http_requests_total" in r.text
```

- [ ] **Step 13: `tests/test_credential_schema.py`**:
```python
import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_organizer_credential_table(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        cols = await s.execute(
            text("SELECT column_name FROM information_schema.columns WHERE table_name='organizer_credential'")
        )
        names = {r[0] for r in cols}
        assert {"id", "user_id", "email", "password_hash", "disabled"} <= names
        uq = await s.execute(text("SELECT conname FROM pg_constraint WHERE conname LIKE 'uq_organizer_credential%'"))
        assert {r[0] for r in uq} == {"uq_organizer_credential_email", "uq_organizer_credential_user"}
```

- [ ] **Step 14: `Dockerfile` + `.dockerignore` + `.gitignore`** — mirror `event-scheduling/Dockerfile` (copies `alembic.ini`, `alembic/`, `event_organizer/`, `uvicorn_config.json`, `entrypoint.sh`; `EXPOSE 8888`). `.gitignore`: `.venv`, `__pycache__`. `.dockerignore`: `.venv`, `__pycache__`, `tests`, `.pytest_cache`, `.ruff_cache`.

- [ ] **Step 15: Run the gate.** Start `org-testpg` (Global Constraints), then `cd event-organizer && TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5601/event_organizer' uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — health(2) + schema(1) pass, ruff clean. Verify `git status` shows no `.venv`; `uv.lock` committed.

- [ ] **Step 16: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-organizer/
git commit -m "feat(organizer): scaffold event-organizer BFF + credential table (slice 6.1)"
```

---

## Task 2: Auth primitives — PasswordService, JWT, require_organizer

**Files:**
- Create: `event_organizer/auth/password.py`, `event_organizer/auth/jwt.py`, `event_organizer/auth/identity.py`
- Test: `tests/test_password.py`, `tests/test_jwt.py`

**Interfaces:**
- Produces:
  - `auth/password.py`: `PasswordService` with `hash(password: str) -> str`, `verify(plain: str, hashed: str) -> bool` (bcrypt).
  - `auth/jwt.py`: `create_access_token(settings, *, user_id: UUID, email: str) -> str`; `decode_token(settings, token: str) -> OrganizerIdentity` (raises `Unauthorized` on bad/expired).
  - `auth/identity.py`: `OrganizerIdentity(user_id: UUID, email: str)` frozen; `require_organizer(request, settings) -> OrganizerIdentity` FastAPI dependency reading `Authorization: Bearer`.

- [ ] **Step 1: `event_organizer/auth/password.py`** — copy `event-admin/event_admin/services/password.py`, rename imports (drop the `IPasswordService` import; define the class standalone):
```python
import bcrypt


class PasswordService:
    def hash(self, password: str) -> str:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    def verify(self, plain: str, hashed: str) -> bool:
        return bcrypt.checkpw(plain.encode(), hashed.encode())
```

- [ ] **Step 2: `event_organizer/auth/identity.py`**:
```python
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class OrganizerIdentity:
    user_id: UUID
    email: str
```

- [ ] **Step 3: Write the failing tests** `tests/test_jwt.py`:
```python
from uuid import uuid4

import pytest

from event_organizer.auth.jwt import create_access_token, decode_token
from event_organizer.config import Settings
from event_organizer.errors import Unauthorized


def _settings(**over):
    base = {"postgres_dsn": "postgresql+asyncpg://u:p@h:5432/d", **over}
    return Settings(**base)


def test_create_decode_round_trip() -> None:
    s = _settings()
    uid = uuid4()
    token = create_access_token(s, user_id=uid, email="a@b.io")
    ident = decode_token(s, token)
    assert ident.user_id == uid and ident.email == "a@b.io"


def test_garbage_token_rejected() -> None:
    with pytest.raises(Unauthorized):
        decode_token(_settings(), "not-a-jwt")


def test_expired_token_rejected() -> None:
    s = _settings(jwt_expire_minutes=-1)  # already expired
    token = create_access_token(s, user_id=uuid4(), email="a@b.io")
    with pytest.raises(Unauthorized):
        decode_token(s, token)
```
`tests/test_password.py`:
```python
from event_organizer.auth.password import PasswordService


def test_hash_and_verify() -> None:
    svc = PasswordService()
    h = svc.hash("secret123")
    assert h != "secret123"
    assert svc.verify("secret123", h) is True
    assert svc.verify("wrong", h) is False
```

- [ ] **Step 4: Run — verify FAIL** (`ModuleNotFoundError: event_organizer.auth.jwt`).

- [ ] **Step 5: `event_organizer/auth/jwt.py`**:
```python
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt

from event_organizer.auth.identity import OrganizerIdentity
from event_organizer.config import Settings
from event_organizer.errors import Unauthorized


def create_access_token(settings: Settings, *, user_id: UUID, email: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    claims: dict[str, Any] = {"sub": str(user_id), "email": email, "exp": expire}
    if settings.jwt_audience:
        claims["aud"] = settings.jwt_audience
    if settings.jwt_issuer:
        claims["iss"] = settings.jwt_issuer
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(settings: Settings, token: str) -> OrganizerIdentity:
    options = {"verify_aud": bool(settings.jwt_audience)}
    kwargs: dict[str, Any] = {"options": options, "algorithms": [settings.jwt_algorithm]}
    if settings.jwt_audience:
        kwargs["audience"] = settings.jwt_audience
    if settings.jwt_issuer:
        kwargs["issuer"] = settings.jwt_issuer
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, **kwargs)
    except jwt.PyJWTError as exc:
        raise Unauthorized("invalid or expired token") from exc
    return OrganizerIdentity(user_id=UUID(payload["sub"]), email=payload["email"])
```

- [ ] **Step 6: `event_organizer/auth/identity.py`** — append the dependency:
```python
from typing import Annotated

from dishka.integrations.fastapi import FromDishka
from fastapi import Depends
from starlette.requests import Request

from event_organizer.auth.jwt import decode_token
from event_organizer.config import Settings
from event_organizer.errors import Unauthorized


def require_organizer(request: Request, settings: FromDishka[Settings]) -> OrganizerIdentity:
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        raise Unauthorized("missing bearer token")
    return decode_token(settings, header[len(prefix):])
```
> Note: `require_organizer` uses `FromDishka[Settings]` — this works with Dishka's FastAPI integration on `DishkaRoute` routers. If injecting `Settings` into a plain FastAPI dependency proves awkward, instead read settings via `get_settings()` inside the function (module-level import `from event_organizer.config import get_settings`). Prefer whichever the codebase's dependency style supports cleanly; the /api/me routers (Task 6) use `route_class=DishkaRoute`, so `FromDishka` is available. Verify at implementation and keep the return type `OrganizerIdentity`.

- [ ] **Step 7: Run — verify PASS.** `... uv run pytest tests/test_jwt.py tests/test_password.py -v` — 4 pass; ruff clean.

- [ ] **Step 8: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-organizer/event_organizer/auth/ event-organizer/tests/test_jwt.py event-organizer/tests/test_password.py
git commit -m "feat(organizer): password service + JWT + require_organizer (slice 6.1)"
```

---

## Task 3: Credential DTO + adapter

**Files:**
- Create: `event_organizer/credentials/dto.py`, `event_organizer/credentials/interfaces.py`, `event_organizer/credentials/adapter.py`
- Test: `tests/test_credential_adapter.py`

**Interfaces:**
- Consumes: `ISqlExecutor`, `errors.ConflictError`.
- Produces:
  - `dto.py`: `OrganizerCredentialDTO(id: UUID, user_id: UUID, email: str, password_hash: str, disabled: bool)` frozen.
  - `interfaces.py`: `ICredentialAdapter` (`get_by_email(email) -> OrganizerCredentialDTO | None`, `create(user_id, email, password_hash) -> OrganizerCredentialDTO`, `update_password_hash(user_id, password_hash) -> None`).
  - `adapter.py`: `CredentialAdapter(sql)`.

- [ ] **Step 1: `event_organizer/credentials/dto.py`**:
```python
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class OrganizerCredentialDTO:
    id: UUID
    user_id: UUID
    email: str
    password_hash: str
    disabled: bool
```

- [ ] **Step 2: `event_organizer/credentials/interfaces.py`**:
```python
from typing import Protocol
from uuid import UUID

from event_organizer.credentials.dto import OrganizerCredentialDTO


class ICredentialAdapter(Protocol):
    async def get_by_email(self, email: str) -> OrganizerCredentialDTO | None: ...
    async def create(self, user_id: UUID, email: str, password_hash: str) -> OrganizerCredentialDTO: ...
    async def update_password_hash(self, user_id: UUID, password_hash: str) -> None: ...
```

- [ ] **Step 3: Write the failing test** `tests/test_credential_adapter.py`:
```python
from uuid import uuid4

import pytest
from sqlalchemy import text

from event_organizer.adapters.sql import SqlExecutor
from event_organizer.credentials.adapter import CredentialAdapter
from event_organizer.errors import ConflictError


@pytest.mark.asyncio
async def test_create_get_update_and_dup(sessionmaker_fixture) -> None:
    uid = uuid4()
    async with sessionmaker_fixture() as s:
        a = CredentialAdapter(SqlExecutor(s))
        c = await a.create(uid, "a@b.io", "hash1")
        await s.commit()
        assert c.user_id == uid and c.email == "a@b.io" and c.disabled is False

    async with sessionmaker_fixture() as s:
        a = CredentialAdapter(SqlExecutor(s))
        got = await a.get_by_email("a@b.io")
        assert got is not None and got.password_hash == "hash1"
        assert await a.get_by_email("missing@x.io") is None

    async with sessionmaker_fixture() as s:
        a = CredentialAdapter(SqlExecutor(s))
        await a.update_password_hash(uid, "hash2")
        await s.commit()
    async with sessionmaker_fixture() as s:
        got = await CredentialAdapter(SqlExecutor(s)).get_by_email("a@b.io")
        assert got.password_hash == "hash2"

    async with sessionmaker_fixture() as s:
        with pytest.raises(ConflictError):
            await CredentialAdapter(SqlExecutor(s)).create(uuid4(), "a@b.io", "h")  # dup email
```

- [ ] **Step 4: Run — verify FAIL.**

- [ ] **Step 5: `event_organizer/credentials/adapter.py`**:
```python
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from event_organizer.credentials.dto import OrganizerCredentialDTO
from event_organizer.errors import ConflictError
from event_organizer.interfaces.sql import ISqlExecutor

_COLS = "id, user_id, email, password_hash, disabled"


def _to_dto(r: dict) -> OrganizerCredentialDTO:
    return OrganizerCredentialDTO(
        id=r["id"], user_id=r["user_id"], email=r["email"], password_hash=r["password_hash"], disabled=r["disabled"]
    )


class CredentialAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def get_by_email(self, email: str) -> OrganizerCredentialDTO | None:
        row = await self._sql.fetch_one(
            f"SELECT {_COLS} FROM organizer_credential WHERE email=:e", {"e": email}  # noqa: S608
        )
        if row is None:
            return None
        return _to_dto(row)

    async def create(self, user_id: UUID, email: str, password_hash: str) -> OrganizerCredentialDTO:
        try:
            async with self._sql.begin_nested():
                row = await self._sql.fetch_one(
                    f"INSERT INTO organizer_credential (user_id, email, password_hash) "  # noqa: S608
                    f"VALUES (:u,:e,:h) RETURNING {_COLS}",
                    {"u": user_id, "e": email, "h": password_hash},
                )
        except IntegrityError as exc:
            raise ConflictError("organizer already has credentials") from exc
        return _to_dto(row)

    async def update_password_hash(self, user_id: UUID, password_hash: str) -> None:
        await self._sql.execute(
            "UPDATE organizer_credential SET password_hash=:h, updated_at=now() WHERE user_id=:u",
            {"h": password_hash, "u": user_id},
        )
```

- [ ] **Step 6: Run — verify PASS.** `... uv run pytest tests/test_credential_adapter.py -v`; ruff clean.

- [ ] **Step 7: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-organizer/event_organizer/credentials/ event-organizer/tests/test_credential_adapter.py
git commit -m "feat(organizer): credential DTO + adapter (get/create/update, dup->409) (slice 6.1)"
```

---

## Task 4: UsersClient + SchedulingClient

**Files:**
- Create: `event_organizer/adapters/scheduling_client.py`, `event_organizer/adapters/users_client.py`, `event_organizer/adapters/interfaces.py`
- Test: `tests/test_scheduling_client.py`, `tests/test_users_client.py`

**Interfaces:**
- Consumes: `errors` (`NotFoundError`, `UpstreamError`).
- Produces:
  - `adapters/interfaces.py`: `ISchedulingClient` (`get_schedule(owner_user_id) -> dict`, `put_schedule(owner_user_id, body: dict) -> dict`, `put_travel(owner_user_id, body: dict) -> dict`, `get_bookings(host_user_id) -> list[dict]`), `IUsersClient` (`get_user(user_id) -> dict`, `patch_user(user_id, body: dict) -> dict`, `is_organizer(email) -> bool`).
  - `SchedulingClient(base_url, api_key, *, transport=None)`, `UsersClient(base_url, token, *, transport=None)`.

- [ ] **Step 1: `event_organizer/adapters/interfaces.py`**:
```python
from typing import Protocol
from uuid import UUID


class ISchedulingClient(Protocol):
    async def get_schedule(self, owner_user_id: UUID) -> dict: ...
    async def put_schedule(self, owner_user_id: UUID, body: dict) -> dict: ...
    async def put_travel(self, owner_user_id: UUID, body: dict) -> dict: ...
    async def get_bookings(self, host_user_id: UUID) -> list[dict]: ...


class IUsersClient(Protocol):
    async def get_user(self, user_id: UUID) -> dict: ...
    async def patch_user(self, user_id: UUID, body: dict) -> dict: ...
    async def is_organizer(self, email: str) -> bool: ...
```

- [ ] **Step 2: Write the failing tests** `tests/test_scheduling_client.py`:
```python
from uuid import uuid4

import httpx
import pytest

from event_organizer.adapters.scheduling_client import SchedulingClient
from event_organizer.errors import NotFoundError, UpstreamError

BASE, KEY = "http://sched.test", "k"


def _c(handler):
    return SchedulingClient(BASE, KEY, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_get_schedule_ok_and_bearer() -> None:
    uid = uuid4()

    def h(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == f"Bearer {KEY}"
        assert req.url.path == f"/api/v1/schedules/{uid}"
        return httpx.Response(200, json={"schedule": {"owner_user_id": str(uid)}, "weekly_hours": []})

    out = await _c(h).get_schedule(uid)
    assert out["schedule"]["owner_user_id"] == str(uid)


@pytest.mark.asyncio
async def test_get_schedule_404_raises_not_found() -> None:
    with pytest.raises(NotFoundError):
        await _c(lambda _req: httpx.Response(404)).get_schedule(uuid4())


@pytest.mark.asyncio
async def test_put_schedule_forwards_body() -> None:
    uid = uuid4()

    def h(req: httpx.Request) -> httpx.Response:
        import json
        assert req.method == "PUT" and json.loads(req.content)["time_zone"] == "UTC"
        return httpx.Response(200, json={"schedule": {"owner_user_id": str(uid)}, "weekly_hours": []})

    await _c(h).put_schedule(uid, {"time_zone": "UTC", "weekly_hours": [], "date_overrides": []})


@pytest.mark.asyncio
async def test_get_bookings_query_and_unwrap() -> None:
    uid = uuid4()

    def h(req: httpx.Request) -> httpx.Response:
        assert req.url.path == "/api/v1/bookings"
        assert req.url.params["host_user_id"] == str(uid)
        return httpx.Response(200, json={"bookings": [{"id": str(uuid4()), "status": "confirmed"}]})

    out = await _c(h).get_bookings(uid)
    assert len(out) == 1 and out[0]["status"] == "confirmed"


@pytest.mark.asyncio
async def test_5xx_raises_upstream() -> None:
    with pytest.raises(UpstreamError):
        await _c(lambda _req: httpx.Response(503)).get_schedule(uuid4())
```
`tests/test_users_client.py`:
```python
from uuid import uuid4

import httpx
import pytest

from event_organizer.adapters.users_client import UsersClient
from event_organizer.errors import NotFoundError, UpstreamError

BASE, TOKEN = "http://users.test", "t"


def _c(handler):
    return UsersClient(BASE, TOKEN, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_get_user_ok() -> None:
    uid = uuid4()

    def h(req: httpx.Request) -> httpx.Response:
        assert req.headers["authorization"] == f"Bearer {TOKEN}"
        assert req.url.path == f"/api/users/id/{uid}"
        return httpx.Response(200, json={"id": str(uid), "email": "a@b.io", "name": "A", "role": "organizer", "time_zone": "UTC"})

    out = await _c(h).get_user(uid)
    assert out["name"] == "A"


@pytest.mark.asyncio
async def test_get_user_404() -> None:
    with pytest.raises(NotFoundError):
        await _c(lambda _req: httpx.Response(404)).get_user(uuid4())


@pytest.mark.asyncio
async def test_patch_user_forwards_body() -> None:
    uid = uuid4()

    def h(req: httpx.Request) -> httpx.Response:
        import json
        assert req.method == "PATCH" and json.loads(req.content) == {"name": "New", "time_zone": "Europe/Moscow"}
        return httpx.Response(200, json={"id": str(uid), "email": "a@b.io", "name": "New", "role": "organizer", "time_zone": "Europe/Moscow"})

    out = await _c(h).patch_user(uid, {"name": "New", "time_zone": "Europe/Moscow"})
    assert out["name"] == "New"


@pytest.mark.asyncio
async def test_is_organizer_true_false() -> None:
    assert await _c(lambda _req: httpx.Response(200, json={"id": str(uuid4()), "email": "a@b.io", "name": "A", "role": "organizer", "time_zone": "UTC"})).is_organizer("a@b.io") is True
    assert await _c(lambda _req: httpx.Response(404)).is_organizer("x@y.io") is False
```

- [ ] **Step 3: Run — verify FAIL.**

- [ ] **Step 4: `event_organizer/adapters/scheduling_client.py`**:
```python
from uuid import UUID

import httpx

from event_organizer.errors import NotFoundError, UpstreamError


class SchedulingClient:
    def __init__(self, base_url: str, api_key: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._transport = transport

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport, timeout=10.0, headers={"authorization": f"Bearer {self._api_key}"}
        )

    @staticmethod
    def _ok(resp: httpx.Response) -> dict:
        if resp.status_code == httpx.codes.NOT_FOUND:
            raise NotFoundError("schedule not found")
        if not resp.is_success:
            raise UpstreamError(f"event-scheduling returned {resp.status_code}")
        return resp.json()

    async def get_schedule(self, owner_user_id: UUID) -> dict:
        async with self._http() as c:
            resp = await c.get(f"{self._base_url}/api/v1/schedules/{owner_user_id}")
        return self._ok(resp)

    async def put_schedule(self, owner_user_id: UUID, body: dict) -> dict:
        async with self._http() as c:
            resp = await c.put(f"{self._base_url}/api/v1/schedules/{owner_user_id}", json=body)
        return self._ok(resp)

    async def put_travel(self, owner_user_id: UUID, body: dict) -> dict:
        async with self._http() as c:
            resp = await c.put(f"{self._base_url}/api/v1/schedules/{owner_user_id}/travel", json=body)
        return self._ok(resp)

    async def get_bookings(self, host_user_id: UUID) -> list[dict]:
        async with self._http() as c:
            resp = await c.get(f"{self._base_url}/api/v1/bookings", params={"host_user_id": str(host_user_id)})
        if not resp.is_success:
            raise UpstreamError(f"event-scheduling returned {resp.status_code}")
        return resp.json()["bookings"]
```

- [ ] **Step 5: `event_organizer/adapters/users_client.py`**:
```python
from uuid import UUID

import httpx

from event_organizer.errors import NotFoundError, UpstreamError


class UsersClient:
    def __init__(self, base_url: str, token: str, *, transport: httpx.BaseTransport | None = None) -> None:
        self._base_url = base_url.rstrip("/")
        self._token = token
        self._transport = transport

    def _http(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            transport=self._transport, timeout=10.0, headers={"authorization": f"Bearer {self._token}"}
        )

    async def get_user(self, user_id: UUID) -> dict:
        async with self._http() as c:
            resp = await c.get(f"{self._base_url}/api/users/id/{user_id}")
        if resp.status_code == httpx.codes.NOT_FOUND:
            raise NotFoundError("user not found")
        if not resp.is_success:
            raise UpstreamError(f"event-users returned {resp.status_code}")
        return resp.json()

    async def patch_user(self, user_id: UUID, body: dict) -> dict:
        async with self._http() as c:
            resp = await c.patch(f"{self._base_url}/api/users/id/{user_id}", json=body)
        if resp.status_code == httpx.codes.NOT_FOUND:
            raise NotFoundError("user not found")
        if not resp.is_success:
            raise UpstreamError(f"event-users returned {resp.status_code}")
        return resp.json()

    async def is_organizer(self, email: str) -> bool:
        async with self._http() as c:
            resp = await c.get(f"{self._base_url}/api/users/by-identity", params={"email": email, "role": "organizer"})
        if resp.status_code == httpx.codes.NOT_FOUND:
            return False
        if not resp.is_success:
            raise UpstreamError(f"event-users returned {resp.status_code}")
        return True
```

- [ ] **Step 6: Run — verify PASS.** `... uv run pytest tests/test_scheduling_client.py tests/test_users_client.py -v`; ruff clean.

- [ ] **Step 7: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-organizer/event_organizer/adapters/scheduling_client.py event-organizer/event_organizer/adapters/users_client.py \
        event-organizer/event_organizer/adapters/interfaces.py \
        event-organizer/tests/test_scheduling_client.py event-organizer/tests/test_users_client.py
git commit -m "feat(organizer): SchedulingClient + UsersClient (Bearer proxies) (slice 6.1)"
```

---

## Task 5: login + admin provisioning

**Files:**
- Create: `event_organizer/schemas/auth.py`, `event_organizer/schemas/admin.py`, `event_organizer/services/login_service.py`, `event_organizer/services/provisioning_service.py`, `event_organizer/routers/auth.py`, `event_organizer/routers/admin.py`
- Modify: `event_organizer/ioc.py` (providers), `event_organizer/main.py` (include routers)
- Test: `tests/test_auth_api.py`

**Interfaces:**
- Consumes: `PasswordService`, `create_access_token`, `ICredentialAdapter`, `IUsersClient`, `Settings`, `require_organizer` (for a wiring smoke).
- Produces: `POST /auth/login`, `POST /admin/organizers`; DI providers for `PasswordService`, `ICredentialAdapter`→`CredentialAdapter`, `ISchedulingClient`→`SchedulingClient`, `IUsersClient`→`UsersClient`, `LoginService`, `ProvisioningService`.

- [ ] **Step 1: Schemas.** `event_organizer/schemas/auth.py`:
```python
from pydantic import BaseModel, EmailStr


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class LoginResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
```
`event_organizer/schemas/admin.py`:
```python
from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr


class CreateOrganizerRequest(BaseModel):
    user_id: UUID
    email: EmailStr
    password: str


class OrganizerCreatedResponse(BaseModel):
    id: UUID
    user_id: UUID
    email: str
```

- [ ] **Step 2: Services.** `event_organizer/services/login_service.py`:
```python
from event_organizer.auth.jwt import create_access_token
from event_organizer.auth.password import PasswordService
from event_organizer.config import Settings
from event_organizer.credentials.interfaces import ICredentialAdapter
from event_organizer.errors import Unauthorized


class LoginService:
    def __init__(self, credentials: ICredentialAdapter, passwords: PasswordService, settings: Settings) -> None:
        self._credentials = credentials
        self._passwords = passwords
        self._settings = settings

    async def login(self, email: str, password: str) -> str:
        credential = await self._credentials.get_by_email(email)
        if credential is None or credential.disabled:
            raise Unauthorized("invalid credentials")
        if not self._passwords.verify(password, credential.password_hash):
            raise Unauthorized("invalid credentials")
        return create_access_token(self._settings, user_id=credential.user_id, email=credential.email)
```
`event_organizer/services/provisioning_service.py`:
```python
from uuid import UUID

from event_organizer.adapters.interfaces import IUsersClient
from event_organizer.auth.password import PasswordService
from event_organizer.credentials.dto import OrganizerCredentialDTO
from event_organizer.credentials.interfaces import ICredentialAdapter
from event_organizer.errors import ValidationError


class ProvisioningService:
    def __init__(self, credentials: ICredentialAdapter, passwords: PasswordService, users: IUsersClient) -> None:
        self._credentials = credentials
        self._passwords = passwords
        self._users = users

    async def create(self, user_id: UUID, email: str, password: str) -> OrganizerCredentialDTO:
        if not await self._users.is_organizer(email):
            raise ValidationError("not an organizer in event-users")
        return await self._credentials.create(user_id, email, self._passwords.hash(password))
```

- [ ] **Step 3: Routers.** `event_organizer/routers/auth.py`:
```python
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter

from event_organizer.schemas.auth import LoginRequest, LoginResponse
from event_organizer.services.login_service import LoginService

auth_router = APIRouter(tags=["auth"], route_class=DishkaRoute)


@auth_router.post("/auth/login", response_model=LoginResponse)
async def login(body: LoginRequest, service: FromDishka[LoginService]) -> LoginResponse:
    token = await service.login(str(body.email), body.password)
    return LoginResponse(access_token=token)
```
`event_organizer/routers/admin.py`:
```python
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Header, status

from event_organizer.config import Settings
from event_organizer.errors import Unauthorized
from event_organizer.schemas.admin import CreateOrganizerRequest, OrganizerCreatedResponse
from event_organizer.services.provisioning_service import ProvisioningService

admin_router = APIRouter(prefix="/admin", tags=["admin"], route_class=DishkaRoute)


@admin_router.post("/organizers", response_model=OrganizerCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_organizer(
    body: CreateOrganizerRequest,
    service: FromDishka[ProvisioningService],
    settings: FromDishka[Settings],
    authorization: str = Header(default=""),
) -> OrganizerCreatedResponse:
    expected = f"Bearer {settings.organizer_admin_key}"
    if authorization != expected:
        raise Unauthorized("invalid admin key")
    created = await service.create(body.user_id, str(body.email), body.password)
    return OrganizerCreatedResponse(id=created.id, user_id=created.user_id, email=created.email)
```

- [ ] **Step 4: ioc providers** — add to `AppProvider`:
```python
    @provide(scope=Scope.APP)
    def provide_password_service(self) -> PasswordService:
        return PasswordService()

    @provide(scope=Scope.REQUEST)
    def provide_credential_adapter(self, sql: ISqlExecutor) -> ICredentialAdapter:
        return CredentialAdapter(sql)

    @provide(scope=Scope.APP)
    def provide_scheduling_client(self, settings: Settings) -> ISchedulingClient:
        return SchedulingClient(settings.event_scheduling_url, settings.scheduling_api_key)

    @provide(scope=Scope.APP)
    def provide_users_client(self, settings: Settings) -> IUsersClient:
        return UsersClient(settings.event_users_url, settings.event_users_token)

    @provide(scope=Scope.REQUEST)
    def provide_login_service(self, credentials: ICredentialAdapter, passwords: PasswordService, settings: Settings) -> LoginService:
        return LoginService(credentials, passwords, settings)

    @provide(scope=Scope.REQUEST)
    def provide_provisioning_service(self, credentials: ICredentialAdapter, passwords: PasswordService, users: IUsersClient) -> ProvisioningService:
        return ProvisioningService(credentials, passwords, users)
```
with imports at top of ioc.py:
```python
from event_organizer.adapters.interfaces import ISchedulingClient, IUsersClient
from event_organizer.adapters.scheduling_client import SchedulingClient
from event_organizer.adapters.users_client import UsersClient
from event_organizer.auth.password import PasswordService
from event_organizer.credentials.adapter import CredentialAdapter
from event_organizer.credentials.interfaces import ICredentialAdapter
from event_organizer.services.login_service import LoginService
from event_organizer.services.provisioning_service import ProvisioningService
```

- [ ] **Step 5: Include routers** in `main.py`: `from event_organizer.routers.auth import auth_router` + `from event_organizer.routers.admin import admin_router`; `app.include_router(auth_router)` + `app.include_router(admin_router)` (after `root_router`).

- [ ] **Step 6: Write the failing test** `tests/test_auth_api.py` (build the app with `IUsersClient` overridden by a fake so provisioning doesn't hit the network; mirror the Dishka-override conftest pattern):
```python
from uuid import uuid4

import pytest


class _FakeUsers:
    def __init__(self, organizer: bool = True) -> None:
        self._organizer = organizer

    async def is_organizer(self, email):
        return self._organizer

    async def get_user(self, user_id): ...
    async def patch_user(self, user_id, body): ...


def _app(users_organizer: bool = True):
    from dishka import Provider, Scope, make_async_container, provide
    from dishka.integrations.fastapi import FastapiProvider, setup_dishka
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse
    from starlette.testclient import TestClient

    from event_organizer.adapters.interfaces import IUsersClient
    from event_organizer.errors import ConflictError, Forbidden, NotFoundError, Unauthorized, UpstreamError, ValidationError
    from event_organizer.ioc import AppProvider
    from event_organizer.main import _domain_error_handler
    from event_organizer.routers.admin import admin_router
    from event_organizer.routers.auth import auth_router
    from event_organizer.routes import root_router

    class FakeUsersProvider(Provider):
        @provide(scope=Scope.APP, override=True)
        def users(self) -> IUsersClient:
            return _FakeUsers(organizer=users_organizer)

    container = make_async_container(AppProvider(), FakeUsersProvider(), FastapiProvider())
    app = FastAPI()
    setup_dishka(container=container, app=app)
    app.include_router(root_router)
    app.include_router(auth_router)
    app.include_router(admin_router)
    for err in (Unauthorized, Forbidden, NotFoundError, ConflictError, ValidationError, UpstreamError):
        app.add_exception_handler(err, _domain_error_handler)
    return app


ADMIN = "dev-organizer-admin-key"


@pytest.mark.asyncio
async def test_provision_then_login(sessionmaker_fixture) -> None:
    # sessionmaker_fixture ensures migrations applied; the app uses its own container/engine over the same test DB.
    with TestClient(_app()) as c:
        uid = str(uuid4())
        r = c.post("/admin/organizers", json={"user_id": uid, "email": "org@x.io", "password": "pw12345"},
                   headers={"Authorization": f"Bearer {ADMIN}"})
        assert r.status_code == 201
        # login works
        lr = c.post("/auth/login", json={"email": "org@x.io", "password": "pw12345"})
        assert lr.status_code == 200 and lr.json()["access_token"]
        # wrong password
        assert c.post("/auth/login", json={"email": "org@x.io", "password": "bad"}).status_code == 401
        # dup provision
        assert c.post("/admin/organizers", json={"user_id": str(uuid4()), "email": "org@x.io", "password": "x"},
                      headers={"Authorization": f"Bearer {ADMIN}"}).status_code == 409
        # bad admin key
        assert c.post("/admin/organizers", json={"user_id": str(uuid4()), "email": "y@x.io", "password": "x"},
                      headers={"Authorization": "Bearer nope"}).status_code == 401


@pytest.mark.asyncio
async def test_provision_non_organizer_422(sessionmaker_fixture) -> None:
    from starlette.testclient import TestClient
    with TestClient(_app(users_organizer=False)) as c:
        r = c.post("/admin/organizers", json={"user_id": str(uuid4()), "email": "z@x.io", "password": "pw"},
                   headers={"Authorization": f"Bearer {ADMIN}"})
        assert r.status_code == 422
```
> The test app must point at the same test DB. Ensure `TEST_POSTGRES_DSN` is set (the `Settings.postgres_dsn` reads it via env — set `POSTGRES_DSN` in the test env or make conftest export it). If `Settings` needs `POSTGRES_DSN`, the conftest should `os.environ.setdefault("POSTGRES_DSN", os.environ["TEST_POSTGRES_DSN"])`. Add `from starlette.testclient import TestClient` import at the top of the test. Adjust the harness to the repo's real Dishka-override + settings-env idiom until green; the assertions (201/200/401/409/422) are fixed.

- [ ] **Step 7: Run — verify PASS.** `... uv run pytest tests/test_auth_api.py -v`; full suite; ruff clean.

- [ ] **Step 8: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-organizer/event_organizer/schemas/auth.py event-organizer/event_organizer/schemas/admin.py \
        event-organizer/event_organizer/services/login_service.py event-organizer/event_organizer/services/provisioning_service.py \
        event-organizer/event_organizer/routers/auth.py event-organizer/event_organizer/routers/admin.py \
        event-organizer/event_organizer/ioc.py event-organizer/event_organizer/main.py event-organizer/tests/test_auth_api.py
git commit -m "feat(organizer): login + admin provisioning (slice 6.1)"
```

---

## Task 6: /api/me/* — schedule, bookings, profile, password

**Files:**
- Create: `event_organizer/schemas/me.py`, `event_organizer/services/profile_service.py`, `event_organizer/services/password_change_service.py`, `event_organizer/routers/me.py`
- Modify: `event_organizer/ioc.py` (providers), `event_organizer/main.py` (include `me_router`)
- Test: `tests/test_me_api.py`

**Interfaces:**
- Consumes: `require_organizer`→`OrganizerIdentity`, `ISchedulingClient`, `IUsersClient`, `ICredentialAdapter`, `PasswordService`.
- Produces: `GET/PUT /api/me/schedule` (+ `/travel`), `GET /api/me/bookings`, `GET/PUT /api/me/profile`, `PUT /api/me/password`.

- [ ] **Step 1: `event_organizer/schemas/me.py`**:
```python
from __future__ import annotations

from datetime import date, time
from pydantic import BaseModel


class WeeklyHourModel(BaseModel):
    day_of_week: int
    start_time: time
    end_time: time


class DateOverrideModel(BaseModel):
    date: date
    start_time: time | None = None
    end_time: time | None = None


class SchedulePutRequest(BaseModel):
    time_zone: str
    weekly_hours: list[WeeklyHourModel]
    date_overrides: list[DateOverrideModel]


class ProfileResponse(BaseModel):
    name: str | None
    email: str
    time_zone: str | None


class ProfilePutRequest(BaseModel):
    name: str
    time_zone: str


class PasswordChangeRequest(BaseModel):
    old_password: str
    new_password: str


class BookingItem(BaseModel):
    id: str
    start_time: str
    end_time: str
    status: str
```

- [ ] **Step 2: Services.** `event_organizer/services/profile_service.py`:
```python
from uuid import UUID

from event_organizer.adapters.interfaces import IUsersClient


class ProfileService:
    def __init__(self, users: IUsersClient) -> None:
        self._users = users

    async def get(self, user_id: UUID) -> dict:
        u = await self._users.get_user(user_id)
        return {"name": u.get("name"), "email": u["email"], "time_zone": u.get("time_zone")}

    async def update(self, user_id: UUID, name: str, time_zone: str) -> dict:
        u = await self._users.patch_user(user_id, {"name": name, "time_zone": time_zone})
        return {"name": u.get("name"), "email": u["email"], "time_zone": u.get("time_zone")}
```
`event_organizer/services/password_change_service.py`:
```python
from uuid import UUID

from event_organizer.auth.password import PasswordService
from event_organizer.credentials.interfaces import ICredentialAdapter
from event_organizer.errors import Unauthorized


class PasswordChangeService:
    def __init__(self, credentials: ICredentialAdapter, passwords: PasswordService) -> None:
        self._credentials = credentials
        self._passwords = passwords

    async def change(self, user_id: UUID, email: str, old_password: str, new_password: str) -> None:
        credential = await self._credentials.get_by_email(email)
        if credential is None or not self._passwords.verify(old_password, credential.password_hash):
            raise Unauthorized("invalid credentials")
        await self._credentials.update_password_hash(user_id, self._passwords.hash(new_password))
```

- [ ] **Step 3: `event_organizer/routers/me.py`**:
```python
from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends

from event_organizer.adapters.interfaces import ISchedulingClient
from event_organizer.auth.identity import OrganizerIdentity, require_organizer
from event_organizer.schemas.me import (
    BookingItem,
    PasswordChangeRequest,
    ProfilePutRequest,
    ProfileResponse,
    SchedulePutRequest,
)
from event_organizer.services.password_change_service import PasswordChangeService
from event_organizer.services.profile_service import ProfileService

me_router = APIRouter(prefix="/api/me", tags=["me"], route_class=DishkaRoute)


@me_router.get("/schedule")
async def get_schedule(scheduling: FromDishka[ISchedulingClient], me: OrganizerIdentity = Depends(require_organizer)) -> dict:
    return await scheduling.get_schedule(me.user_id)


@me_router.put("/schedule")
async def put_schedule(
    body: SchedulePutRequest, scheduling: FromDishka[ISchedulingClient], me: OrganizerIdentity = Depends(require_organizer)
) -> dict:
    return await scheduling.put_schedule(me.user_id, body.model_dump(mode="json"))


@me_router.put("/schedule/travel")
async def put_travel(
    body: dict, scheduling: FromDishka[ISchedulingClient], me: OrganizerIdentity = Depends(require_organizer)
) -> dict:
    return await scheduling.put_travel(me.user_id, body)


@me_router.get("/bookings", response_model=list[BookingItem])
async def get_bookings(scheduling: FromDishka[ISchedulingClient], me: OrganizerIdentity = Depends(require_organizer)) -> list[BookingItem]:
    rows = await scheduling.get_bookings(me.user_id)
    return [BookingItem(id=r["id"], start_time=r["start_time"], end_time=r["end_time"], status=r["status"]) for r in rows]


@me_router.get("/profile", response_model=ProfileResponse)
async def get_profile(profile: FromDishka[ProfileService], me: OrganizerIdentity = Depends(require_organizer)) -> ProfileResponse:
    return ProfileResponse(**await profile.get(me.user_id))


@me_router.put("/profile", response_model=ProfileResponse)
async def put_profile(
    body: ProfilePutRequest, profile: FromDishka[ProfileService], me: OrganizerIdentity = Depends(require_organizer)
) -> ProfileResponse:
    return ProfileResponse(**await profile.update(me.user_id, body.name, body.time_zone))


@me_router.put("/password", status_code=204)
async def change_password(
    body: PasswordChangeRequest, service: FromDishka[PasswordChangeService], me: OrganizerIdentity = Depends(require_organizer)
) -> None:
    await service.change(me.user_id, me.email, body.old_password, body.new_password)
```
> The bookings response projects ONLY `{id,start_time,end_time,status}` — `client_user_id`/`host_user_id` are NOT exposed (attendee-name resolution deferred). Profile response drops `id`/`role`.

- [ ] **Step 4: ioc providers** — add `ProfileService` + `PasswordChangeService`:
```python
    @provide(scope=Scope.REQUEST)
    def provide_profile_service(self, users: IUsersClient) -> ProfileService:
        return ProfileService(users)

    @provide(scope=Scope.REQUEST)
    def provide_password_change_service(self, credentials: ICredentialAdapter, passwords: PasswordService) -> PasswordChangeService:
        return PasswordChangeService(credentials, passwords)
```
(+ imports for the two services.)

- [ ] **Step 5: Include router** in `main.py`: `from event_organizer.routers.me import me_router` + `app.include_router(me_router)`.

- [ ] **Step 6: Write the failing test** `tests/test_me_api.py` — build the app with `ISchedulingClient` + `IUsersClient` overridden by fakes; obtain a real JWT via login (provision first) OR mint one directly with `create_access_token`. Cover: schedule GET/PUT hit `me.user_id`; bookings projects to 4 fields (no client_user_id leak); profile GET projects name/email/tz; profile PUT forwards ONLY name+time_zone; password change with correct old → 204 and re-login works, wrong old → 401; no token → 401. Mint the token directly to avoid a full login dance:
```python
from uuid import uuid4

import pytest

from event_organizer.auth.jwt import create_access_token
from event_organizer.config import get_settings


class _FakeScheduling:
    def __init__(self) -> None:
        self.seen_owner = None

    async def get_schedule(self, owner_user_id):
        self.seen_owner = owner_user_id
        return {"schedule": {"owner_user_id": str(owner_user_id)}, "weekly_hours": [], "date_overrides": []}

    async def put_schedule(self, owner_user_id, body):
        self.seen_owner = owner_user_id
        return {"schedule": {"owner_user_id": str(owner_user_id)}, "weekly_hours": [], "date_overrides": []}

    async def put_travel(self, owner_user_id, body): return {}
    async def get_bookings(self, host_user_id):
        return [{"id": "b1", "start_time": "2026-10-01T09:00:00Z", "end_time": "2026-10-01T09:30:00Z",
                 "status": "confirmed", "client_user_id": str(uuid4()), "host_user_id": str(host_user_id)}]


class _FakeUsers:
    def __init__(self) -> None:
        self.patched = None
    async def get_user(self, user_id):
        return {"id": str(user_id), "email": "org@x.io", "name": "Org", "role": "organizer", "time_zone": "UTC"}
    async def patch_user(self, user_id, body):
        self.patched = body
        return {"id": str(user_id), "email": "org@x.io", "name": body["name"], "role": "organizer", "time_zone": body["time_zone"]}
    async def is_organizer(self, email): return True


def _app_and_fakes():
    from dishka import Provider, Scope, make_async_container, provide
    from dishka.integrations.fastapi import FastapiProvider, setup_dishka
    from fastapi import FastAPI
    from event_organizer.adapters.interfaces import ISchedulingClient, IUsersClient
    from event_organizer.errors import ConflictError, Forbidden, NotFoundError, Unauthorized, UpstreamError, ValidationError
    from event_organizer.ioc import AppProvider
    from event_organizer.main import _domain_error_handler
    from event_organizer.routers.me import me_router
    from event_organizer.routes import root_router

    sched, users = _FakeScheduling(), _FakeUsers()

    class Fakes(Provider):
        @provide(scope=Scope.APP, override=True)
        def s(self) -> ISchedulingClient: return sched
        @provide(scope=Scope.APP, override=True)
        def u(self) -> IUsersClient: return users

    container = make_async_container(AppProvider(), Fakes(), FastapiProvider())
    app = FastAPI()
    setup_dishka(container=container, app=app)
    app.include_router(root_router)
    app.include_router(me_router)
    for err in (Unauthorized, Forbidden, NotFoundError, ConflictError, ValidationError, UpstreamError):
        app.add_exception_handler(err, _domain_error_handler)
    return app, sched, users


def _auth(uid, email="org@x.io"):
    return {"Authorization": f"Bearer {create_access_token(get_settings(), user_id=uid, email=email)}"}


@pytest.mark.asyncio
async def test_schedule_uses_session_id(sessionmaker_fixture) -> None:
    from starlette.testclient import TestClient
    app, sched, _ = _app_and_fakes()
    uid = uuid4()
    with TestClient(app) as c:
        r = c.get("/api/me/schedule", headers=_auth(uid))
        assert r.status_code == 200
        assert sched.seen_owner == uid  # id from token, not request


@pytest.mark.asyncio
async def test_bookings_projection_hides_user_ids(sessionmaker_fixture) -> None:
    from starlette.testclient import TestClient
    app, _, _ = _app_and_fakes()
    with TestClient(app) as c:
        r = c.get("/api/me/bookings", headers=_auth(uuid4()))
        assert r.status_code == 200
        item = r.json()[0]
        assert set(item) == {"id", "start_time", "end_time", "status"}
        assert "client_user_id" not in item and "host_user_id" not in item


@pytest.mark.asyncio
async def test_profile_put_forwards_only_name_tz(sessionmaker_fixture) -> None:
    from starlette.testclient import TestClient
    app, _, users = _app_and_fakes()
    with TestClient(app) as c:
        r = c.put("/api/me/profile", headers=_auth(uuid4()), json={"name": "New", "time_zone": "Europe/Moscow"})
        assert r.status_code == 200
        assert users.patched == {"name": "New", "time_zone": "Europe/Moscow"}  # no email/role


@pytest.mark.asyncio
async def test_no_token_401(sessionmaker_fixture) -> None:
    from starlette.testclient import TestClient
    app, _, _ = _app_and_fakes()
    with TestClient(app) as c:
        assert c.get("/api/me/schedule").status_code == 401
```
> Add a password-change test: provision a credential in the DB (via CredentialAdapter, using PasswordService to hash "old"), mint a token for that user_id/email, `PUT /api/me/password {old,new}` → 204, then verify the stored hash now verifies "new" (via the adapter). Wrong old → 401. Keep the fakes for scheduling/users. Adjust for the settings-env (`POSTGRES_DSN`) as in Task 5.

- [ ] **Step 7: Run — verify PASS + full suite.** `cd event-organizer && TEST_POSTGRES_DSN=... uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green.

- [ ] **Step 8: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-organizer/event_organizer/schemas/me.py event-organizer/event_organizer/services/profile_service.py \
        event-organizer/event_organizer/services/password_change_service.py event-organizer/event_organizer/routers/me.py \
        event-organizer/event_organizer/ioc.py event-organizer/event_organizer/main.py event-organizer/tests/test_me_api.py
git commit -m "feat(organizer): /api/me schedule+bookings+profile+password (id from session) (slice 6.1)"
```

---

## Task 7: docker-compose + docs + final gate

**Files:**
- Modify: `docker-compose.services.yml`, root `CLAUDE.md`, `docs/architecture/ONBOARDING.md`, `docs/architecture/ARCHITECTURE.md`
- Create: `event-organizer/CLAUDE.md`

- [ ] **Step 1: Full gate.** `cd event-organizer && TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5601/event_organizer' uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green.

- [ ] **Step 2: `docker-compose.services.yml`** — add an `event-organizer` service (mirror the event-scheduling block; own DB on the shared postgres). Add its DB to the postgres init if the compose uses a multi-DB init (check how `event_scheduling`/`event_users` DBs get created — likely `docker/postgres-init/` or a `POSTGRES_MULTIPLE_DATABASES` env; add `event_organizer` there):
```yaml
  event-organizer:
    build:
      context: ./event-organizer
    environment:
      DEBUG: "false"
      LOG_LEVEL: ${LOG_LEVEL:-INFO}
      OTEL_SERVICE_NAME: event-organizer
      OTEL_SDK_DISABLED: ${OTEL_SDK_DISABLED:-true}
      OTEL_EXPORTER_OTLP_ENDPOINT: ${OTEL_EXPORTER_OTLP_ENDPOINT:-http://otel-collector:4317}
      OTEL_EXPORTER_OTLP_PROTOCOL: grpc
      POSTGRES_DSN: postgresql+asyncpg://${PG_ORGANIZER_USER:-event_organizer}:${PG_ORGANIZER_PASSWORD:-event_organizer}@postgres:5432/${PG_ORGANIZER_DB:-event_organizer}
      JWT_SECRET_KEY: ${ORGANIZER_JWT_SECRET:-dev-organizer-jwt-secret}
      ORGANIZER_ADMIN_KEY: ${ORGANIZER_ADMIN_KEY:-dev-organizer-admin-key}
      EVENT_SCHEDULING_URL: http://event-scheduling:8888
      SCHEDULING_API_KEY: ${SCHEDULING_API_KEY:-dev-scheduling-api-key-3f9c2e1a7b64d508}
      EVENT_USERS_URL: http://event-users:8888
      EVENT_USERS_TOKEN: ${USERS_API_BEARER_TOKEN:-dev-users-bearer-2a7d9e4f8c1b6350}
    ports:
      - "${ORGANIZER_PORT:-8006}:8888"
    depends_on:
      postgres:
        condition: service_healthy
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
> IMPORTANT: read how the other services' DBs are provisioned in the shared postgres (`docker/` init scripts or the postgres service's env listing `event_saver,event_users,...`). Add `event_organizer` to that list so the DB exists before the service's `alembic upgrade head` runs. Verify `docker compose config` parses and the SCHEDULING_API_KEY/EVENT_USERS_TOKEN defaults byte-match the event-scheduling block.

- [ ] **Step 3: Docs.**
  - `event-organizer/CLAUDE.md`: BFF role (organizer cabinet, password auth), the endpoints (`/auth/login`, `/admin/organizers`, `/api/me/schedule|bookings|profile|password`), ownership-by-construction (id from JWT, closes slice-5 IDOR), own DB/migration, commands, deferred (frontend 6.2, TOTP/reset/self-register, event-types/calendars are admin/other). Mirror `event-scheduling/CLAUDE.md` structure.
  - Root `CLAUDE.md`: add `event-organizer` to the services table + host-ports table (`8006 | event-organizer (organizer cabinet BFF)`), bump service count.
  - `docs/architecture/ONBOARDING.md` + `ARCHITECTURE.md`: new BFF; organizer JWT auth; `event-organizer` fronts event-scheduling/event-users with ownership by construction (explicitly note it closes the slice-5 calendar IDOR concern for organizer-facing access).
  - Every claim TRUE against the code Tasks 1–6 built (no event-types/calendars; id always from session).

- [ ] **Step 4: Re-run the gate.**

- [ ] **Step 5: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add docker-compose.services.yml CLAUDE.md docs/architecture/ONBOARDING.md docs/architecture/ARCHITECTURE.md \
        event-organizer/CLAUDE.md
git commit -m "docs(organizer): compose wiring + docs for event-organizer BFF (slice 6.1)"
```

---

## Self-Review (completed during plan authoring)

**1. Spec coverage:** §0/§1 architecture (BFF, password auth, own DB, ownership-by-construction) → Tasks 1–6. §2 migration → Task 1. §3 auth (bcrypt+JWT, login, require_organizer) → Tasks 2 + 5. §4 provisioning → Task 5. §5 /api/me (schedule GET/PUT/travel, bookings read, profile GET/PUT name+tz-only, password change) → Task 6. §6 error map + no-leaks → main.py handler (Task 1) + projections (Task 6). §7 tests → distributed. §8 deferred (frontend 6.2, TOTP/guard/reset/self-register, event-types/calendars) → noted, not built. §9 DoR + docker/docs → Tasks 1 (Docker) + 7 (compose/docs).

**2. Placeholders:** All app code complete. Boilerplate (telemetry/metrics/logger/sql/alembic env/password) is "copy from event-scheduling/event-admin, rename imports" against real files. Two flagged verify-at-impl notes (not placeholders): the `require_organizer` settings-injection style (Task 2 Step 6) and the test-harness settings-env (`POSTGRES_DSN`) + Dishka-override idiom (Tasks 5/6) — both with fixed assertions and a concrete fallback. Conftest DB fixtures are "mirror event-scheduling/tests/conftest.py" (real file).

**3. Type consistency:** `OrganizerIdentity{user_id,email}` (T2) consumed by require_organizer (T2) + all /api/me handlers (T6) + login/token (T5). `OrganizerCredentialDTO{id,user_id,email,password_hash,disabled}` (T3) used by CredentialAdapter (T3), LoginService (T5), PasswordChangeService (T6). `ICredentialAdapter`/`ISchedulingClient`/`IUsersClient` signatures (T3/T4) match adapters (T3/T4), services (T5/T6), ioc providers (T5/T6), and the test fakes (T5/T6). `create_access_token(settings,*,user_id,email)` / `decode_token(settings,token)` (T2) consistent across login (T5), require_organizer (T2), and the /api/me tests (T6). `PasswordService.hash/verify` (T2) consistent. Error types + `_STATUS` map (T1) identical in main.py and every test harness (T5/T6).
