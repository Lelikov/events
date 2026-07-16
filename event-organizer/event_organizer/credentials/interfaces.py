from typing import Protocol
from uuid import UUID

from event_organizer.credentials.dto import OrganizerCredentialDTO


class ICredentialAdapter(Protocol):
    async def get_by_email(self, email: str) -> OrganizerCredentialDTO | None: ...
    async def create(self, user_id: UUID, email: str, password_hash: str) -> OrganizerCredentialDTO: ...
    async def update_password_hash(self, user_id: UUID, password_hash: str) -> None: ...
