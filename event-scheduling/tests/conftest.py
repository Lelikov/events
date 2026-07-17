"""Real-seam test harness for event-scheduling.

The service talks to PostgreSQL through ``SqlExecutor`` running raw SQL.
The suite runs against a real PostgreSQL, not a fake.

It uses whatever the ``TEST_POSTGRES_DSN`` env var points at; failing that it
boots a throwaway local cluster via ``initdb``/``pg_ctl`` (Homebrew Postgres).
If neither is available the whole suite skips rather than failing.
"""

import os


os.environ.setdefault("OTEL_SDK_DISABLED", "true")
import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest


API_KEY = "test-scheduling-key"

os.environ.setdefault("SCHEDULING_API_KEY", API_KEY)
os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("DEBUG", "false")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _EphemeralPostgres:
    """A short-lived local Postgres cluster for the test session."""

    def __init__(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="pg-scheduling-test-"))
        self.datadir = self.tmpdir / "data"
        self.socketdir = self.tmpdir / "sock"
        self.socketdir.mkdir()
        self.port = _free_port()

    def start(self) -> str:
        subprocess.run(
            ["initdb", "-D", str(self.datadir), "-U", "postgres", "--auth=trust", "-E", "UTF8"],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            [
                "pg_ctl",
                "-D",
                str(self.datadir),
                "-o",
                f"-p {self.port} -k {self.socketdir} -c listen_addresses=127.0.0.1",
                "-w",
                "-l",
                str(self.tmpdir / "pg.log"),
                "start",
            ],
            check=True,
            capture_output=True,
        )
        subprocess.run(
            ["createdb", "-h", "127.0.0.1", "-p", str(self.port), "-U", "postgres", "event_scheduling"],
            check=True,
            capture_output=True,
        )
        return f"postgresql+asyncpg://postgres@127.0.0.1:{self.port}/event_scheduling"

    def stop(self) -> None:
        with contextlib_suppress():
            subprocess.run(
                ["pg_ctl", "-D", str(self.datadir), "-m", "immediate", "-w", "stop"],
                check=False,
                capture_output=True,
            )
        shutil.rmtree(self.tmpdir, ignore_errors=True)


class contextlib_suppress:  # noqa: N801
    def __enter__(self) -> None:
        return None

    def __exit__(self, *_: object) -> bool:
        return True


@pytest.fixture(scope="session")
def postgres_dsn() -> Generator[str]:
    dsn = os.environ.get("TEST_POSTGRES_DSN")
    if dsn:
        os.environ["POSTGRES_DSN"] = dsn
        yield dsn
        return

    if shutil.which("initdb") is None or shutil.which("pg_ctl") is None:
        pytest.skip("No TEST_POSTGRES_DSN and no local Postgres (initdb/pg_ctl) available")

    pg = _EphemeralPostgres()
    try:
        dsn = pg.start()
    except subprocess.CalledProcessError as exc:  # pragma: no cover - environment dependent
        pg.stop()
        pytest.skip(f"Could not start ephemeral Postgres: {exc.stderr.decode(errors='ignore')[:300]}")
    os.environ["POSTGRES_DSN"] = dsn
    # Give the socket a moment in slow CI before the first connection.
    time.sleep(0.2)
    try:
        yield dsn
    finally:
        pg.stop()


@pytest.fixture(scope="session")
def _migrated(postgres_dsn: str) -> str:
    env = {**os.environ, "POSTGRES_DSN": postgres_dsn}
    root = Path(__file__).resolve().parent.parent
    subprocess.run(["alembic", "upgrade", "head"], check=True, cwd=root, env=env, capture_output=True)
    return postgres_dsn


@pytest.fixture
async def _clean_db(_migrated: str) -> AsyncGenerator[None]:
    """Truncate domain tables before each test so cases start from an empty schema."""
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    eng = create_async_engine(_migrated)
    async with eng.begin() as conn:
        await conn.execute(
            text(
                "TRUNCATE schedule, weekly_hours, date_override, travel_schedule, "
                "event_type, host, booking_limit, schedule_change_log, booking, booking_change_log, outbox "
                "RESTART IDENTITY CASCADE"
            )
        )
    await eng.dispose()
    return


