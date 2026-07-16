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
