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
    from fastapi import FastAPI

    from event_organizer.adapters.interfaces import IUsersClient
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
    from starlette.testclient import TestClient

    with TestClient(_app()) as c:
        uid = str(uuid4())
        email = f"org-{uuid4()}@x.io"
        r = c.post(
            "/admin/organizers",
            json={"user_id": uid, "email": email, "password": "pw12345"},
            headers={"Authorization": f"Bearer {ADMIN}"},
        )
        assert r.status_code == 201
        # login works
        lr = c.post("/auth/login", json={"email": email, "password": "pw12345"})
        assert lr.status_code == 200
        assert lr.json()["access_token"]
        # wrong password
        assert c.post("/auth/login", json={"email": email, "password": "bad"}).status_code == 401
        # dup provision
        assert (
            c.post(
                "/admin/organizers",
                json={"user_id": str(uuid4()), "email": email, "password": "x"},
                headers={"Authorization": f"Bearer {ADMIN}"},
            ).status_code
            == 409
        )
        # bad admin key
        assert (
            c.post(
                "/admin/organizers",
                json={"user_id": str(uuid4()), "email": f"y-{uuid4()}@x.io", "password": "x"},
                headers={"Authorization": "Bearer nope"},
            ).status_code
            == 401
        )


@pytest.mark.asyncio
async def test_provision_non_organizer_422(sessionmaker_fixture) -> None:
    from starlette.testclient import TestClient

    with TestClient(_app(users_organizer=False)) as c:
        r = c.post(
            "/admin/organizers",
            json={"user_id": str(uuid4()), "email": f"z-{uuid4()}@x.io", "password": "pw"},
            headers={"Authorization": f"Bearer {ADMIN}"},
        )
        assert r.status_code == 422
