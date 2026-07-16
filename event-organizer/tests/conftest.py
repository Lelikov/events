"""Real-seam test harness for event-organizer.

The service talks to PostgreSQL through ``SqlExecutor`` running raw SQL.
The suite runs against a real PostgreSQL, not a fake.

It uses whatever the ``TEST_POSTGRES_DSN`` env var points at; failing that it
boots a throwaway local cluster via ``initdb``/``pg_ctl`` (Homebrew Postgres).
If neither is available the whole suite skips rather than failing.
"""

import os

os.environ.setdefault("OTEL_SDK_DISABLED", "true")
os.environ.setdefault("POSTGRES_DSN", os.environ.get("TEST_POSTGRES_DSN", ""))

import shutil
import socket
import subprocess
import tempfile
import time
from collections.abc import AsyncGenerator, Generator
from pathlib import Path

import pytest

os.environ.setdefault("LOG_LEVEL", "WARNING")
os.environ.setdefault("DEBUG", "false")


def _free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class _EphemeralPostgres:
    """A short-lived local Postgres cluster for the test session."""

    def __init__(self) -> None:
        self.tmpdir = Path(tempfile.mkdtemp(prefix="pg-organizer-test-"))
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
            ["createdb", "-h", "127.0.0.1", "-p", str(self.port), "-U", "postgres", "event_organizer"],
            check=True,
            capture_output=True,
        )
        return f"postgresql+asyncpg://postgres@127.0.0.1:{self.port}/event_organizer"

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
        await conn.execute(text("TRUNCATE organizer_credential RESTART IDENTITY CASCADE"))
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

    from event_organizer.errors import (
        ConflictError,
        Forbidden,
        NotFoundError,
        Unauthorized,
        UpstreamError,
        ValidationError,
    )
    from event_organizer.ioc import AppProvider
    from event_organizer.main import _domain_error_handler
    from event_organizer.metrics import HttpMetricsMiddleware
    from event_organizer.routes import root_router

    container = make_async_container(AppProvider(), FastapiProvider())
    application = FastAPI()
    setup_dishka(container=container, app=application)
    application.include_router(root_router)
    application.add_middleware(HttpMetricsMiddleware)
    for _err in (Unauthorized, Forbidden, NotFoundError, ConflictError, ValidationError, UpstreamError):
        application.add_exception_handler(_err, _domain_error_handler)
    return application


@pytest.fixture
def client(app) -> Generator:
    from starlette.testclient import TestClient

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
async def sessionmaker_fixture(_migrated: str):
    """Return an async_sessionmaker bound to the migrated test DB.

    Used by schema/adapter integration tests to drive the DB directly
    (outside of the Dishka DI container / TestClient lifecycle).
    """
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

    engine = create_async_engine(_migrated)
    sm = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)
    yield sm
    await engine.dispose()
