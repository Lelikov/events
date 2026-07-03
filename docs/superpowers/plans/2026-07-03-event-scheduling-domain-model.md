# event-scheduling — доменная модель расписаний (срез 1) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Поднять новый сервис `event-scheduling` — владельца доменной модели расписаний (`schedule`/`weekly_hours`/`date_override`/`travel_schedule`/`event_type`/`host`/`booking_limit`/`schedule_change_log`) с CRUD, аудит-логом и одноразовым ETL из cal.com.

**Architecture:** Слоёный async FastAPI-сервис по образцу `event-shortener`: `routes → controllers → adapters/*_db → adapters/sql (SqlExecutor, raw text() SQL) → AsyncSession → PostgreSQL`. Своя БД `event_scheduling`, alembic-миграции, Dishka DI (APP/REQUEST scopes), pydantic-settings. Занятость абстрагирована `BusyTimesSource` Protocol со stub-реализацией под будущий движок слотов (срез 2).

**Tech Stack:** Python 3.14, FastAPI, Dishka, SQLAlchemy 2 (async, raw SQL), asyncpg, alembic, pydantic-settings, structlog, pytest + pytest-asyncio, Ruff, uv.

**Spec:** `docs/superpowers/specs/2026-07-03-event-scheduling-domain-model-design.md`
**Reference service (шаблон для копирования):** `/Users/alexandrlelikov/PycharmProjects/events/event-shortener/`

## Global Constraints

- **Python `>=3.14`**; зависимости через `uv`; lock — `uv.lock` (`uv sync`).
- **Ruff** line-length 120, target `py314`; `select = ["ALL"]` с ignore-набором из `event-shortener/pyproject.toml` (скопировать целиком).
- **Стиль кода: NO `elif`; avoid `else`** — early returns / guard clauses / mapping dicts.
- **Raw SQL только** через `SqlExecutor` (`:param` плейсхолдеры, не `?`); ORM-модели в `db/models.py` существуют ТОЛЬКО для alembic.
- **DTO** — frozen dataclasses; Pydantic — только в `schemas/` (request/response) с `to_dto()`/`from_dto()`.
- **PK** — `uuid` со `server_default=text("gen_random_uuid()")` (встроена в Postgres 16). Времена суток — `TIME`, даты — `DATE`, метки — `TIMESTAMPTZ`.
- **owner_user_id / host.user_id / actor_user_id** — непрозрачные `uuid`-ссылки на `event-users`, БЕЗ внешних ключей (кросс-сервис).
- **`day_of_week`**: `smallint` 1..7, 1=понедельник … 7=воскресенье (ISO-8601).
- **Все write — в одной транзакции** (Dishka REQUEST-scope session коммитит на успехе, откатывает на исключении). Аудит-снимок пишется в той же транзакции, что и сохранение.
- **Порт**: host `8004` → контейнер `8888`. **БД**: `event_scheduling` на общем postgres.
- **Внутренний периметр**: статический bearer-ключ `SCHEDULING_API_KEY` на всех `/api/*` (как `require_api_key` в шаблоне). Внешнего RBAC нет.

---

## File Structure

Новый каталог `/Users/alexandrlelikov/PycharmProjects/events/event-scheduling/`, зеркало `event-shortener`:

```
event-scheduling/
├── alembic.ini                     # копия шаблона (prepend_sys_path=.)
├── alembic/
│   ├── env.py                      # копия шаблона; читает Settings().postgres_dsn, target=Base.metadata
│   ├── script.py.mako              # копия шаблона
│   └── versions/
│       └── 0001_initial.py         # Task 2: все 8 таблиц
├── event_scheduling/
│   ├── __init__.py
│   ├── main.py                     # FastAPI app + Dishka + lifespan + exception handlers
│   ├── ioc.py                      # AppProvider: Settings/Engine/Session/SqlExecutor/adapters/controllers
│   ├── config.py                   # pydantic-settings Settings (+ scheduling_api_key)
│   ├── auth.py                     # require_api_key (копия шаблона, переименовать ключ)
│   ├── logger.py telemetry.py metrics.py  # копии шаблона (переименовать сервис)
│   ├── errors.py                   # ValidationError / NotFoundError / ConflictError
│   ├── validation.py               # чистые валидаторы (tz, weekly_hours, date_override, limits)
│   ├── routes.py                   # health/ready/metrics + include schedule_router, event_type_router
│   ├── adapters/
│   │   ├── sql.py                  # SqlExecutor (копия шаблона)
│   │   ├── schedule_db.py          # SQL расписания + аудит-снимок
│   │   └── event_type_db.py        # SQL event_type/host/booking_limit
│   ├── controllers/
│   │   ├── schedule.py             # ScheduleController (replace-all, snapshot)
│   │   └── event_type.py           # EventTypeController
│   ├── dto/
│   │   ├── schedule.py             # ScheduleDTO, WeeklyHourDTO, DateOverrideDTO, TravelDTO, ScheduleBundleDTO, ChangeLogEntryDTO
│   │   └── event_type.py           # EventTypeDTO, HostDTO, BookingLimitDTO
│   ├── schemas/
│   │   ├── schedule.py             # Pydantic req/resp + to_dto/from_dto
│   │   └── event_type.py
│   ├── interfaces/
│   │   ├── sql.py                  # ISqlExecutor (копия шаблона)
│   │   ├── schedule.py             # IScheduleDBAdapter, IScheduleController
│   │   ├── event_type.py           # IEventTypeDBAdapter, IEventTypeController
│   │   └── busy_times.py           # BusyTimesSource Protocol + TimeWindow/BusyInterval + StubBusyTimesSource
│   └── db/
│       ├── base.py                 # DeclarativeBase (копия шаблона)
│       └── models.py               # 8 ORM-моделей (только для alembic)
├── scripts/
│   ├── etl_mapping.py              # чистые функции маппинга cal.com → доменные DTO
│   └── etl_from_calcom.py          # оркестрация ETL + отчёт
├── tests/
│   ├── conftest.py                 # эфемерный Postgres + фикстуры (копия шаблона, TRUNCATE всех таблиц)
│   ├── test_health.py test_validation.py test_busy_times.py
│   ├── test_schedule_api.py test_event_type_api.py test_change_log.py
│   └── test_etl_mapping.py
├── Dockerfile entrypoint.sh uvicorn_config.json  # копии шаблона (переименовать пакет/сервис)
├── pyproject.toml uv.lock .env.example
├── .pre-commit-config.yaml .gitlab-ci.yml
└── CLAUDE.md README.md docs/{SERVICE_OVERVIEW,API_CONTRACTS,DATA_MODEL,DEPENDENCIES,AUDIT}.md
```

Файлы, помеченные «копия шаблона», копируются **байт-в-байт** из одноимённого файла `event-shortener/` с заменой `event_shortener`→`event_scheduling`, `event-shortener`→`event-scheduling`, `SHORTENER_API_KEY`→`SCHEDULING_API_KEY`.

---

## Task 1: Scaffold service — boots, /health зелёный, поднимается в compose

**Files:**
- Create: весь каркас `event-scheduling/` кроме доменных таблиц/CRUD (см. ниже точный список)
- Modify: `docker-compose.services.yml`, `docker-compose.infra.yml`, `docker/postgres-init/00-init-databases.sh`, корневой `CLAUDE.md`
- Test: `event-scheduling/tests/test_health.py`, `event-scheduling/tests/conftest.py`

**Interfaces:**
- Produces: рабочий FastAPI-app `event_scheduling.main:app` с `GET /health`→`{"status":"ok"}`; Dishka `AppProvider` дающий `Settings`, `AsyncEngine`, `async_sessionmaker`, `AsyncSession` (REQUEST), `ISqlExecutor`; `config.Settings.postgres_dsn`/`scheduling_api_key`; `errors.{ValidationError,NotFoundError,ConflictError}`; `adapters.sql.SqlExecutor`; `interfaces.sql.ISqlExecutor`.

- [ ] **Step 1: Скопировать инфраструктурные файлы шаблона**

Скопировать из `event-shortener/` в `event-scheduling/` и заменить строки `event_shortener`→`event_scheduling`, `event-shortener`→`event-scheduling`, `SHORTENER_API_KEY`→`SCHEDULING_API_KEY`, `shortener_api_key`→`scheduling_api_key`:

```
pyproject.toml  .pre-commit-config.yaml  .gitlab-ci.yml  uvicorn_config.json
Dockerfile  entrypoint.sh
alembic.ini  alembic/env.py  alembic/script.py.mako
event_scheduling/__init__.py  logger.py  telemetry.py  metrics.py  auth.py
event_scheduling/adapters/sql.py  event_scheduling/interfaces/sql.py
event_scheduling/db/base.py
```

В `pyproject.toml`: `name = "event-scheduling"`, `description = "Ownership of organizer schedules, event types and hosts (booking domain model)"`. Удалить из `dependencies` строку `ujson` только если не используется; остальное оставить как в шаблоне.

`entrypoint.sh` — заменить последнюю строку на:
```bash
exec uvicorn event_scheduling.main:app --host 0.0.0.0 --port 8888 --log-config uvicorn_config.json
```

- [ ] **Step 2: `event_scheduling/config.py`**

```python
from functools import lru_cache

from pydantic import Field, PostgresDsn, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    debug: bool = False
    log_level: str = "INFO"

    # asyncpg URL, e.g. postgresql+asyncpg://event_scheduling:event_scheduling@postgres:5432/event_scheduling
    postgres_dsn: PostgresDsn = Field(strict=True)

    # Static bearer key gating every /api/* route (constant-time compared in auth.py).
    scheduling_api_key: str = Field(...)

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        upper = v.upper()
        if upper not in valid_levels:
            raise ValueError(f"Invalid log_level: {v!r}. Must be one of {sorted(valid_levels)}")
        return upper


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 3: `event_scheduling/errors.py`**

```python
class DomainError(Exception):
    """Base for domain errors mapped to HTTP status codes in main.py."""


class ValidationError(DomainError):
    """Invalid input — mapped to HTTP 422."""


class NotFoundError(DomainError):
    """Missing aggregate — mapped to HTTP 404."""


class ConflictError(DomainError):
    """Uniqueness / state conflict — mapped to HTTP 409."""
```

- [ ] **Step 4: `event_scheduling/ioc.py`** (по образцу шаблона, пока только инфраструктура + SqlExecutor; адаптеры/контроллеры добавят Task 4/9)

```python
from collections.abc import AsyncGenerator

import structlog
from dishka import Provider, Scope, provide
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.config import Settings, get_settings
from event_scheduling.interfaces.sql import ISqlExecutor


logger = structlog.get_logger(__name__)