@pytest.fixture
def app(_migrated: str, _clean_db) -> Generator:
    """Build a fresh FastAPI app + Dishka container per test.

    A new container (and thus a new engine) per test keeps each TestClient's
    asyncpg connections bound to that client's own event loop — sharing one
    engine across loops triggers cross-loop RuntimeErrors. The Prometheus
    collectors live at module level so they register exactly once regardless.
    The wiring (AppProvider) is the production wiring, pointed at the test DB.
    """
    from dishka import make_async_container
    from dishka.integrations.fastapi import FastapiProvider, setup_dishka
    from fastapi import FastAPI

    from event_scheduling.errors import ConflictError, NotFoundError, ValidationError
    from event_scheduling.ioc import AppProvider
    from event_scheduling.main import _domain_error_handler
    from event_scheduling.metrics import HttpMetricsMiddleware
    from event_scheduling.routers.booking import booking_router
    from event_scheduling.routers.booking_field import booking_field_router
    from event_scheduling.routers.calendar import calendar_router
    from event_scheduling.routers.event_type import event_type_router
    from event_scheduling.routers.schedule import schedule_router
    from event_scheduling.routers.slots import slots_router
    from event_scheduling.routes import root_router

    container = make_async_container(AppProvider(), FastapiProvider())
    application = FastAPI()
    setup_dishka(container=container, app=application)
    application.include_router(root_router)
    application.include_router(schedule_router)
    application.include_router(event_type_router)
    application.include_router(booking_field_router)
    application.include_router(slots_router)
    application.include_router(booking_router)
    application.include_router(calendar_router)
    application.add_middleware(HttpMetricsMiddleware)
    for _err in (ValidationError, NotFoundError, ConflictError):
        application.add_exception_handler(_err, _domain_error_handler)
    return application


@pytest.fixture
def client(app) -> Generator:
    from starlette.testclient import TestClient

    with TestClient(app) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {API_KEY}"})
        yield test_client


@pytest.fixture
def bookable_event_type(client) -> tuple[str, str, str]:
    """Seed a bookable schedule + single-host event type via HTTP.

    Mirrors test_booking_api.py's ``_seed_single_host_et_http``. Returns
    ``(event_type_id, client_user_id, a_valid_start_time_iso)`` — a fresh
    random client_user_id and a start_time (Thursday 09:00 Europe/Berlin, within
    the seeded weekly hours) that ``POST /api/v1/bookings`` can successfully book.
    """
    from uuid import uuid4

    owner = str(uuid4())
    client.put(
        f"/api/v1/schedules/{owner}",
        json={
            "name": "s",
            "time_zone": "Europe/Berlin",
            "weekly_hours": [{"day_of_week": 4, "start_time": "09:00", "end_time": "17:00"}],  # Thursday
            "date_overrides": [],
        },
        headers={"actor-source": "admin"},
    )
    sid = client.get(f"/api/v1/schedules/{owner}").json()["schedule"]["id"]
    body = {
        "slug": f"et-{uuid4().hex[:8]}",
        "title": "Intro",
        "duration_minutes": 60,
        "slot_interval_minutes": 30,
        "min_booking_notice_minutes": 0,
        "buffer_before_minutes": 0,
        "buffer_after_minutes": 0,
        "hosts": [{"user_id": owner, "schedule_id": sid}],
        "booking_limits": [],
    }
    et_id = client.post("/api/v1/event-types", json=body).json()["id"]
    return et_id, str(uuid4()), "2026-10-01T09:00:00Z"


@pytest.fixture
def unauth_client(app) -> Generator:
    from starlette.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client_fake_users(_migrated: str, _clean_db) -> Generator:
    """Build the same wiring as `app`/`client`, but with `IUsersClient` overridden by a deterministic fake.

    No live event-users is present in this test harness, so any test exercising
    a route that resolves participant details via `IUsersClient.by_ids` (e.g.
    `GET /bookings/{id}/detail`) needs a fake that always resolves — real
    `UsersClient` would attempt an HTTP call and fail/hang.
    """
    from dishka import Provider, Scope, make_async_container, provide
    from dishka.integrations.fastapi import FastapiProvider, setup_dishka
    from fastapi import FastAPI
    from starlette.testclient import TestClient

    from event_scheduling.errors import ConflictError, NotFoundError, ValidationError
    from event_scheduling.ioc import AppProvider
    from event_scheduling.main import _domain_error_handler
    from event_scheduling.metrics import HttpMetricsMiddleware
    from event_scheduling.publishing.dto import ParticipantInfo
    from event_scheduling.publishing.interfaces import IUsersClient
    from event_scheduling.routers.booking import booking_router
    from event_scheduling.routers.booking_field import booking_field_router
    from event_scheduling.routers.calendar import calendar_router
    from event_scheduling.routers.event_type import event_type_router
    from event_scheduling.routers.schedule import schedule_router
    from event_scheduling.routers.slots import slots_router
    from event_scheduling.routes import root_router

    class _FakeUsersClient:
        async def by_ids(self, user_ids):
            return {
                uid: ParticipantInfo(email=f"{uid}@x.io", time_zone="Europe/Berlin", name="N", locale="en")
                for uid in user_ids
            }

    class FakeUsersProvider(Provider):
        @provide(scope=Scope.APP, override=True)
        def provide_users_client(self) -> IUsersClient:
            return _FakeUsersClient()

    container = make_async_container(AppProvider(), FakeUsersProvider(), FastapiProvider())
    application = FastAPI()
    setup_dishka(container=container, app=application)
    application.include_router(root_router)
    application.include_router(schedule_router)
    application.include_router(event_type_router)
    application.include_router(booking_field_router)
    application.include_router(slots_router)
    application.include_router(booking_router)
    application.include_router(calendar_router)
    application.add_middleware(HttpMetricsMiddleware)
    for _err in (ValidationError, NotFoundError, ConflictError):
        application.add_exception_handler(_err, _domain_error_handler)

    with TestClient(application) as test_client:
        test_client.headers.update({"Authorization": f"Bearer {API_KEY}"})
        yield test_client


