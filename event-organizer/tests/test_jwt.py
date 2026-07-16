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
    assert ident.user_id == uid
    assert ident.email == "a@b.io"


def test_garbage_token_rejected() -> None:
    with pytest.raises(Unauthorized):
        decode_token(_settings(), "not-a-jwt")


def test_expired_token_rejected() -> None:
    s = _settings(jwt_expire_minutes=-1)  # already expired
    token = create_access_token(s, user_id=uuid4(), email="a@b.io")
    with pytest.raises(Unauthorized):
        decode_token(s, token)
