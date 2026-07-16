from dataclasses import dataclass
from uuid import UUID


@dataclass(frozen=True)
class OrganizerCredentialDTO:
    id: UUID
    user_id: UUID
    email: str
    password_hash: str
    disabled: bool