@pytest.fixture
async def calcom_dsn(postgres_dsn: str) -> AsyncGenerator[str]:
    """Create and seed a minimal cal.com-schema fixture DB; yield its asyncpg DSN; drop on teardown.

    Three tables mirror the cal.com source schema that ``run_etl`` reads:
    ``users``, ``"Schedule"``, ``"Availability"``.

    Seed: one organizer (org@example.com) with a default schedule (id=10)
    containing a recurring availability row (days={1,3}) and one date-override,
    plus one non-default schedule (id=11) to verify it is skipped.
    """
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine

    base = postgres_dsn.rsplit("/", 1)[0]
    fixture_dsn = base + "/calcom_fixture"

    # CREATE DATABASE cannot run inside a transaction — use AUTOCOMMIT on the app DB.
    admin_eng = create_async_engine(postgres_dsn, isolation_level="AUTOCOMMIT")
    async with admin_eng.connect() as conn:
        await conn.execute(text("DROP DATABASE IF EXISTS calcom_fixture"))
        await conn.execute(text("CREATE DATABASE calcom_fixture"))
    await admin_eng.dispose()

    fix_eng = create_async_engine(fixture_dsn)
    async with fix_eng.begin() as conn:
        await conn.execute(
            text(
                """
                CREATE TABLE users (
                    id int PRIMARY KEY,
                    email text NOT NULL,
                    "timeZone" text NOT NULL,
                    "defaultScheduleId" int
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE "Schedule" (
                    id int PRIMARY KEY,
                    "userId" int NOT NULL,
                    "timeZone" text
                )
                """
            )
        )
        await conn.execute(
            text(
                """
                CREATE TABLE "Availability" (
                    id int PRIMARY KEY,
                    "scheduleId" int NOT NULL,
                    days int[],
                    "startTime" time,
                    "endTime" time,
                    date date
                )
                """
            )
        )
        # Organizer: email=org@example.com, defaultScheduleId=10
        await conn.execute(
            text(
                'INSERT INTO users (id, email, "timeZone", "defaultScheduleId")'
                " VALUES (1, 'org@example.com', 'UTC', 10)"
            )
        )
        # Default schedule (id=10) — will be migrated
        await conn.execute(text('INSERT INTO "Schedule" (id, "userId", "timeZone") VALUES (10, 1, \'Europe/Berlin\')'))
        # Non-default schedule (id=11) — must be skipped with a report entry
        await conn.execute(text('INSERT INTO "Schedule" (id, "userId", "timeZone") VALUES (11, 1, \'Europe/Berlin\')'))
        # Recurring availability for default schedule: Mon+Wed, 09:00-17:00
        await conn.execute(
            text(
                'INSERT INTO "Availability" (id, "scheduleId", days, "startTime", "endTime", date)'
                " VALUES (1, 10, ARRAY[1,3]::int[], '09:00', '17:00', NULL)"
            )
        )
        # Date override for default schedule: 2026-01-01, 10:00-14:00
        await conn.execute(
            text(
                'INSERT INTO "Availability" (id, "scheduleId", days, "startTime", "endTime", date)'
                " VALUES (2, 10, NULL, '10:00', '14:00', '2026-01-01')"
            )
        )
    await fix_eng.dispose()

    yield fixture_dsn

    # Teardown: drop the fixture DB (run_etl disposes its engine so no live connections remain)
    admin_eng2 = create_async_engine(postgres_dsn, isolation_level="AUTOCOMMIT")
    async with admin_eng2.connect() as conn2:
        await conn2.execute(text("DROP DATABASE IF EXISTS calcom_fixture"))
    await admin_eng2.dispose()


@pytest.fixture
async def sessionmaker_fixture(_migrated: str):
    """Return an async_sessionmaker bound to the migrated test DB.

    Used by the slots read-adapter integration tests to drive the adapter
    directly (outside of the Dishka DI container / TestClient lifecycle).
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(_migrated)
    sm = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sm
    await engine.dispose()
