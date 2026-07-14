from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, status

from event_scheduling.auth import require_api_key
from event_scheduling.calendar.interfaces import (
    ICalendarReadAdapter,
    ICalendarWriteAdapter,
    IICalClient,
    IICalParser,
)
from event_scheduling.calendar.sync_service import sync_calendar
from event_scheduling.config import Settings
from event_scheduling.errors import NotFoundError, ValidationError
from event_scheduling.interfaces.sql import ISqlExecutor
from event_scheduling.schemas.calendar import CalendarListResponse, CalendarResponse, CreateCalendarRequest
from event_scheduling.slots.interfaces import Clock


calendar_router = APIRouter(
    prefix="/api/v1/calendars", tags=["calendars"], route_class=DishkaRoute, dependencies=[Depends(require_api_key)]
)

_ALLOWED_SCHEMES = ("http://", "https://")


@calendar_router.post("", response_model=CalendarResponse, status_code=status.HTTP_201_CREATED)
async def create_calendar(body: CreateCalendarRequest, write: FromDishka[ICalendarWriteAdapter]) -> CalendarResponse:
    if not body.url.startswith(_ALLOWED_SCHEMES):
        raise ValidationError("url must be http(s)")
    return CalendarResponse.from_dto(await write.create(body.host_user_id, body.url))


@calendar_router.get("", response_model=CalendarListResponse)
async def list_calendars(host_user_id: UUID, read: FromDishka[ICalendarReadAdapter]) -> CalendarListResponse:
    rows = await read.list_by_host(host_user_id)
    return CalendarListResponse(items=[CalendarResponse.from_dto(c) for c in rows])


@calendar_router.delete("/{calendar_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_calendar(calendar_id: UUID, write: FromDishka[ICalendarWriteAdapter]) -> None:
    await write.delete(calendar_id)


@calendar_router.post("/{calendar_id}/sync", response_model=CalendarResponse)
async def sync_now(
    calendar_id: UUID,
    read: FromDishka[ICalendarReadAdapter],
    sql: FromDishka[ISqlExecutor],
    client: FromDishka[IICalClient],
    parser: FromDishka[IICalParser],
    clock: FromDishka[Clock],
    settings: FromDishka[Settings],
) -> CalendarResponse:
    calendar = await read.get(calendar_id)
    if calendar is None:
        raise NotFoundError("calendar not found")
    await sync_calendar(sql, client, parser, clock, calendar, settings.calendar_sync_window_days)
    refreshed = await read.get(calendar_id)
    return CalendarResponse.from_dto(refreshed)
