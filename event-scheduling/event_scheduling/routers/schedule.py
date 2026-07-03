from typing import Annotated
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Header, status

from event_scheduling.auth import require_api_key
from event_scheduling.dto.schedule import ActorDTO
from event_scheduling.interfaces.schedule import IScheduleController
from event_scheduling.schemas.schedule import (
    ChangeLogEntryModel,
    ChangeLogResponse,
    ReplaceTravelRequest,
    ScheduleBundleResponse,
    UpsertScheduleRequest,
)


schedule_router = APIRouter(
    prefix="/api/v1/schedules",
    tags=["schedules"],
    route_class=DishkaRoute,
    dependencies=[Depends(require_api_key)],
)


@schedule_router.get("/{owner_user_id}/change-log", response_model=ChangeLogResponse)
async def get_change_log(
    owner_user_id: UUID,
    controller: FromDishka[IScheduleController],
    limit: int = 50,
    offset: int = 0,
) -> ChangeLogResponse:
    entries = await controller.list_change_log(owner_user_id, limit, offset)
    return ChangeLogResponse(entries=[ChangeLogEntryModel(**e.__dict__) for e in entries])


@schedule_router.get("/{owner_user_id}", response_model=ScheduleBundleResponse)
async def get_schedule(owner_user_id: UUID, controller: FromDishka[IScheduleController]) -> ScheduleBundleResponse:
    bundle = await controller.get_schedule(owner_user_id)
    return ScheduleBundleResponse.from_dto(bundle)


@schedule_router.put("/{owner_user_id}", response_model=ScheduleBundleResponse, status_code=status.HTTP_200_OK)
async def put_schedule(
    owner_user_id: UUID,
    body: UpsertScheduleRequest,
    controller: FromDishka[IScheduleController],
    actor_source: Annotated[str, Header()] = "admin",
    actor_user_id: Annotated[UUID | None, Header()] = None,
) -> ScheduleBundleResponse:
    actor = ActorDTO(source=actor_source, user_id=actor_user_id)
    bundle = await controller.upsert_schedule(owner_user_id, body.to_dto(), actor)
    return ScheduleBundleResponse.from_dto(bundle)


@schedule_router.put("/{owner_user_id}/travel", response_model=ScheduleBundleResponse)
async def put_travel(
    owner_user_id: UUID,
    body: ReplaceTravelRequest,
    controller: FromDishka[IScheduleController],
    actor_source: Annotated[str, Header()] = "admin",
    actor_user_id: Annotated[UUID | None, Header()] = None,
) -> ScheduleBundleResponse:
    actor = ActorDTO(source=actor_source, user_id=actor_user_id)
    bundle = await controller.replace_travel(owner_user_id, body.to_dtos(), actor)
    return ScheduleBundleResponse.from_dto(bundle)