class AppProvider(Provider):
    @provide(scope=Scope.APP)
    def provide_settings(self) -> Settings:
        settings = get_settings()
        logger.info("Settings initialized", debug=settings.debug, log_level=settings.log_level)
        return settings

    @provide(scope=Scope.APP)
    async def provide_db_engine(self, settings: Settings) -> AsyncGenerator[AsyncEngine]:
        engine = create_async_engine(str(settings.postgres_dsn), pool_size=10, max_overflow=20, pool_pre_ping=True)
        try:
            yield engine
        finally:
            await engine.dispose()

    @provide(scope=Scope.APP)
    def provide_sessionmaker(self, engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
        return async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    @provide(scope=Scope.REQUEST)
    async def provide_session(self, sessionmaker: async_sessionmaker[AsyncSession]) -> AsyncGenerator[AsyncSession]:
        async with sessionmaker() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    @provide(scope=Scope.REQUEST)
    def provide_sql_executor(self, session: AsyncSession) -> ISqlExecutor:
        return SqlExecutor(session)
```

- [ ] **Step 5: `event_scheduling/routes.py`** (ops-эндпоинты; доменные роутеры включатся позже)

```python
from dishka.integrations.fastapi import DishkaRoute
from fastapi import APIRouter
from starlette.responses import PlainTextResponse

from event_scheduling import metrics

root_router = APIRouter(route_class=DishkaRoute)


@root_router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@root_router.get("/ready")
async def ready() -> dict[str, str]:
    return {"status": "ready"}


@root_router.get("/metrics")
async def metrics_endpoint() -> PlainTextResponse:
    return PlainTextResponse(metrics.render(), media_type=metrics.CONTENT_TYPE)
```
> Если `metrics.render()`/`CONTENT_TYPE` в шаблоне называются иначе — использовать точные имена из скопированного `metrics.py`.

- [ ] **Step 6: `event_scheduling/main.py`** (app + exception handlers, транслирующие доменные ошибки)

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from logging import getLevelNamesMapping

import structlog
from dishka import make_async_container
from dishka.integrations.fastapi import FastapiProvider, setup_dishka
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from event_scheduling.config import Settings
from event_scheduling.errors import ConflictError, NotFoundError, ValidationError
from event_scheduling.ioc import AppProvider
from event_scheduling.logger import setup_logger
from event_scheduling.metrics import HttpMetricsMiddleware
from event_scheduling.routes import root_router
from event_scheduling.telemetry import instrument_asyncpg, instrument_fastapi, setup_tracing

container = make_async_container(AppProvider(), FastapiProvider())
logger = structlog.get_logger(__name__)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncGenerator[None]:
    settings = await container.get(Settings)
    setup_logger(log_level=getLevelNamesMapping().get(settings.log_level), console_render=settings.debug)
    logger.info("Starting event-scheduling", log_level=settings.log_level, debug=settings.debug)
    yield
    await container.close()
    logger.info("event-scheduling shutdown complete")


app = FastAPI(title="event-scheduling", version="0.1.0", lifespan=lifespan)
setup_tracing()
instrument_fastapi(app)
instrument_asyncpg()
setup_dishka(container=container, app=app)
app.include_router(root_router)
app.add_middleware(HttpMetricsMiddleware)

_STATUS = {ValidationError: 422, NotFoundError: 404, ConflictError: 409}


async def _domain_error_handler(_: Request, exc: Exception) -> JSONResponse:
    status = next((code for typ, code in _STATUS.items() if isinstance(exc, typ)), 500)
    return JSONResponse(status_code=status, content={"detail": str(exc)})


for _err in (ValidationError, NotFoundError, ConflictError):
    app.add_exception_handler(_err, _domain_error_handler)
```

- [ ] **Step 7: Написать падающий smoke-тест `tests/test_health.py` + скопировать `tests/conftest.py`**

Скопировать `event-shortener/tests/conftest.py` → `event-scheduling/tests/conftest.py`. Заменить `event_shortener`→`event_scheduling`, `root_router` include оставить, а `TRUNCATE short_urls RESTART IDENTITY` заменить на (Task 2 добавит остальные, пока — заглушка, безопасная на пустой схеме):
```python
await conn.execute(text(
    "TRUNCATE schedule, weekly_hours, date_override, travel_schedule, "
    "event_type, host, booking_limit, schedule_change_log RESTART IDENTITY CASCADE"
))
```
> На этом шаге таблиц ещё нет — Task 2 создаёт их и делает TRUNCATE валидным. Чтобы Task 1 прошёл независимо, в conftest временно оберни TRUNCATE в try/except или (проще) закоммить Task 1 и Task 2 подряд. Рекомендуется: в Task 1 conftest делает `TRUNCATE` в блоке `try: ... except Exception: pass`, а Task 2 убирает try/except.

`tests/test_health.py`:
```python
def test_health_ok(client) -> None:
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}
```

- [ ] **Step 8: Прогнать тест — убедиться, что падает**

Run: `cd event-scheduling && uv sync && TEST_POSTGRES_DSN=... uv run pytest tests/test_health.py -v`
Expected: FAIL (нет `metrics`/`logger`/… или app не собирается) — фиксируем недостающие копии, пока тест не станет исполняться и падать по существу, затем зелёный после Step 1–6.

- [ ] **Step 9: Прогнать тест — зелёный**

Run: `cd event-scheduling && uv run pytest tests/test_health.py -v`
Expected: PASS.

- [ ] **Step 10: Docker-compose + провизия БД**

`docker/postgres-init/00-init-databases.sh` — после строки для shortener добавить:
```bash
create_db_role "${PG_SCHEDULING_DB:-event_scheduling}" "${PG_SCHEDULING_USER:-event_scheduling}" "${PG_SCHEDULING_PASSWORD:-event_scheduling}"
```

`docker-compose.infra.yml` — в `postgres.environment` добавить:
```yaml
PG_SCHEDULING_USER: ${PG_SCHEDULING_USER:-event_scheduling}
PG_SCHEDULING_PASSWORD: ${PG_SCHEDULING_PASSWORD:-event_scheduling}
PG_SCHEDULING_DB: ${PG_SCHEDULING_DB:-event_scheduling}
```

`docker-compose.services.yml` — новый сервис (порт 8004):
```yaml
event-scheduling:
  build:
    context: ./event-scheduling
  environment:
    DEBUG: "false"
    LOG_LEVEL: ${LOG_LEVEL:-INFO}
    OTEL_SERVICE_NAME: event-scheduling
    OTEL_SDK_DISABLED: ${OTEL_SDK_DISABLED:-true}
    OTEL_EXPORTER_OTLP_ENDPOINT: ${OTEL_EXPORTER_OTLP_ENDPOINT:-http://otel-collector:4317}
    OTEL_EXPORTER_OTLP_PROTOCOL: grpc
    OTEL_TRACES_SAMPLER: ${OTEL_TRACES_SAMPLER:-parentbased_always_on}
    POSTGRES_DSN: postgresql+asyncpg://${PG_SCHEDULING_USER:-event_scheduling}:${PG_SCHEDULING_PASSWORD:-event_scheduling}@postgres:5432/${PG_SCHEDULING_DB:-event_scheduling}
    SCHEDULING_API_KEY: ${SCHEDULING_API_KEY:-dev-scheduling-api-key-3f9c2e1a7b64d508}
  ports:
    - "${SCHEDULING_PORT:-8004}:8888"
  depends_on:
    postgres:
      condition: service_healthy
  healthcheck:
    test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8888/health', timeout=5)\""]
    interval: 10s
    timeout: 10s
    retries: 10
    start_period: 20s
  restart: unless-stopped
```

Корневой `CLAUDE.md` — добавить `event-scheduling/` в таблицу сервисов и `8004` в таблицу портов.

- [ ] **Step 11: Проверить сборку и boot в compose**

Run: `docker compose build event-scheduling && docker compose up -d postgres event-scheduling && sleep 20 && curl -fsS localhost:8004/health`
Expected: `{"status":"ok"}`; в логах — успешный `alembic upgrade head` (пока миграций нет — «no migrations», это ок).

- [ ] **Step 12: Commit**

```bash
git add event-scheduling docker-compose.services.yml docker-compose.infra.yml docker/postgres-init/00-init-databases.sh CLAUDE.md
git commit -m "feat(scheduling): scaffold event-scheduling service (health, DI, compose, DB)"
```

---

## Task 2: Initial schema — 8 таблиц (миграция + ORM-модели)

**Files:**
- Create: `event-scheduling/event_scheduling/db/models.py`, `event-scheduling/alembic/versions/0001_initial.py`
- Modify: `event-scheduling/tests/conftest.py` (убрать try/except вокруг TRUNCATE)
- Test: `event-scheduling/tests/test_schema.py`

**Interfaces:**
- Produces: таблицы `schedule, weekly_hours, date_override, travel_schedule, event_type, host, booking_limit, schedule_change_log` с колонками из спека §2; ORM-модели `Schedule, WeeklyHour, DateOverride, TravelSchedule, EventType, Host, BookingLimit, ScheduleChangeLog` в `db.models`.

- [ ] **Step 1: Падающий тест `tests/test_schema.py`**

```python
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

EXPECTED_TABLES = {
    "schedule", "weekly_hours", "date_override", "travel_schedule",
    "event_type", "host", "booking_limit", "schedule_change_log",
}


@pytest.mark.asyncio
async def test_all_tables_exist(_migrated: str) -> None:
    eng = create_async_engine(_migrated)
    async with eng.connect() as conn:
        rows = await conn.execute(text(
            "SELECT tablename FROM pg_tables WHERE schemaname = 'public'"
        ))
        tables = {r[0] for r in rows}
    await eng.dispose()
    assert EXPECTED_TABLES <= tables


@pytest.mark.asyncio
async def test_day_of_week_check_rejects_zero(_migrated: str) -> None:
    eng = create_async_engine(_migrated)
    async with eng.begin() as conn:
        sched = (await conn.execute(text(
            "INSERT INTO schedule (owner_user_id, name, time_zone) "
            "VALUES (gen_random_uuid(), 'x', 'Europe/Moscow') RETURNING id"
        ))).scalar()
        with pytest.raises(Exception):  # noqa: B017 - CheckViolation
            await conn.execute(text(
                "INSERT INTO weekly_hours (schedule_id, day_of_week, start_time, end_time) "
                "VALUES (:s, 0, '09:00', '17:00')"
            ), {"s": sched})
    await eng.dispose()
```

- [ ] **Step 2: Прогнать — падает**

Run: `cd event-scheduling && uv run pytest tests/test_schema.py -v`
Expected: FAIL (таблиц нет).

- [ ] **Step 3: `event_scheduling/db/models.py`** (только для alembic)

```python
from datetime import date, datetime, time

from sqlalchemy import (
    CheckConstraint, Date, DateTime, ForeignKey, Integer, SmallInteger,
    Text, Time, UniqueConstraint, text,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from event_scheduling.db.base import Base

_UUID_PK = mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"))


class Schedule(Base):
    __tablename__ = "schedule"
    id: Mapped[str] = _UUID_PK
    owner_user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    name: Mapped[str] = mapped_column(Text, nullable=False)
    time_zone: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    __table_args__ = (UniqueConstraint("owner_user_id", name="uq_schedule_owner"),)


class WeeklyHour(Base):
    __tablename__ = "weekly_hours"
    id: Mapped[str] = _UUID_PK
    schedule_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("schedule.id", ondelete="CASCADE"), nullable=False)
    day_of_week: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    start_time: Mapped[time] = mapped_column(Time, nullable=False)
    end_time: Mapped[time] = mapped_column(Time, nullable=False)
    __table_args__ = (
        CheckConstraint("day_of_week BETWEEN 1 AND 7", name="ck_weekly_hours_dow"),
        CheckConstraint("end_time > start_time", name="ck_weekly_hours_range"),
    )


class DateOverride(Base):
    __tablename__ = "date_override"
    id: Mapped[str] = _UUID_PK
    schedule_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("schedule.id", ondelete="CASCADE"), nullable=False)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    end_time: Mapped[time | None] = mapped_column(Time, nullable=True)
    __table_args__ = (
        CheckConstraint(
            "(start_time IS NULL AND end_time IS NULL) OR "
            "(start_time IS NOT NULL AND end_time IS NOT NULL AND end_time > start_time)",
            name="ck_date_override_range",
        ),
    )


class TravelSchedule(Base):
    __tablename__ = "travel_schedule"
    id: Mapped[str] = _UUID_PK
    schedule_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("schedule.id", ondelete="CASCADE"), nullable=False)
    time_zone: Mapped[str] = mapped_column(Text, nullable=False)
    start_date: Mapped[date] = mapped_column(Date, nullable=False)
    end_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    prev_time_zone: Mapped[str | None] = mapped_column(Text, nullable=True)


class EventType(Base):
    __tablename__ = "event_type"
    id: Mapped[str] = _UUID_PK
    slug: Mapped[str] = mapped_column(Text, nullable=False)
    title: Mapped[str] = mapped_column(Text, nullable=False)
    scheduling_type: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'round_robin'"))
    duration_minutes: Mapped[int] = mapped_column(Integer, nullable=False)
    slot_interval_minutes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    min_booking_notice_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    buffer_before_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    buffer_after_minutes: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    __table_args__ = (UniqueConstraint("slug", name="uq_event_type_slug"),)


class Host(Base):
    __tablename__ = "host"
    event_type_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("event_type.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), primary_key=True)
    schedule_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("schedule.id", ondelete="RESTRICT"), nullable=False)


class BookingLimit(Base):
    __tablename__ = "booking_limit"
    id: Mapped[str] = _UUID_PK
    event_type_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("event_type.id", ondelete="CASCADE"), nullable=False)
    limit_type: Mapped[str] = mapped_column(Text, nullable=False)
    period: Mapped[str] = mapped_column(Text, nullable=False)
    value: Mapped[int] = mapped_column(Integer, nullable=False)
    __table_args__ = (
        CheckConstraint("value > 0", name="ck_booking_limit_value"),
        UniqueConstraint("event_type_id", "limit_type", "period", name="uq_booking_limit"),
    )


class ScheduleChangeLog(Base):
    __tablename__ = "schedule_change_log"
    id: Mapped[str] = _UUID_PK
    owner_user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    schedule_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)  # no FK: audit survives delete
    actor_source: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
```

- [ ] **Step 4: Миграция `alembic/versions/0001_initial.py`**

Заголовок как в шаблоне (`revision="0001"`, `down_revision=None`). В `upgrade()` создать все 8 таблиц в порядке зависимостей (`schedule`→дети; `event_type`→`host`/`booking_limit`; `schedule_change_log`), повторяя колонки/констрейнты из моделей. Пример первой таблицы (остальные — по тем же колонкам из Step 3):

```python
from collections.abc import Sequence
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels = None
depends_on = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "schedule",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("owner_user_id", _UUID, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("time_zone", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("owner_user_id", name="uq_schedule_owner"),
    )
    # weekly_hours, date_override, travel_schedule, event_type, host, booking_limit, schedule_change_log
    # — по колонкам и CheckConstraint/UniqueConstraint из db/models.py (Step 3).
    ...  # ЗАПОЛНИТЬ все 8 таблиц; НЕ оставлять многоточие в реальном файле


def downgrade() -> None:
    for tbl in (
        "schedule_change_log", "booking_limit", "host", "event_type",
        "travel_schedule", "date_override", "weekly_hours", "schedule",
    ):
        op.drop_table(tbl)
```
> В реальном файле раскрыть все 8 `op.create_table` полностью — без многоточий (это plan-failure). Колонки брать 1:1 из `db/models.py`.

- [ ] **Step 5: Убрать try/except вокруг TRUNCATE в `tests/conftest.py`** — теперь таблицы есть.

- [ ] **Step 6: Прогнать — зелёный**

Run: `cd event-scheduling && uv run pytest tests/test_schema.py tests/test_health.py -v`
Expected: PASS (обе таблицы существуют; day_of_week=0 отвергается CheckConstraint).

- [ ] **Step 7: Commit**

```bash
git add event-scheduling/event_scheduling/db/models.py event-scheduling/alembic/versions/0001_initial.py event-scheduling/tests
git commit -m "feat(scheduling): initial schema — 8 tables + ORM models + migration"
```

---

## Task 3: Валидаторы (чистые функции) + BusyTimesSource seam

**Files:**
- Create: `event_scheduling/validation.py`, `event_scheduling/interfaces/busy_times.py`
- Test: `tests/test_validation.py`, `tests/test_busy_times.py`

**Interfaces:**
- Produces:
  - `validation.validate_time_zone(tz: str) -> None` (raises `ValidationError`)
  - `validation.validate_weekly_hours(rows: list[WeeklyHourDTO]) -> None`
  - `validation.validate_date_overrides(rows: list[DateOverrideDTO]) -> None`
  - `validation.validate_booking_limits(rows: list[BookingLimitDTO]) -> None`
  - `interfaces.busy_times.TimeWindow` (frozen dc: `start: datetime, end: datetime`), `BusyInterval` (frozen dc: `start: datetime, end: datetime`), `BusyTimesSource` (Protocol с `async def get_busy(self, user_ids: Sequence[UUID], window: TimeWindow) -> list[BusyInterval]`), `StubBusyTimesSource` (возвращает `[]`).
- Consumes: DTO из `dto.schedule`/`dto.event_type` — этот таск создаёт минимальные их версии, если Task 4/9 ещё не выполнены (см. Step 1).

- [ ] **Step 1: Создать DTO, нужные валидаторам** (в `dto/schedule.py` и `dto/event_type.py`)

```python
# dto/schedule.py
from dataclasses import dataclass
from datetime import date, time


@dataclass(frozen=True)
class WeeklyHourDTO:
    day_of_week: int
    start_time: time
    end_time: time


@dataclass(frozen=True)
class DateOverrideDTO:
    date: date
    start_time: time | None
    end_time: time | None
```
```python
# dto/event_type.py
from dataclasses import dataclass


@dataclass(frozen=True)
class BookingLimitDTO:
    limit_type: str
    period: str
    value: int
```

- [ ] **Step 2: Падающий тест `tests/test_validation.py`**

```python
import datetime as dt

import pytest

from event_scheduling.dto.event_type import BookingLimitDTO
from event_scheduling.dto.schedule import DateOverrideDTO, WeeklyHourDTO
from event_scheduling.errors import ValidationError
from event_scheduling.validation import (
    validate_booking_limits, validate_date_overrides, validate_time_zone, validate_weekly_hours,
)


def test_time_zone_valid_and_invalid() -> None:
    validate_time_zone("Europe/Moscow")  # no raise
    with pytest.raises(ValidationError):
        validate_time_zone("Mars/Phobos")


def test_weekly_hours_rejects_bad_day_and_range() -> None:
    with pytest.raises(ValidationError):
        validate_weekly_hours([WeeklyHourDTO(0, dt.time(9), dt.time(17))])
    with pytest.raises(ValidationError):
        validate_weekly_hours([WeeklyHourDTO(1, dt.time(17), dt.time(9))])
    validate_weekly_hours([WeeklyHourDTO(1, dt.time(9), dt.time(17))])  # ok


def test_date_override_null_invariant() -> None:
    validate_date_overrides([DateOverrideDTO(dt.date(2026, 1, 1), None, None)])  # day off ok
    validate_date_overrides([DateOverrideDTO(dt.date(2026, 1, 1), dt.time(9), dt.time(12))])  # window ok
    with pytest.raises(ValidationError):
        validate_date_overrides([DateOverrideDTO(dt.date(2026, 1, 1), dt.time(9), None)])  # mixed
    with pytest.raises(ValidationError):
        validate_date_overrides([DateOverrideDTO(dt.date(2026, 1, 1), dt.time(12), dt.time(9))])  # end<start


def test_booking_limits_validation() -> None:
    validate_booking_limits([BookingLimitDTO("booking_count", "day", 3)])  # ok
    with pytest.raises(ValidationError):
        validate_booking_limits([BookingLimitDTO("booking_count", "day", 0)])  # value>0
    with pytest.raises(ValidationError):
        validate_booking_limits([BookingLimitDTO("nope", "day", 1)])  # bad type
    with pytest.raises(ValidationError):
        validate_booking_limits([BookingLimitDTO("booking_count", "decade", 1)])  # bad period
```

- [ ] **Step 3: Прогнать — падает**

Run: `cd event-scheduling && uv run pytest tests/test_validation.py -v`
Expected: FAIL (нет `validation`).

- [ ] **Step 4: `event_scheduling/validation.py`**

```python
from collections.abc import Sequence
from zoneinfo import ZoneInfo, available_timezones

from event_scheduling.dto.event_type import BookingLimitDTO
from event_scheduling.dto.schedule import DateOverrideDTO, WeeklyHourDTO
from event_scheduling.errors import ValidationError

_LIMIT_TYPES = {"booking_count", "booking_duration"}
_PERIODS = {"day", "week", "month", "year"}
_TZ_NAMES = available_timezones()


def validate_time_zone(tz: str) -> None:
    if tz not in _TZ_NAMES:
        raise ValidationError(f"Unknown time zone: {tz!r}")
    ZoneInfo(tz)  # cheap sanity, raises if the tzdata is unusable


def validate_weekly_hours(rows: Sequence[WeeklyHourDTO]) -> None:
    for r in rows:
        if not 1 <= r.day_of_week <= 7:
            raise ValidationError(f"day_of_week must be 1..7, got {r.day_of_week}")
        if r.end_time <= r.start_time:
            raise ValidationError(f"weekly_hours end_time must be > start_time (day {r.day_of_week})")


def validate_date_overrides(rows: Sequence[DateOverrideDTO]) -> None:
    for r in rows:
        both_null = r.start_time is None and r.end_time is None
        both_set = r.start_time is not None and r.end_time is not None
        if not (both_null or both_set):
            raise ValidationError(f"date_override {r.date}: start/end must both be null or both set")
        if both_set and r.end_time <= r.start_time:
            raise ValidationError(f"date_override {r.date}: end_time must be > start_time")


def validate_booking_limits(rows: Sequence[BookingLimitDTO]) -> None:
    for r in rows:
        if r.limit_type not in _LIMIT_TYPES:
            raise ValidationError(f"bad limit_type: {r.limit_type!r}")
        if r.period not in _PERIODS:
            raise ValidationError(f"bad period: {r.period!r}")
        if r.value <= 0:
            raise ValidationError("booking_limit value must be > 0")
```

- [ ] **Step 5: `event_scheduling/interfaces/busy_times.py`**

```python
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID


@dataclass(frozen=True)
class TimeWindow:
    start: datetime
    end: datetime


@dataclass(frozen=True)
class BusyInterval:
    start: datetime
    end: datetime


class BusyTimesSource(Protocol):
    async def get_busy(self, user_ids: Sequence[UUID], window: TimeWindow) -> list[BusyInterval]: ...


class StubBusyTimesSource:
    """Slice-1 placeholder — no busy times until slice 3 backs this with the `booking` table."""

    async def get_busy(self, user_ids: Sequence[UUID], window: TimeWindow) -> list[BusyInterval]:
        return []
```

- [ ] **Step 6: Тест `tests/test_busy_times.py`**

```python
import datetime as dt
from uuid import uuid4

import pytest

from event_scheduling.interfaces.busy_times import StubBusyTimesSource, TimeWindow


@pytest.mark.asyncio
async def test_stub_returns_empty() -> None:
    src = StubBusyTimesSource()
    window = TimeWindow(dt.datetime(2026, 1, 1), dt.datetime(2026, 1, 2))
    assert await src.get_busy([uuid4()], window) == []
```

- [ ] **Step 7: Прогнать — зелёный**

Run: `cd event-scheduling && uv run pytest tests/test_validation.py tests/test_busy_times.py -v`
Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add event-scheduling/event_scheduling/validation.py event-scheduling/event_scheduling/interfaces/busy_times.py event-scheduling/event_scheduling/dto event-scheduling/tests/test_validation.py event-scheduling/tests/test_busy_times.py
git commit -m "feat(scheduling): schedule/limit validators + BusyTimesSource seam"
```

---

## Task 4: Schedule PUT — replace-all + аудит-снимок (в одной транзакции)

**Files:**
- Create: `dto/schedule.py` (дополнить), `schemas/schedule.py`, `interfaces/schedule.py`, `adapters/schedule_db.py`, `controllers/schedule.py`
- Modify: `event_scheduling/ioc.py` (провайдеры адаптера/контроллера), `event_scheduling/routes.py` (include schedule_router)
- Test: `tests/test_schedule_api.py`

**Interfaces:**
- Consumes: `ISqlExecutor`; валидаторы Task 3; `errors.*`.
- Produces:
  - DTO: `ScheduleDTO(id, owner_user_id, name, time_zone)`, `TravelDTO(time_zone, start_date, end_date, prev_time_zone)`, `ScheduleBundleDTO(schedule: ScheduleDTO, weekly_hours: list[WeeklyHourDTO], date_overrides: list[DateOverrideDTO], travel_schedules: list[TravelDTO])`, `UpsertScheduleDTO(name, time_zone, weekly_hours, date_overrides)`, `ActorDTO(source: str, user_id: UUID | None)`.
  - `IScheduleController.upsert_schedule(owner_user_id: UUID, dto: UpsertScheduleDTO, actor: ActorDTO) -> ScheduleBundleDTO`
  - `IScheduleDBAdapter.replace_schedule(owner_user_id, dto) -> ScheduleBundleDTO`, `.append_change_log(owner_user_id, schedule_id, actor, snapshot: dict) -> None`, `.get_bundle(owner_user_id) -> ScheduleBundleDTO | None`
  - Route `PUT /api/v1/schedules/{owner_user_id}` → 200 с bundle.

- [ ] **Step 1: Падающий тест `tests/test_schedule_api.py`**

```python
from uuid import uuid4

OWNER = str(uuid4())
HDRS = {"actor-source": "admin"}


def _bundle() -> dict:
    return {
        "name": "Консультации",
        "time_zone": "Europe/Moscow",
        "weekly_hours": [
            {"day_of_week": 1, "start_time": "09:00", "end_time": "17:00"},
            {"day_of_week": 2, "start_time": "09:00", "end_time": "13:00"},
        ],
        "date_overrides": [
            {"date": "2026-01-07", "start_time": None, "end_time": None},
            {"date": "2026-01-08", "start_time": "10:00", "end_time": "12:00"},
        ],
    }


def test_put_creates_schedule_and_returns_bundle(client) -> None:
    resp = client.put(f"/api/v1/schedules/{OWNER}", json=_bundle(), headers=HDRS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["schedule"]["time_zone"] == "Europe/Moscow"
    assert len(body["weekly_hours"]) == 2
    assert len(body["date_overrides"]) == 2


def test_put_is_replace_all(client) -> None:
    client.put(f"/api/v1/schedules/{OWNER}", json=_bundle(), headers=HDRS)
    smaller = _bundle()
    smaller["weekly_hours"] = [{"day_of_week": 3, "start_time": "08:00", "end_time": "10:00"}]
    smaller["date_overrides"] = []
    resp = client.put(f"/api/v1/schedules/{OWNER}", json=smaller, headers=HDRS)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["weekly_hours"]) == 1
    assert body["weekly_hours"][0]["day_of_week"] == 3
    assert body["date_overrides"] == []


def test_put_rejects_bad_timezone(client) -> None:
    bad = _bundle()
    bad["time_zone"] = "Mars/Phobos"
    resp = client.put(f"/api/v1/schedules/{OWNER}", json=bad, headers=HDRS)
    assert resp.status_code == 422
```
> `client` — аутентифицированный фикстур-клиент (в `conftest.py` уже добавляет `Authorization: Bearer <API_KEY>`; при копировании conftest заменить константу `API_KEY` на `dev-scheduling-api-key-3f9c2e1a7b64d508`).

- [ ] **Step 2: Прогнать — падает**

Run: `cd event-scheduling && uv run pytest tests/test_schedule_api.py -v`
Expected: FAIL (404/нет роутера).

- [ ] **Step 3: Дополнить `dto/schedule.py`**

```python
from dataclasses import dataclass
from datetime import date, time
from uuid import UUID


@dataclass(frozen=True)
class ScheduleDTO:
    id: UUID
    owner_user_id: UUID
    name: str
    time_zone: str


@dataclass(frozen=True)
class TravelDTO:
    time_zone: str
    start_date: date
    end_date: date | None
    prev_time_zone: str | None


@dataclass(frozen=True)
class ScheduleBundleDTO:
    schedule: ScheduleDTO
    weekly_hours: list["WeeklyHourDTO"]
    date_overrides: list["DateOverrideDTO"]
    travel_schedules: list[TravelDTO]


@dataclass(frozen=True)
class UpsertScheduleDTO:
    name: str
    time_zone: str
    weekly_hours: list["WeeklyHourDTO"]
    date_overrides: list["DateOverrideDTO"]


@dataclass(frozen=True)
class ActorDTO:
    source: str
    user_id: UUID | None
```
> `WeeklyHourDTO`/`DateOverrideDTO` уже определены в Task 3 (в этом же файле).

- [ ] **Step 4: `interfaces/schedule.py`**

```python
from typing import Protocol
from uuid import UUID

from event_scheduling.dto.schedule import ActorDTO, ScheduleBundleDTO, UpsertScheduleDTO


class IScheduleDBAdapter(Protocol):
    async def get_bundle(self, owner_user_id: UUID) -> ScheduleBundleDTO | None: ...
    async def replace_schedule(self, owner_user_id: UUID, dto: UpsertScheduleDTO) -> ScheduleBundleDTO: ...
    async def append_change_log(self, owner_user_id: UUID, schedule_id: UUID, actor: ActorDTO, snapshot: dict) -> None: ...


class IScheduleController(Protocol):
    async def get_schedule(self, owner_user_id: UUID) -> ScheduleBundleDTO: ...
    async def upsert_schedule(self, owner_user_id: UUID, dto: UpsertScheduleDTO, actor: ActorDTO) -> ScheduleBundleDTO: ...
```

- [ ] **Step 5: `adapters/schedule_db.py`**

Реализация: `replace_schedule` делает upsert `schedule` (INSERT ... ON CONFLICT(owner_user_id) DO UPDATE SET name/time_zone/updated_at RETURNING id), затем `DELETE FROM weekly_hours WHERE schedule_id=:sid`, `DELETE FROM date_override WHERE schedule_id=:sid`, `INSERT` новых строк, затем `get_bundle`. `append_change_log` — один INSERT в `schedule_change_log`. Всё через `self._sql` (общая session/транзакция).

```python
import json
from uuid import UUID

from event_scheduling.dto.schedule import (
    ActorDTO, DateOverrideDTO, ScheduleBundleDTO, ScheduleDTO, TravelDTO, UpsertScheduleDTO, WeeklyHourDTO,
)
from event_scheduling.interfaces.sql import ISqlExecutor


class ScheduleDBAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def _upsert_schedule_row(self, owner_user_id: UUID, name: str, time_zone: str) -> UUID:
        row = await self._sql.fetch_one(
            """
            INSERT INTO schedule (owner_user_id, name, time_zone)
            VALUES (:owner, :name, :tz)
            ON CONFLICT (owner_user_id)
            DO UPDATE SET name = EXCLUDED.name, time_zone = EXCLUDED.time_zone, updated_at = now()
            RETURNING id
            """,
            {"owner": owner_user_id, "name": name, "tz": time_zone},
        )
        return row["id"]

    async def replace_schedule(self, owner_user_id: UUID, dto: UpsertScheduleDTO) -> ScheduleBundleDTO:
        sid = await self._upsert_schedule_row(owner_user_id, dto.name, dto.time_zone)
        await self._sql.execute("DELETE FROM weekly_hours WHERE schedule_id = :sid", {"sid": sid})
        await self._sql.execute("DELETE FROM date_override WHERE schedule_id = :sid", {"sid": sid})
        for w in dto.weekly_hours:
            await self._sql.execute(
                "INSERT INTO weekly_hours (schedule_id, day_of_week, start_time, end_time) "
                "VALUES (:sid, :d, :s, :e)",
                {"sid": sid, "d": w.day_of_week, "s": w.start_time, "e": w.end_time},
            )
        for o in dto.date_overrides:
            await self._sql.execute(
                "INSERT INTO date_override (schedule_id, date, start_time, end_time) "
                "VALUES (:sid, :date, :s, :e)",
                {"sid": sid, "date": o.date, "s": o.start_time, "e": o.end_time},
            )
        bundle = await self.get_bundle(owner_user_id)
        assert bundle is not None
        return bundle

    async def get_bundle(self, owner_user_id: UUID) -> ScheduleBundleDTO | None:
        srow = await self._sql.fetch_one(
            "SELECT id, owner_user_id, name, time_zone FROM schedule WHERE owner_user_id = :owner",
            {"owner": owner_user_id},
        )
        if srow is None:
            return None
        sid = srow["id"]
        whs = await self._sql.fetch_all(
            "SELECT day_of_week, start_time, end_time FROM weekly_hours WHERE schedule_id = :sid "
            "ORDER BY day_of_week, start_time",
            {"sid": sid},
        )
        ovs = await self._sql.fetch_all(
            "SELECT date, start_time, end_time FROM date_override WHERE schedule_id = :sid ORDER BY date, start_time",
            {"sid": sid},
        )
        trs = await self._sql.fetch_all(
            "SELECT time_zone, start_date, end_date, prev_time_zone FROM travel_schedule "
            "WHERE schedule_id = :sid ORDER BY start_date",
            {"sid": sid},
        )
        return ScheduleBundleDTO(
            schedule=ScheduleDTO(srow["id"], srow["owner_user_id"], srow["name"], srow["time_zone"]),
            weekly_hours=[WeeklyHourDTO(r["day_of_week"], r["start_time"], r["end_time"]) for r in whs],
            date_overrides=[DateOverrideDTO(r["date"], r["start_time"], r["end_time"]) for r in ovs],
            travel_schedules=[TravelDTO(r["time_zone"], r["start_date"], r["end_date"], r["prev_time_zone"]) for r in trs],
        )

    async def append_change_log(self, owner_user_id: UUID, schedule_id: UUID, actor: ActorDTO, snapshot: dict) -> None:
        await self._sql.execute(
            """
            INSERT INTO schedule_change_log (owner_user_id, schedule_id, actor_source, actor_user_id, snapshot)
            VALUES (:owner, :sid, :src, :uid, CAST(:snap AS jsonb))
            """,
            {
                "owner": owner_user_id,
                "sid": schedule_id,
                "src": actor.source,
                "uid": actor.user_id,
                "snap": json.dumps(snapshot),
            },
        )
```

- [ ] **Step 6: `controllers/schedule.py`** — валидация → replace-all → snapshot в той же транзакции

```python
from uuid import UUID

from event_scheduling.dto.schedule import ActorDTO, ScheduleBundleDTO, UpsertScheduleDTO
from event_scheduling.errors import NotFoundError
from event_scheduling.interfaces.schedule import IScheduleDBAdapter
from event_scheduling.validation import validate_date_overrides, validate_time_zone, validate_weekly_hours


def _bundle_to_snapshot(bundle: ScheduleBundleDTO) -> dict:
    return {
        "schedule": {"name": bundle.schedule.name, "time_zone": bundle.schedule.time_zone},
        "weekly_hours": [{"day_of_week": w.day_of_week, "start_time": w.start_time.isoformat(),
                          "end_time": w.end_time.isoformat()} for w in bundle.weekly_hours],
        "date_overrides": [{"date": o.date.isoformat(),
                            "start_time": o.start_time.isoformat() if o.start_time else None,
                            "end_time": o.end_time.isoformat() if o.end_time else None} for o in bundle.date_overrides],
        "travel_schedules": [{"time_zone": t.time_zone, "start_date": t.start_date.isoformat(),
                              "end_date": t.end_date.isoformat() if t.end_date else None,
                              "prev_time_zone": t.prev_time_zone} for t in bundle.travel_schedules],
    }


class ScheduleController:
    def __init__(self, db: IScheduleDBAdapter) -> None:
        self._db = db

    async def get_schedule(self, owner_user_id: UUID) -> ScheduleBundleDTO:
        bundle = await self._db.get_bundle(owner_user_id)
        if bundle is None:
            raise NotFoundError(f"schedule for owner {owner_user_id} not found")
        return bundle

    async def upsert_schedule(self, owner_user_id: UUID, dto: UpsertScheduleDTO, actor: ActorDTO) -> ScheduleBundleDTO:
        validate_time_zone(dto.time_zone)
        validate_weekly_hours(dto.weekly_hours)
        validate_date_overrides(dto.date_overrides)
        bundle = await self._db.replace_schedule(owner_user_id, dto)
        await self._db.append_change_log(owner_user_id, bundle.schedule.id, actor, _bundle_to_snapshot(bundle))
        return bundle
```

- [ ] **Step 7: `schemas/schedule.py`** — Pydantic req/resp + `to_dto`/`from_dto`

```python
from datetime import date, time
from uuid import UUID

from pydantic import BaseModel

from event_scheduling.dto.schedule import (
    DateOverrideDTO, ScheduleBundleDTO, UpsertScheduleDTO, WeeklyHourDTO,
)


class WeeklyHourModel(BaseModel):
    day_of_week: int
    start_time: time
    end_time: time


class DateOverrideModel(BaseModel):
    date: date
    start_time: time | None = None
    end_time: time | None = None


class TravelModel(BaseModel):
    time_zone: str
    start_date: date
    end_date: date | None = None
    prev_time_zone: str | None = None


class UpsertScheduleRequest(BaseModel):
    name: str
    time_zone: str
    weekly_hours: list[WeeklyHourModel]
    date_overrides: list[DateOverrideModel]

    def to_dto(self) -> UpsertScheduleDTO:
        return UpsertScheduleDTO(
            name=self.name,
            time_zone=self.time_zone,
            weekly_hours=[WeeklyHourDTO(w.day_of_week, w.start_time, w.end_time) for w in self.weekly_hours],
            date_overrides=[DateOverrideDTO(o.date, o.start_time, o.end_time) for o in self.date_overrides],
        )


class ScheduleModel(BaseModel):
    id: UUID
    owner_user_id: UUID
    name: str
    time_zone: str


class ScheduleBundleResponse(BaseModel):
    schedule: ScheduleModel
    weekly_hours: list[WeeklyHourModel]
    date_overrides: list[DateOverrideModel]
    travel_schedules: list[TravelModel]

    @classmethod
    def from_dto(cls, b: ScheduleBundleDTO) -> "ScheduleBundleResponse":
        return cls(
            schedule=ScheduleModel(id=b.schedule.id, owner_user_id=b.schedule.owner_user_id,
                                   name=b.schedule.name, time_zone=b.schedule.time_zone),
            weekly_hours=[WeeklyHourModel(day_of_week=w.day_of_week, start_time=w.start_time, end_time=w.end_time)
                          for w in b.weekly_hours],
            date_overrides=[DateOverrideModel(date=o.date, start_time=o.start_time, end_time=o.end_time)
                            for o in b.date_overrides],
            travel_schedules=[TravelModel(time_zone=t.time_zone, start_date=t.start_date, end_date=t.end_date,
                                          prev_time_zone=t.prev_time_zone) for t in b.travel_schedules],
        )
```

- [ ] **Step 8: Роутер + провайдеры + include**

`schemas/schedule.py` использует actor из заголовков — добавить роутер в новый файл `event_scheduling/routers/schedule.py` (или в `routes.py`). Для единообразия с шаблоном создать `event_scheduling/routers/__init__.py` и `event_scheduling/routers/schedule.py`:

```python
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Header, status

from event_scheduling.auth import require_api_key
from event_scheduling.dto.schedule import ActorDTO
from event_scheduling.interfaces.schedule import IScheduleController
from event_scheduling.schemas.schedule import ScheduleBundleResponse, UpsertScheduleRequest

schedule_router = APIRouter(
    prefix="/api/v1/schedules", tags=["schedules"],
    route_class=DishkaRoute, dependencies=[Depends(require_api_key)],
)


@schedule_router.put("/{owner_user_id}", response_model=ScheduleBundleResponse, status_code=status.HTTP_200_OK)
async def put_schedule(
    owner_user_id: UUID,
    body: UpsertScheduleRequest,
    controller: FromDishka[IScheduleController],
    actor_source: str = Header(default="admin"),
    actor_user_id: UUID | None = Header(default=None),
) -> ScheduleBundleResponse:
    actor = ActorDTO(source=actor_source, user_id=actor_user_id)
    bundle = await controller.upsert_schedule(owner_user_id, body.to_dto(), actor)
    return ScheduleBundleResponse.from_dto(bundle)
```

В `routes.py` добавить `from event_scheduling.routers.schedule import schedule_router` и в `main.py` — `app.include_router(schedule_router)` (или включить внутри `root_router`; проще — отдельным `app.include_router`).

В `ioc.py` добавить провайдеры (REQUEST scope):
```python
    @provide(scope=Scope.REQUEST)
    def provide_schedule_db(self, sql: ISqlExecutor) -> IScheduleDBAdapter:
        return ScheduleDBAdapter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_schedule_controller(self, db: IScheduleDBAdapter) -> IScheduleController:
        return ScheduleController(db)
```
(+ импорты `ScheduleDBAdapter`, `ScheduleController`, `IScheduleDBAdapter`, `IScheduleController`).

- [ ] **Step 9: Прогнать — зелёный**

Run: `cd event-scheduling && uv run pytest tests/test_schedule_api.py -v`
Expected: PASS (create, replace-all, 422 на плохой tz).

- [ ] **Step 10: Commit**

```bash
git add event-scheduling/event_scheduling event-scheduling/tests/test_schedule_api.py
git commit -m "feat(scheduling): schedule PUT — replace-all + audit snapshot"
```

---

## Task 5: Schedule GET (composite bundle)

**Files:**
- Modify: `event_scheduling/routers/schedule.py`
- Test: `tests/test_schedule_api.py` (дополнить)

**Interfaces:**
- Consumes: `IScheduleController.get_schedule`.
- Produces: `GET /api/v1/schedules/{owner_user_id}` → 200 bundle | 404.

- [ ] **Step 1: Падающий тест (дополнить `tests/test_schedule_api.py`)**

```python
def test_get_returns_bundle_after_put(client) -> None:
    owner = str(uuid4())
    client.put(f"/api/v1/schedules/{owner}", json=_bundle(), headers=HDRS)
    resp = client.get(f"/api/v1/schedules/{owner}")
    assert resp.status_code == 200
    assert resp.json()["schedule"]["owner_user_id"] == owner


def test_get_missing_returns_404(client) -> None:
    resp = client.get(f"/api/v1/schedules/{uuid4()}")
    assert resp.status_code == 404
```

- [ ] **Step 2: Прогнать — падает**

Run: `cd event-scheduling && uv run pytest tests/test_schedule_api.py -k get -v`
Expected: FAIL (нет GET-роута).

- [ ] **Step 3: Добавить GET в `routers/schedule.py`**

```python
@schedule_router.get("/{owner_user_id}", response_model=ScheduleBundleResponse)
async def get_schedule(owner_user_id: UUID, controller: FromDishka[IScheduleController]) -> ScheduleBundleResponse:
    bundle = await controller.get_schedule(owner_user_id)
    return ScheduleBundleResponse.from_dto(bundle)
```
> `NotFoundError` из контроллера транслируется в 404 обработчиком из `main.py`.

- [ ] **Step 4: Прогнать — зелёный**

Run: `cd event-scheduling && uv run pytest tests/test_schedule_api.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add event-scheduling/event_scheduling/routers/schedule.py event-scheduling/tests/test_schedule_api.py
git commit -m "feat(scheduling): schedule GET composite bundle"
```

---

## Task 6: Travel PUT (diff-replace) + аудит-снимок

**Files:**
- Modify: `dto/schedule.py`, `interfaces/schedule.py`, `adapters/schedule_db.py`, `controllers/schedule.py`, `schemas/schedule.py`, `routers/schedule.py`
- Test: `tests/test_schedule_api.py` (дополнить)

**Interfaces:**
- Produces: `IScheduleController.replace_travel(owner_user_id, travels: list[TravelDTO], actor) -> ScheduleBundleDTO`; `IScheduleDBAdapter.replace_travel(schedule_id, travels) -> None`; route `PUT /api/v1/schedules/{owner_user_id}/travel`.

- [ ] **Step 1: Падающий тест**

```python
def test_put_travel_replaces_and_snapshots(client) -> None:
    owner = str(uuid4())
    client.put(f"/api/v1/schedules/{owner}", json=_bundle(), headers=HDRS)
    travel = {"travel_schedules": [
        {"time_zone": "Asia/Almaty", "start_date": "2026-02-01", "end_date": "2026-02-10", "prev_time_zone": "Europe/Moscow"},
    ]}
    resp = client.put(f"/api/v1/schedules/{owner}/travel", json=travel, headers=HDRS)
    assert resp.status_code == 200
    assert len(resp.json()["travel_schedules"]) == 1
    # replace: пустой список очищает
    empty = client.put(f"/api/v1/schedules/{owner}/travel", json={"travel_schedules": []}, headers=HDRS)
    assert empty.json()["travel_schedules"] == []


def test_put_travel_rejects_bad_tz(client) -> None:
    owner = str(uuid4())
    client.put(f"/api/v1/schedules/{owner}", json=_bundle(), headers=HDRS)
    bad = {"travel_schedules": [{"time_zone": "Mars/Base", "start_date": "2026-02-01", "end_date": None, "prev_time_zone": None}]}
    resp = client.put(f"/api/v1/schedules/{owner}/travel", json=bad, headers=HDRS)
    assert resp.status_code == 422
```

- [ ] **Step 2: Прогнать — падает.** Run: `uv run pytest tests/test_schedule_api.py -k travel -v` → FAIL.

- [ ] **Step 3: Адаптер — `replace_travel`**

```python
    async def replace_travel(self, schedule_id: UUID, travels: list[TravelDTO]) -> None:
        await self._sql.execute("DELETE FROM travel_schedule WHERE schedule_id = :sid", {"sid": schedule_id})
        for t in travels:
            await self._sql.execute(
                "INSERT INTO travel_schedule (schedule_id, time_zone, start_date, end_date, prev_time_zone) "
                "VALUES (:sid, :tz, :sd, :ed, :prev)",
                {"sid": schedule_id, "tz": t.time_zone, "sd": t.start_date, "ed": t.end_date, "prev": t.prev_time_zone},
            )
```
Добавить сигнатуру в `IScheduleDBAdapter`.

- [ ] **Step 4: Контроллер — `replace_travel`** (валидирует tz каждой поездки, пишет снимок)

```python
    async def replace_travel(self, owner_user_id: UUID, travels: list[TravelDTO], actor: ActorDTO) -> ScheduleBundleDTO:
        existing = await self._db.get_bundle(owner_user_id)
        if existing is None:
            raise NotFoundError(f"schedule for owner {owner_user_id} not found")
        for t in travels:
            validate_time_zone(t.time_zone)
            if t.prev_time_zone is not None:
                validate_time_zone(t.prev_time_zone)
        await self._db.replace_travel(existing.schedule.id, travels)
        bundle = await self._db.get_bundle(owner_user_id)
        await self._db.append_change_log(owner_user_id, bundle.schedule.id, actor, _bundle_to_snapshot(bundle))
        return bundle
```
Добавить в `IScheduleController`.

- [ ] **Step 5: Schema + route.** В `schemas/schedule.py`:
```python
class ReplaceTravelRequest(BaseModel):
    travel_schedules: list[TravelModel]

    def to_dtos(self) -> list["TravelDTO"]:
        from event_scheduling.dto.schedule import TravelDTO
        return [TravelDTO(t.time_zone, t.start_date, t.end_date, t.prev_time_zone) for t in self.travel_schedules]
```
В `routers/schedule.py`:
```python
@schedule_router.put("/{owner_user_id}/travel", response_model=ScheduleBundleResponse)
async def put_travel(
    owner_user_id: UUID, body: ReplaceTravelRequest, controller: FromDishka[IScheduleController],
    actor_source: str = Header(default="admin"), actor_user_id: UUID | None = Header(default=None),
) -> ScheduleBundleResponse:
    bundle = await controller.replace_travel(owner_user_id, body.to_dtos(), ActorDTO(actor_source, actor_user_id))
    return ScheduleBundleResponse.from_dto(bundle)
```

- [ ] **Step 6: Прогнать — зелёный.** Run: `uv run pytest tests/test_schedule_api.py -v` → PASS.

- [ ] **Step 7: Commit**
```bash
git add event-scheduling/event_scheduling event-scheduling/tests/test_schedule_api.py
git commit -m "feat(scheduling): travel PUT diff-replace + audit snapshot"
```

---

## Task 7: GET change-log

**Files:**
- Modify: `dto/schedule.py`, `interfaces/schedule.py`, `adapters/schedule_db.py`, `controllers/schedule.py`, `schemas/schedule.py`, `routers/schedule.py`
- Test: `tests/test_change_log.py`

**Interfaces:**
- Produces: `ChangeLogEntryDTO(id, at, actor_source, actor_user_id, snapshot: dict)`; `IScheduleDBAdapter.list_change_log(owner_user_id, limit, offset) -> list[ChangeLogEntryDTO]`; `IScheduleController.list_change_log(...)`; route `GET /api/v1/schedules/{owner_user_id}/change-log?limit&offset`.

- [ ] **Step 1: Падающий тест `tests/test_change_log.py`**

```python
from uuid import uuid4

HDRS = {"actor-source": "admin"}


def _bundle():
    return {"name": "s", "time_zone": "Europe/Moscow",
            "weekly_hours": [{"day_of_week": 1, "start_time": "09:00", "end_time": "17:00"}],
            "date_overrides": []}


def test_each_put_appends_one_snapshot(client) -> None:
    owner = str(uuid4())
    client.put(f"/api/v1/schedules/{owner}", json=_bundle(), headers=HDRS)
    second = _bundle(); second["name"] = "s2"
    client.put(f"/api/v1/schedules/{owner}", json=second, headers=HDRS)
    resp = client.get(f"/api/v1/schedules/{owner}/change-log")
    assert resp.status_code == 200
    log = resp.json()["entries"]
    assert len(log) == 2
    # по убыванию at: свежий первый
    assert log[0]["snapshot"]["schedule"]["name"] == "s2"
    assert log[0]["actor_source"] == "admin"
```

- [ ] **Step 2: Прогнать — падает.** Run: `uv run pytest tests/test_change_log.py -v` → FAIL.

- [ ] **Step 3: DTO + адаптер**

`dto/schedule.py`:
```python
@dataclass(frozen=True)
class ChangeLogEntryDTO:
    id: UUID
    at: "datetime"
    actor_source: str
    actor_user_id: UUID | None
    snapshot: dict
```
(добавить `from datetime import datetime` вверх файла.)

`adapters/schedule_db.py`:
```python
    async def list_change_log(self, owner_user_id: UUID, limit: int, offset: int) -> list[ChangeLogEntryDTO]:
        rows = await self._sql.fetch_all(
            "SELECT id, at, actor_source, actor_user_id, snapshot FROM schedule_change_log "
            "WHERE owner_user_id = :owner ORDER BY at DESC, id DESC LIMIT :limit OFFSET :offset",
            {"owner": owner_user_id, "limit": limit, "offset": offset},
        )
        return [ChangeLogEntryDTO(r["id"], r["at"], r["actor_source"], r["actor_user_id"], r["snapshot"]) for r in rows]
```
(+ сигнатура в `IScheduleDBAdapter`; import `ChangeLogEntryDTO`.)

- [ ] **Step 4: Контроллер + schema + route**

Контроллер:
```python
    async def list_change_log(self, owner_user_id: UUID, limit: int, offset: int) -> list[ChangeLogEntryDTO]:
        return await self._db.list_change_log(owner_user_id, limit, offset)
```
`schemas/schedule.py`:
```python
class ChangeLogEntryModel(BaseModel):
    id: UUID
    at: datetime
    actor_source: str
    actor_user_id: UUID | None
    snapshot: dict


class ChangeLogResponse(BaseModel):
    entries: list[ChangeLogEntryModel]
```
(добавить `from datetime import datetime`.)
`routers/schedule.py`:
```python
@schedule_router.get("/{owner_user_id}/change-log", response_model=ChangeLogResponse)
async def get_change_log(
    owner_user_id: UUID, controller: FromDishka[IScheduleController],
    limit: int = 50, offset: int = 0,
) -> ChangeLogResponse:
    entries = await controller.list_change_log(owner_user_id, limit, offset)
    return ChangeLogResponse(entries=[ChangeLogEntryModel(**e.__dict__) for e in entries])
```

- [ ] **Step 5: Прогнать — зелёный.** Run: `uv run pytest tests/test_change_log.py -v` → PASS.

- [ ] **Step 6: Commit**
```bash
git add event-scheduling/event_scheduling event-scheduling/tests/test_change_log.py
git commit -m "feat(scheduling): GET schedule change-log (raw snapshots)"
```

---

## Task 8: Event-type CRUD (nested hosts + booking_limits)

**Files:**
- Create: `dto/event_type.py` (дополнить), `schemas/event_type.py`, `interfaces/event_type.py`, `adapters/event_type_db.py`, `controllers/event_type.py`, `routers/event_type.py`
- Modify: `ioc.py`, `main.py`/`routes.py` (include)
- Test: `tests/test_event_type_api.py`

**Interfaces:**
- Produces:
  - DTO: `HostDTO(user_id, schedule_id)`, `EventTypeDTO(id, slug, title, scheduling_type, duration_minutes, slot_interval_minutes, min_booking_notice_minutes, buffer_before_minutes, buffer_after_minutes, hosts: list[HostDTO], booking_limits: list[BookingLimitDTO])`, `UpsertEventTypeDTO(...без id...)`.
  - `IEventTypeController.create/get/list/update/delete`; `IEventTypeDBAdapter.insert/get/list/update/delete` (replace-all hosts+limits в update).
  - Routes: `POST /api/v1/event-types`, `GET /api/v1/event-types`, `GET /api/v1/event-types/{id}`, `PUT /api/v1/event-types/{id}`, `DELETE /api/v1/event-types/{id}`.

- [ ] **Step 1: Падающий тест `tests/test_event_type_api.py`**

```python
from uuid import uuid4

HDRS = {"authorization": "ignored-by-fixture"}  # client fixture already sets bearer


def _sched_owner(client) -> tuple[str, str]:
    owner = str(uuid4())
    client.put(f"/api/v1/schedules/{owner}", json={
        "name": "s", "time_zone": "Europe/Moscow",
        "weekly_hours": [{"day_of_week": 1, "start_time": "09:00", "end_time": "17:00"}],
        "date_overrides": [],
    }, headers={"actor-source": "admin"})
    sid = client.get(f"/api/v1/schedules/{owner}").json()["schedule"]["id"]
    return owner, sid


def _payload(slug: str, owner: str, sid: str) -> dict:
    return {
        "slug": slug, "title": "Разбор", "duration_minutes": 60,
        "slot_interval_minutes": 30, "min_booking_notice_minutes": 120,
        "buffer_before_minutes": 5, "buffer_after_minutes": 5,
        "hosts": [{"user_id": owner, "schedule_id": sid}],
        "booking_limits": [{"limit_type": "booking_count", "period": "day", "value": 3}],
    }


def test_create_and_get_event_type(client) -> None:
    owner, sid = _sched_owner(client)
    created = client.post("/api/v1/event-types", json=_payload("razbor", owner, sid))
    assert created.status_code == 201
    et_id = created.json()["id"]
    got = client.get(f"/api/v1/event-types/{et_id}")
    assert got.status_code == 200
    assert got.json()["duration_minutes"] == 60
    assert len(got.json()["hosts"]) == 1
    assert len(got.json()["booking_limits"]) == 1


def test_update_replaces_hosts_and_limits(client) -> None:
    owner, sid = _sched_owner(client)
    et_id = client.post("/api/v1/event-types", json=_payload("upd", owner, sid)).json()["id"]
    upd = _payload("upd", owner, sid)
    upd["booking_limits"] = []
    resp = client.put(f"/api/v1/event-types/{et_id}", json=upd)
    assert resp.status_code == 200
    assert resp.json()["booking_limits"] == []


def test_create_rejects_zero_limit(client) -> None:
    owner, sid = _sched_owner(client)
    bad = _payload("bad", owner, sid)
    bad["booking_limits"] = [{"limit_type": "booking_count", "period": "day", "value": 0}]
    assert client.post("/api/v1/event-types", json=bad).status_code == 422


def test_delete_event_type(client) -> None:
    owner, sid = _sched_owner(client)
    et_id = client.post("/api/v1/event-types", json=_payload("del", owner, sid)).json()["id"]
    assert client.delete(f"/api/v1/event-types/{et_id}").status_code == 204
    assert client.get(f"/api/v1/event-types/{et_id}").status_code == 404
```

- [ ] **Step 2: Прогнать — падает.** Run: `uv run pytest tests/test_event_type_api.py -v` → FAIL.

- [ ] **Step 3: DTO (`dto/event_type.py` — дополнить `BookingLimitDTO` из Task 3)**

```python
from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class HostDTO:
    user_id: UUID
    schedule_id: UUID


@dataclass(frozen=True)
class EventTypeDTO:
    id: UUID
    slug: str
    title: str
    scheduling_type: str
    duration_minutes: int
    slot_interval_minutes: int | None
    min_booking_notice_minutes: int
    buffer_before_minutes: int
    buffer_after_minutes: int
    hosts: list[HostDTO]
    booking_limits: list["BookingLimitDTO"]


@dataclass(frozen=True)
class UpsertEventTypeDTO:
    slug: str
    title: str
    scheduling_type: str
    duration_minutes: int
    slot_interval_minutes: int | None
    min_booking_notice_minutes: int
    buffer_before_minutes: int
    buffer_after_minutes: int
    hosts: list[HostDTO]
    booking_limits: list["BookingLimitDTO"]
```

- [ ] **Step 4: `interfaces/event_type.py`, `adapters/event_type_db.py`, `controllers/event_type.py`**

Адаптер: `insert` (INSERT event_type RETURNING id; затем INSERT hosts + booking_limits; вернуть `get`), `get` (SELECT event_type + hosts + limits → DTO | None), `list_all`, `update` (UPDATE event_type; `DELETE FROM host WHERE event_type_id=:id`; `DELETE FROM booking_limit WHERE event_type_id=:id`; заново INSERT; вернуть `get`), `delete` (DELETE ... RETURNING id → bool). Ловить `IntegrityError` на дубликат `slug` → `ConflictError`.

Контроллер `create`/`update` вызывают `validate_booking_limits(dto.booking_limits)` перед записью; `get`/`delete` бросают `NotFoundError` при отсутствии.

Полные реализации — по образцу `ScheduleDBAdapter` (Task 4, Step 5) и `event-shortener/event_scheduling/adapters/short_url_db.py`. Ключевые запросы:

```python
# insert event_type
row = await self._sql.fetch_one(
    """
    INSERT INTO event_type (slug, title, scheduling_type, duration_minutes, slot_interval_minutes,
                            min_booking_notice_minutes, buffer_before_minutes, buffer_after_minutes)
    VALUES (:slug, :title, :st, :dur, :si, :notice, :bb, :ba)
    RETURNING id
    """,
    {"slug": dto.slug, "title": dto.title, "st": dto.scheduling_type, "dur": dto.duration_minutes,
     "si": dto.slot_interval_minutes, "notice": dto.min_booking_notice_minutes,
     "bb": dto.buffer_before_minutes, "ba": dto.buffer_after_minutes},
)
# hosts
await self._sql.execute(
    "INSERT INTO host (event_type_id, user_id, schedule_id) VALUES (:et, :uid, :sid)",
    {"et": et_id, "uid": h.user_id, "sid": h.schedule_id},
)
# booking_limits
await self._sql.execute(
    "INSERT INTO booking_limit (event_type_id, limit_type, period, value) VALUES (:et, :lt, :p, :v)",
    {"et": et_id, "lt": bl.limit_type, "p": bl.period, "v": bl.value},
)
```
> Раскрыть полностью все методы адаптера/контроллера/интерфейса без многоточий.

- [ ] **Step 5: `schemas/event_type.py` + `routers/event_type.py`** (Pydantic req/resp с `to_dto`/`from_dto`; роутер под `require_api_key`; POST→201, DELETE→204). Include в app; провайдеры в `ioc.py`.

- [ ] **Step 6: Прогнать — зелёный.** Run: `uv run pytest tests/test_event_type_api.py -v` → PASS.

- [ ] **Step 7: Commit**
```bash
git add event-scheduling/event_scheduling event-scheduling/tests/test_event_type_api.py
git commit -m "feat(scheduling): event-type CRUD with nested hosts + booking limits"
```

---

## Task 9: ETL — чистые функции маппинга (unit)

**Files:**
- Create: `scripts/__init__.py`, `scripts/etl_mapping.py`
- Test: `tests/test_etl_mapping.py`

**Interfaces:**
- Produces:
  - `etl_mapping.remap_day_of_week(calcom_day: int) -> int` (0=Вс→7, 1=Пн→1 … 6=Сб→6)
  - `etl_mapping.resolve_time_zone(schedule_tz: str | None, user_tz: str) -> str`
  - `etl_mapping.expand_weekly(days: list[int], start: time, end: time) -> list[WeeklyHourDTO]`
  - `etl_mapping.expand_booking_limits(limits_json: dict, limit_type: str) -> list[BookingLimitDTO]`

- [ ] **Step 1: Падающий тест `tests/test_etl_mapping.py`**

```python
import datetime as dt

from event_scheduling.dto.event_type import BookingLimitDTO
from scripts.etl_mapping import expand_booking_limits, expand_weekly, remap_day_of_week, resolve_time_zone


def test_remap_day_sunday_zero_to_seven() -> None:
    assert remap_day_of_week(0) == 7
    assert remap_day_of_week(1) == 1
    assert remap_day_of_week(6) == 6


def test_resolve_time_zone_prefers_schedule_then_user() -> None:
    assert resolve_time_zone("Europe/Berlin", "Europe/Moscow") == "Europe/Berlin"
    assert resolve_time_zone(None, "Europe/Moscow") == "Europe/Moscow"


def test_expand_weekly_one_row_per_day() -> None:
    rows = expand_weekly([0, 1, 3], dt.time(9), dt.time(17))
    assert sorted(r.day_of_week for r in rows) == [1, 3, 7]  # 0→7 remap
    assert all(r.start_time == dt.time(9) and r.end_time == dt.time(17) for r in rows)


def test_expand_booking_limits_json_to_rows() -> None:
    rows = expand_booking_limits({"PER_DAY": 3, "PER_WEEK": 10}, "booking_count")
    assert BookingLimitDTO("booking_count", "day", 3) in rows
    assert BookingLimitDTO("booking_count", "week", 10) in rows
```

- [ ] **Step 2: Прогнать — падает.** Run: `uv run pytest tests/test_etl_mapping.py -v` → FAIL.

- [ ] **Step 3: `scripts/etl_mapping.py`**

```python
from collections.abc import Sequence
from datetime import time

from event_scheduling.dto.event_type import BookingLimitDTO
from event_scheduling.dto.schedule import WeeklyHourDTO

# cal.com bookingLimits/durationLimits JSON keys → domain period
_PERIOD_MAP = {"PER_DAY": "day", "PER_WEEK": "week", "PER_MONTH": "month", "PER_YEAR": "year"}


def remap_day_of_week(calcom_day: int) -> int:
    """cal.com: 0=Sunday..6=Saturday → ISO: 1=Monday..7=Sunday."""
    if calcom_day == 0:
        return 7
    return calcom_day


def resolve_time_zone(schedule_tz: str | None, user_tz: str) -> str:
    if schedule_tz is not None:
        return schedule_tz
    return user_tz


def expand_weekly(days: Sequence[int], start: time, end: time) -> list[WeeklyHourDTO]:
    return [WeeklyHourDTO(remap_day_of_week(d), start, end) for d in days]


def expand_booking_limits(limits_json: dict, limit_type: str) -> list[BookingLimitDTO]:
    rows: list[BookingLimitDTO] = []
    for key, value in limits_json.items():
        period = _PERIOD_MAP.get(key)
        if period is None:
            continue
        rows.append(BookingLimitDTO(limit_type, period, int(value)))
    return rows
```

- [ ] **Step 4: Прогнать — зелёный.** Run: `uv run pytest tests/test_etl_mapping.py -v` → PASS.

- [ ] **Step 5: Commit**
```bash
git add event-scheduling/scripts/etl_mapping.py event-scheduling/scripts/__init__.py event-scheduling/tests/test_etl_mapping.py
git commit -m "feat(scheduling): ETL pure mapping functions (day remap, tz, limits)"
```

---

## Task 10: ETL — оркестрация + отчёт (интеграция против сид-БД cal.com)

**Files:**
- Create: `scripts/etl_from_calcom.py`
- Test: `tests/test_etl_run.py`

**Interfaces:**
- Consumes: `etl_mapping.*`; таблицы `event_scheduling` (запись через прямой asyncpg/SQLAlchemy engine на целевой DSN); БД `calcom` (чтение).
- Produces: `etl_from_calcom.run_etl(calcom_dsn: str, target_dsn: str, resolve_email_to_uuid: Callable[[str], UUID | None]) -> EtlReport`, где `EtlReport` — dataclass со счётчиками `migrated`/`skipped` по каждой сущности и списком `skips: list[tuple[str, str]]` (entity, reason).

- [ ] **Step 1: Тест-фикстура cal.com.** В `tests/test_etl_run.py` подготовить минимальную схему-срез cal.com в отдельной тестовой БД (или в схеме `calcom_fixture` того же ephemeral Postgres): таблицы `users(id int, email text, "timeZone" text, "defaultScheduleId" int)`, `"Schedule"(id, "userId", "timeZone")`, `"Availability"(id, "scheduleId", days int[], "startTime" time, "endTime" time, date date)`. Засеять: 1 организатор (email совпадает с тем, что вернёт `resolve_email_to_uuid`), 1 default-расписание с недельной строкой `days={1,3}` и одним date-override, плюс 1 «лишнее» расписание (не default) — ожидаем его пропуск с логом.

```python
import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from scripts.etl_from_calcom import run_etl


@pytest.mark.asyncio
async def test_etl_migrates_default_schedule_and_skips_extra(_migrated: str, calcom_dsn: str) -> None:
    uid = uuid4()
    report = await run_etl(calcom_dsn=calcom_dsn, target_dsn=_migrated,
                           resolve_email_to_uuid=lambda email: uid if email == "org@example.com" else None)
    assert report.migrated["schedule"] == 1
    assert report.skipped["schedule"] >= 1  # лишнее расписание пропущено
    assert any(reason for entity, reason in report.skips if entity == "schedule")

    eng = create_async_engine(_migrated)
    async with eng.connect() as conn:
        wh = (await conn.execute(text("SELECT day_of_week FROM weekly_hours ORDER BY day_of_week"))).scalars().all()
        assert wh == [1, 3]
        baseline = (await conn.execute(text(
            "SELECT count(*) FROM schedule_change_log WHERE actor_source = 'etl'"))).scalar()
        assert baseline == 1  # стартовый снимок
    await eng.dispose()
```
> `calcom_dsn` — фикстура в `conftest.py`, создающая и сидирующая срез cal.com-схемы (см. Step 2). Не подключать реальный `docker/calcom-init/` в юните — достаточно минимального среза таблиц, которые читает ETL.

- [ ] **Step 2: Прогнать — падает.** Run: `uv run pytest tests/test_etl_run.py -v` → FAIL (нет `run_etl`).

- [ ] **Step 3: `scripts/etl_from_calcom.py`**

Реализовать `run_etl`: открыть два async engine (calcom read, target write). Прочитать `users` в карту `id→(email, timeZone, defaultScheduleId)`. Для каждого `Schedule`: если `userId.defaultScheduleId != schedule.id` → `skipped["schedule"] += 1`, `skips.append(("schedule","non-default"))`, continue. Резолвить `uuid = resolve_email_to_uuid(email)`; None → skip с причиной `"email-not-found"`. Иначе — в одной транзакции целевой БД: upsert `schedule` (`resolve_time_zone`), собрать `Availability` строки, `expand_weekly` для recurring (`date IS NULL AND array_length(days,1) IS NOT NULL`), date-override для `date IS NOT NULL`, вставить, затем прочитать bundle и записать baseline-снимок в `schedule_change_log` (`actor_source='etl'`). Аналогично event_type/host/booking_limit (managed/collective — skip с логом; в юнит-тесте эти таблицы можно не сидировать — счётчики останутся 0). Вернуть `EtlReport`.

```python
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from scripts.etl_mapping import expand_weekly, resolve_time_zone


@dataclass
class EtlReport:
    migrated: dict[str, int] = field(default_factory=lambda: {"schedule": 0, "event_type": 0})
    skipped: dict[str, int] = field(default_factory=lambda: {"schedule": 0, "event_type": 0})
    skips: list[tuple[str, str]] = field(default_factory=list)


async def run_etl(calcom_dsn: str, target_dsn: str,
                  resolve_email_to_uuid: Callable[[str], UUID | None]) -> EtlReport:
    report = EtlReport()
    src = create_async_engine(calcom_dsn)
    dst = create_async_engine(target_dsn)
    try:
        async with src.connect() as sconn:
            users = {r[0]: {"email": r[1], "tz": r[2], "default": r[3]} for r in (await sconn.execute(
                text('SELECT id, email, "timeZone", "defaultScheduleId" FROM users'))).all()}
            schedules = (await sconn.execute(
                text('SELECT id, "userId", "timeZone" FROM "Schedule"'))).all()
            for sid, uid, sched_tz in schedules:
                user = users.get(uid)
                if user is None or user["default"] != sid:
                    report.skipped["schedule"] += 1
                    report.skips.append(("schedule", "non-default-or-missing-user"))
                    continue
                target_uuid = resolve_email_to_uuid(user["email"])
                if target_uuid is None:
                    report.skipped["schedule"] += 1
                    report.skips.append(("schedule", f"email-not-found:{user['email']}"))
                    continue
                avails = (await sconn.execute(text(
                    'SELECT days, "startTime", "endTime", date FROM "Availability" WHERE "scheduleId" = :sid'),
                    {"sid": sid})).all()
                tz = resolve_time_zone(sched_tz, user["tz"])
                await _write_schedule(dst, target_uuid, tz, avails)
                report.migrated["schedule"] += 1
        return report
    finally:
        await src.dispose()
        await dst.dispose()


async def _write_schedule(dst, owner_uuid: UUID, tz: str, avails: list) -> None:
    async with dst.begin() as conn:
        new_sid = (await conn.execute(text(
            """
            INSERT INTO schedule (owner_user_id, name, time_zone) VALUES (:o, 'Imported', :tz)
            ON CONFLICT (owner_user_id) DO UPDATE SET time_zone = EXCLUDED.time_zone, updated_at = now()
            RETURNING id
            """), {"o": owner_uuid, "tz": tz})).scalar()
        await conn.execute(text("DELETE FROM weekly_hours WHERE schedule_id = :s"), {"s": new_sid})
        await conn.execute(text("DELETE FROM date_override WHERE schedule_id = :s"), {"s": new_sid})
        for days, start, end, date in avails:
            if date is not None:
                await conn.execute(text(
                    "INSERT INTO date_override (schedule_id, date, start_time, end_time) VALUES (:s,:d,:st,:e)"),
                    {"s": new_sid, "d": date, "st": start, "e": end})
                continue
            for wh in expand_weekly(list(days or []), start, end):
                await conn.execute(text(
                    "INSERT INTO weekly_hours (schedule_id, day_of_week, start_time, end_time) VALUES (:s,:d,:st,:e)"),
                    {"s": new_sid, "d": wh.day_of_week, "st": wh.start_time, "e": wh.end_time})
        # baseline snapshot
        await conn.execute(text(
            """
            INSERT INTO schedule_change_log (owner_user_id, schedule_id, actor_source, snapshot)
            VALUES (:o, :s, 'etl', CAST(:snap AS jsonb))
            """), {"o": owner_uuid, "s": new_sid, "snap": '{"source":"etl-baseline"}'})
```
> В юнит-тесте event_type-ветка не задействована; при реальном прогоне добавить симметричную обработку `"EventType"`/`"Host"`/limits с skip-логом для managed/collective (раскрыть полностью, без многоточий).

- [ ] **Step 4: `conftest.py` — фикстура `calcom_dsn`** (создаёт срез cal.com-схемы в отдельной БД `calcom_fixture` на том же ephemeral Postgres и сидирует данные из Step 1). Реализовать создание таблиц `users`/`"Schedule"`/`"Availability"` и вставку сид-строк; вернуть DSN.

- [ ] **Step 5: Прогнать — зелёный.** Run: `uv run pytest tests/test_etl_run.py -v` → PASS (миграция default-расписания, ремап дней `{1,3}`, пропуск лишнего, baseline-снимок).

- [ ] **Step 6: Commit**
```bash
git add event-scheduling/scripts/etl_from_calcom.py event-scheduling/tests/test_etl_run.py event-scheduling/tests/conftest.py
git commit -m "feat(scheduling): ETL orchestration + migration report (integration)"
```

---

## Task 11: Документация + финальная проверка

**Files:**
- Create: `event-scheduling/CLAUDE.md`, `event-scheduling/README.md`, `event-scheduling/docs/{SERVICE_OVERVIEW,API_CONTRACTS,DATA_MODEL,DEPENDENCIES,AUDIT}.md`, `event-scheduling/.env.example`
- Modify: корневой `CLAUDE.md` (data-flow diagram + сервис `event-scheduling`), `docs/architecture/ARCHITECTURE.md` (топология: новый сервис-владелец домена расписаний)

**Interfaces:** нет кода — документация и smoke-проверка полного сервиса.

- [ ] **Step 1: `event-scheduling/CLAUDE.md`** — по образцу `event-shortener/CLAUDE.md`: команды (`uv sync`, `uv run pytest`, `ruff check --fix .`, `alembic upgrade head`), request-flow, слои, перечень 8 таблиц, ссылка на спек и на `scripts/etl_from_calcom.py`.

- [ ] **Step 2: `docs/*.md` + `.env.example`** — заполнить `DATA_MODEL.md` схемой 8 таблиц (из спека §2), `API_CONTRACTS.md` — эндпоинтами (§3), `DEPENDENCIES.md` — «читает event-users по email при ETL; занятость — BusyTimesSource (stub)», `AUDIT.md` — пусто (новый сервис). `.env.example`:
```bash
POSTGRES_DSN=postgresql+asyncpg://event_scheduling:event_scheduling@postgres:5432/event_scheduling
SCHEDULING_API_KEY=dev-scheduling-api-key-3f9c2e1a7b64d508
LOG_LEVEL=INFO
DEBUG=false
```

- [ ] **Step 3: Обновить корневые доки** — `CLAUDE.md` (таблица сервисов уже правлена в Task 1; добавить в data-flow, что `event-scheduling` владеет доменом расписаний, cal.com — источник ETL); `docs/architecture/ARCHITECTURE.md` — врезка про новый сервис и план замены cal.com (ссылка на спек/план).

- [ ] **Step 4: Полный прогон тестов + линт**

Run: `cd event-scheduling && uv run pytest -v && ruff check . && ruff format --check .`
Expected: все тесты PASS; ruff — 0 ошибок.

- [ ] **Step 5: Smoke весь сервис в compose**

Run: `docker compose up -d --build postgres event-scheduling && sleep 25 && curl -fsS localhost:8004/health && curl -fsS -XPUT localhost:8004/api/v1/schedules/$(uuidgen) -H "Authorization: Bearer dev-scheduling-api-key-3f9c2e1a7b64d508" -H "content-type: application/json" -d '{"name":"s","time_zone":"Europe/Moscow","weekly_hours":[{"day_of_week":1,"start_time":"09:00","end_time":"17:00"}],"date_overrides":[]}'`
Expected: health ok; PUT возвращает bundle 200.

- [ ] **Step 6: Commit**
```bash
git add event-scheduling/CLAUDE.md event-scheduling/README.md event-scheduling/docs event-scheduling/.env.example CLAUDE.md docs/architecture/ARCHITECTURE.md
git commit -m "docs(scheduling): service docs + architecture topology for event-scheduling"
```

---

## Self-Review (проведён при написании плана)

**1. Покрытие спека:**
- §1 форма сервиса/границы → Task 1 (scaffold, compose, DI), Task 3 (BusyTimesSource seam).
- §2 схема (8 таблиц) → Task 2.
- §3 CRUD (schedule PUT/GET, travel, change-log; event-type CRUD) → Tasks 4–8; краевые проверки → Task 3 (валидаторы) + применение в контроллерах.
- §4 ETL → Tasks 9–10 (маппинг + оркестрация + отчёт + baseline-снимок).
- §5 тесты/ошибки → тесты в каждом таске; exception handlers (422/404/409) в Task 1 Step 6.
- §7 DoR → Task 11 (доки, полный прогон, smoke).
- Аудит-лог (§2.8) → Task 4 (append в той же транзакции), Task 7 (GET), Task 10 (baseline).

**2. Плейсхолдеры:** миграция (Task 2 Step 4) и ETL event_type-ветка (Task 10 Step 3), адаптер event_type (Task 8 Step 4) содержат явные пометки «раскрыть полностью без многоточий» — это указания исполнителю развернуть повторяющийся по образцу код, а не оставить `...` в файле. Все прочие шаги содержат полный код.

**3. Согласованность типов:** `WeeklyHourDTO`/`DateOverrideDTO` определены в Task 3, переиспользованы в Task 4+; `BookingLimitDTO` в Task 3, дополнен использованием в Task 8/9; `ScheduleBundleDTO`/`ActorDTO` из Task 4 используются в Task 5/6/7; сигнатуры `IScheduleDBAdapter`/`IScheduleController` растут по мере тасков (append_change_log, replace_travel, list_change_log) — каждый таск добавляет и метод, и его использование.
