from uuid import uuid4

import pytest

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
        assert c.user_id == uid
        assert c.email == "a@b.io"
        assert c.disabled is False

    async with sessionmaker_fixture() as s:
        a = CredentialAdapter(SqlExecutor(s))
        got = await a.get_by_email("a@b.io")
        assert got is not None
        assert got.password_hash == "hash1"
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
