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

    async def put_travel(self, owner_user_id, body):
        return {}

    async def get_bookings(self, host_user_id):
        return [
            {
                "id": "b1",
                "start_time": "2026-10-01T09:00:00Z",
                "end_time": "2026-10-01T09:30:00Z",
                "status": "confirmed",
                "client_user_id": str(uuid4()),
                "host_user_id": str(host_user_id),
            }
        ]


class _FakeUsers:
    def __init__(self) -> None:
        self.patched = None

    async def get_user(self, user_id):
        return {"id": str(user_id), "email": "org@x.io", "name": "Org", "role": "organizer", "time_zone": "UTC"}

    async def patch_user(self, user_id, body):
        self.patched = body
        return {
            "id": str(user_id),
            "email": "org@x.io",
            "name": body["name"],
            "role": "organizer",
            "time_zone": body["time_zone"],
        }

    async def is_organizer(self, email):
        return True


def _app_and_fakes():
    from dishka import Provider, Scope, make_async_container, provide
    from dishka.integrations.fastapi import FastapiProvider, setup_dishka
    from fastapi import FastAPI

    from event_organizer.adapters.interfaces import ISchedulingClient, IUsersClient
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
    from event_organizer.routers.me import me_router
    from event_organizer.routes import root_router

    sched, users = _FakeScheduling(), _FakeUsers()

    class Fakes(Provider):
        @provide(scope=Scope.APP, override=True)
        def s(self) -> ISchedulingClient:
            return sched

        @provide(scope=Scope.APP, override=True)
        def u(self) -> IUsersClient:
            return users

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
        assert "client_user_id" not in item
        assert "host_user_id" not in item


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


@pytest.mark.asyncio
async def test_password_change_success_and_wrong_old(sessionmaker_fixture) -> None:
    from starlette.testclient import TestClient

    from event_organizer.adapters.sql import SqlExecutor
    from event_organizer.auth.password import PasswordService
    from event_organizer.credentials.adapter import CredentialAdapter

    uid = uuid4()
    email = f"pw-{uuid4()}@x.io"
    passwords = PasswordService()

    async with sessionmaker_fixture() as s:
        await CredentialAdapter(SqlExecutor(s)).create(uid, email, passwords.hash("old"))
        await s.commit()

    app, _, _ = _app_and_fakes()
    with TestClient(app) as c:
        # wrong old password -> 401, hash untouched
        wrong = c.put(
            "/api/me/password", headers=_auth(uid, email=email), json={"old_password": "nope", "new_password": "new"}
        )
        assert wrong.status_code == 401

        # correct old password -> 204
        ok = c.put(
            "/api/me/password", headers=_auth(uid, email=email), json={"old_password": "old", "new_password": "new"}
        )
        assert ok.status_code == 204

    async with sessionmaker_fixture() as s:
        credential = await CredentialAdapter(SqlExecutor(s)).get_by_email(email)
        assert passwords.verify("new", credential.password_hash)
        assert not passwords.verify("old", credential.password_hash)
