from dataclasses import dataclass
from uuid import UUID

from starlette.requests import Request

from event_organizer.config import get_settings
from event_organizer.errors import Unauthorized


@dataclass(frozen=True)
class OrganizerIdentity:
    user_id: UUID
    email: str


def require_organizer(request: Request) -> OrganizerIdentity:
    # Deferred import breaks the identity<->jwt circular import (jwt.py imports OrganizerIdentity
    # from this module at module load time).
    from event_organizer.auth.jwt import decode_token  # noqa: PLC0415

    # require_organizer is wired via plain FastAPI Depends(), not dishka's inject(), so a
    # FromDishka[Settings] param here would never get resolved by the DI container (only the
    # top-level DishkaRoute endpoint params are wrapped) — fall back to the settings singleton.
    settings = get_settings()
    header = request.headers.get("Authorization", "")
    prefix = "Bearer "
    if not header.startswith(prefix):
        raise Unauthorized("missing bearer token")
    return decode_token(settings, header[len(prefix) :])
