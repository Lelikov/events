from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Header, status

from event_organizer.config import Settings
from event_organizer.errors import Unauthorized
from event_organizer.schemas.admin import CreateOrganizerRequest, OrganizerCreatedResponse
from event_organizer.services.provisioning_service import ProvisioningService

admin_router = APIRouter(prefix="/admin", tags=["admin"], route_class=DishkaRoute)


@admin_router.post("/organizers", response_model=OrganizerCreatedResponse, status_code=status.HTTP_201_CREATED)
async def create_organizer(
    body: CreateOrganizerRequest,
    service: FromDishka[ProvisioningService],
    settings: FromDishka[Settings],
    authorization: Annotated[str, Header()] = "",
) -> OrganizerCreatedResponse:
    expected = f"Bearer {settings.organizer_admin_key}"
    if authorization != expected:
        raise Unauthorized("invalid admin key")
    created = await service.create(body.user_id, str(body.email), body.password)
    return OrganizerCreatedResponse(id=created.id, user_id=created.user_id, email=created.email)
