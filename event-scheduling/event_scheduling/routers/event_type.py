from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, status
from fastapi.responses import Response

from event_scheduling.auth import require_api_key
from event_scheduling.interfaces.event_type import IEventTypeController
from event_scheduling.schemas.event_type import EventTypeListResponse, EventTypeResponse, UpsertEventTypeRequest


event_type_router = APIRouter(
    prefix="/api/v1/event-types",
    tags=["event-types"],
    route_class=DishkaRoute,
    dependencies=[Depends(require_api_key)],
)


@event_type_router.post("", response_model=EventTypeResponse, status_code=status.HTTP_201_CREATED)
async def create_event_type(
    body: UpsertEventTypeRequest,
    controller: FromDishka[IEventTypeController],
) -> EventTypeResponse:
    dto = await controller.create(body.to_dto())
    return EventTypeResponse.from_dto(dto)


@event_type_router.get("", response_model=EventTypeListResponse)
async def list_event_types(
    controller: FromDishka[IEventTypeController],
) -> EventTypeListResponse:
    dtos = await controller.list_all()
    return EventTypeListResponse(items=[EventTypeResponse.from_dto(d) for d in dtos])


@event_type_router.get("/{event_type_id}", response_model=EventTypeResponse)
async def get_event_type(
    event_type_id: UUID,
    controller: FromDishka[IEventTypeController],
) -> EventTypeResponse:
    dto = await controller.get(event_type_id)
    return EventTypeResponse.from_dto(dto)


@event_type_router.put("/{event_type_id}", response_model=EventTypeResponse)
async def update_event_type(
    event_type_id: UUID,
    body: UpsertEventTypeRequest,
    controller: FromDishka[IEventTypeController],
) -> EventTypeResponse:
    dto = await controller.update(event_type_id, body.to_dto())
    return EventTypeResponse.from_dto(dto)


@event_type_router.delete("/{event_type_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_event_type(
    event_type_id: UUID,
    controller: FromDishka[IEventTypeController],
) -> Response:
    await controller.delete(event_type_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)
