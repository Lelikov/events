from uuid import UUID

from sqlalchemy.exc import IntegrityError

from event_organizer.credentials.dto import OrganizerCredentialDTO
from event_organizer.errors import ConflictError
from event_organizer.interfaces.sql import ISqlExecutor

_COLS = "id, user_id, email, password_hash, disabled"


def _to_dto(r: dict) -> OrganizerCredentialDTO:
    return OrganizerCredentialDTO(
        id=r["id"], user_id=r["user_id"], email=r["email"], password_hash=r["password_hash"], disabled=r["disabled"]
    )


class CredentialAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def get_by_email(self, email: str) -> OrganizerCredentialDTO | None:
        row = await self._sql.fetch_one(
            f"SELECT {_COLS} FROM organizer_credential WHERE email=:e",  # noqa: S608
            {"e": email},
        )
        if row is None:
            return None
        return _to_dto(row)

    async def create(self, user_id: UUID, email: str, password_hash: str) -> OrganizerCredentialDTO:
        try:
            async with self._sql.begin_nested():
                row = await self._sql.fetch_one(
                    f"INSERT INTO organizer_credential (user_id, email, password_hash) "  # noqa: S608
                    f"VALUES (:u,:e,:h) RETURNING {_COLS}",
                    {"u": user_id, "e": email, "h": password_hash},
                )
        except IntegrityError as exc:
            raise ConflictError("organizer already has credentials") from exc
        return _to_dto(row)

    async def update_password_hash(self, user_id: UUID, password_hash: str) -> None:
        await self._sql.execute(
            "UPDATE organizer_credential SET password_hash=:h, updated_at=now() WHERE user_id=:u",
            {"h": password_hash, "u": user_id},
        )
