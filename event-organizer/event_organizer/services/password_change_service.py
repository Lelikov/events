from uuid import UUID

from event_organizer.auth.password import PasswordService
from event_organizer.credentials.interfaces import ICredentialAdapter
from event_organizer.errors import Unauthorized


class PasswordChangeService:
    def __init__(self, credentials: ICredentialAdapter, passwords: PasswordService) -> None:
        self._credentials = credentials
        self._passwords = passwords

    async def change(self, user_id: UUID, email: str, old_password: str, new_password: str) -> None:
        credential = await self._credentials.get_by_email(email)
        if credential is None or not self._passwords.verify(old_password, credential.password_hash):
            raise Unauthorized("invalid credentials")
        await self._credentials.update_password_hash(user_id, self._passwords.hash(new_password))
