from event_organizer.auth.jwt import create_access_token
from event_organizer.auth.password import PasswordService
from event_organizer.config import Settings
from event_organizer.credentials.interfaces import ICredentialAdapter
from event_organizer.errors import Unauthorized


class LoginService:
    def __init__(self, credentials: ICredentialAdapter, passwords: PasswordService, settings: Settings) -> None:
        self._credentials = credentials
        self._passwords = passwords
        self._settings = settings

    async def login(self, email: str, password: str) -> str:
        credential = await self._credentials.get_by_email(email)
        if credential is None or credential.disabled:
            raise Unauthorized("invalid credentials")
        if not self._passwords.verify(password, credential.password_hash):
            raise Unauthorized("invalid credentials")
        return create_access_token(self._settings, user_id=credential.user_id, email=credential.email)
