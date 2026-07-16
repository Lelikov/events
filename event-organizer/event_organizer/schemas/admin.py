from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, EmailStr


class CreateOrganizerRequest(BaseModel):
    user_id: UUID
    email: EmailStr
    password: str


class OrganizerCreatedResponse(BaseModel):
    id: UUID
    user_id: UUID
    email: str
