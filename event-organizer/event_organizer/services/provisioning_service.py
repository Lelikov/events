from uuid import UUID

from event_organizer.adapters.interfaces import IUsersClient
from event_organizer.auth.password import PasswordService
from event_organizer.credentials.dto import OrganizerCredentialDTO
from event_organizer.credentials.interfaces import ICredentialAdapter
from event_organizer.errors import ValidationError


class ProvisioningService:
    def __init__(self, credentials: ICredentialAdapter, passwords: PasswordService, users: IUsersClient) -> None:
        self._credentials = credentials
        self._passwords = passwords
        self._users = users

    async def create(self, user_id: UUID, email: str, password: str) -> OrganizerCredentialDTO:
        resolved = await self._users.resolve_organizer(email)
        if resolved is None:
            raise ValidationError("not an organizer in event-users")
        if resolved != user_id:
            raise ValidationError("user_id does not match the organizer for this email")
        return await self._credentials.create(user_id, email, self._passwords.hash(password))
