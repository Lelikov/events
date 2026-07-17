from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends

from event_scheduling.auth import require_api_key
from event_scheduling.booking_fields.interfaces import IBookingFieldController
from event_scheduling.schemas.booking_field import (
    BookingFieldListResponse,
    BookingFieldModel,
    ReplaceBookingFieldsRequest,
)


booking_field_router = APIRouter(
    prefix="/api/v1/event-types",
    tags=["booking-fields"],
    route_class=DishkaRoute,
    dependencies=[Depends(require_api_key)],
)


@booking_field_router.get("/{event_type_id}/booking-fields", response_model=BookingFieldListResponse)
async def get_booking_fields(
    event_type_id: UUID, controller: FromDishka[IBookingFieldController]
) -> BookingFieldListResponse:
    fields = await controller.list_for(event_type_id)
    return BookingFieldListResponse(items=[BookingFieldModel.from_dto(f) for f in fields])


@booking_field_router.put("/{event_type_id}/booking-fields", response_model=BookingFieldListResponse)
async def replace_booking_fields(
    event_type_id: UUID, body: ReplaceBookingFieldsRequest, controller: FromDishka[IBookingFieldController]
) -> BookingFieldListResponse:
    fields = await controller.replace(event_type_id, [i.to_dto() for i in body.items])
    return BookingFieldListResponse(items=[BookingFieldModel.from_dto(f) for f in fields])
