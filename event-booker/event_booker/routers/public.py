from datetime import datetime
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter

from event_booker.dto import AnswerDTO
from event_booker.interfaces.clients import ISchedulingClient
from event_booker.schemas.public import (
    BookingConfirmationResponse,
    CreateBookingPublicRequest,
    EventTypeListResponse,
    EventTypeModel,
    SlotsPublicResponse,
)
from event_booker.services.guest_booking import GuestBookingService


public_router = APIRouter(prefix="/api/public", tags=["public"], route_class=DishkaRoute)


@public_router.get("/event-types", response_model=EventTypeListResponse)
async def list_event_types(scheduling: FromDishka[ISchedulingClient]) -> EventTypeListResponse:
    items = await scheduling.list_event_types()
    return EventTypeListResponse(items=[EventTypeModel.from_dto(d) for d in items])


@public_router.get("/event-types/{event_type_id}", response_model=EventTypeModel)
async def get_event_type(event_type_id: UUID, scheduling: FromDishka[ISchedulingClient]) -> EventTypeModel:
    return EventTypeModel.from_dto(await scheduling.get_event_type(event_type_id))


@public_router.get("/slots", response_model=SlotsPublicResponse)
async def get_slots(
    event_type_id: UUID,
    start: datetime,
    end: datetime,
    time_zone: str,
    scheduling: FromDishka[ISchedulingClient],
) -> SlotsPublicResponse:
    return SlotsPublicResponse.from_result(await scheduling.get_slots(event_type_id, start, end, time_zone))


@public_router.post("/bookings", response_model=BookingConfirmationResponse, status_code=201)
async def create_booking(
    body: CreateBookingPublicRequest, service: FromDishka[GuestBookingService]
) -> BookingConfirmationResponse:
    answers = [AnswerDTO(key=a.key, value=a.value) for a in body.answers]
    confirmation = await service.book(
        body.event_type_id, body.name, body.email, body.start_time, body.time_zone, answers=answers
    )
    return BookingConfirmationResponse.from_confirmation(confirmation)
