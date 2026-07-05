from datetime import UTC, datetime, timedelta
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends

from event_scheduling.auth import require_api_key
from event_scheduling.errors import ValidationError
from event_scheduling.schemas.slots import SlotsResponse
from event_scheduling.slots.interfaces import ISlotService
from event_scheduling.validation import validate_time_zone


slots_router = APIRouter(
    prefix="/api/v1/slots", tags=["slots"], route_class=DishkaRoute, dependencies=[Depends(require_api_key)]
)

_MAX_WINDOW_DAYS = 62


def _as_utc(d: datetime) -> datetime:
    if d.tzinfo is None:
        return d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


@slots_router.get("", response_model=SlotsResponse)
async def get_slots(
    event_type_id: UUID,
    start: datetime,
    end: datetime,
    time_zone: str,
    service: FromDishka[ISlotService],
) -> SlotsResponse:
    validate_time_zone(time_zone)
    ws, we = _as_utc(start), _as_utc(end)
    if we <= ws:
        raise ValidationError("end must be after start")
    if we - ws > timedelta(days=_MAX_WINDOW_DAYS):
        raise ValidationError(f"window exceeds {_MAX_WINDOW_DAYS} days")
    slots = await service.available_slots(event_type_id, ws, we, time_zone)
    return SlotsResponse(event_type_id=event_type_id, time_zone=time_zone, slots=slots)
