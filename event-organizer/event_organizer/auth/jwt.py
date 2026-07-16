from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

import jwt

from event_organizer.auth.identity import OrganizerIdentity
from event_organizer.config import Settings
from event_organizer.errors import Unauthorized


def create_access_token(settings: Settings, *, user_id: UUID, email: str) -> str:
    expire = datetime.now(UTC) + timedelta(minutes=settings.jwt_expire_minutes)
    claims: dict[str, Any] = {"sub": str(user_id), "email": email, "exp": expire}
    if settings.jwt_audience:
        claims["aud"] = settings.jwt_audience
    if settings.jwt_issuer:
        claims["iss"] = settings.jwt_issuer
    return jwt.encode(claims, settings.jwt_secret_key, algorithm=settings.jwt_algorithm)


def decode_token(settings: Settings, token: str) -> OrganizerIdentity:
    options = {"verify_aud": bool(settings.jwt_audience)}
    kwargs: dict[str, Any] = {"options": options, "algorithms": [settings.jwt_algorithm]}
    if settings.jwt_audience:
        kwargs["audience"] = settings.jwt_audience
    if settings.jwt_issuer:
        kwargs["issuer"] = settings.jwt_issuer
    try:
        payload = jwt.decode(token, settings.jwt_secret_key, **kwargs)
    except jwt.PyJWTError as exc:
        raise Unauthorized("invalid or expired token") from exc
    return OrganizerIdentity(user_id=UUID(payload["sub"]), email=payload["email"])
