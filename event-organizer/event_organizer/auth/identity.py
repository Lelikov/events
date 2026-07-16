from dataclasses import dataclass
from uuid import UUID

from dishka.integrations.fastapi import FromDishka
from starlette.requests import Request

from event_organizer.config import Settings
from event_organizer.errors import Unauthorized


@dataclass(frozen=True)
class OrganizerIdentity:
    user_id: UUID
    email: str


def require_organizer(request: Request, settings: FromDishka[Settings]) -> OrganizerIdentity:
    # Deferred import breaks the identity<->jwt circular import (jwt.py imports OrganizerIdentity
    # from this module at module load time).
    from event_organizer.auth.jwt import decode_token  # noqa: PLC0415

    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        raise Unauthorized("missing bearer token")
    return decode_token(settings, header[len(prefix) :])
