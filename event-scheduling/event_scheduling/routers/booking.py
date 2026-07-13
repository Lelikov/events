from datetime import datetime
from typing import Annotated
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Header, Query, status

from event_scheduling.auth import require_api_key
from event_scheduling.booking.dto import CreateBookingDTO
from event_scheduling.booking.interfaces import IBookingDetailService, IBookingService
from event_scheduling.dto.schedule import ActorDTO
from event_scheduling.errors import NotFoundError, ValidationError
from event_scheduling.schemas.booking import (
    BookingDetailResponse,
    BookingHistoryResponse,
    BookingListResponse,
    BookingResponse,
    ChangeEntryModel,
    CreateBookingRequest,
    RescheduleRequest,
)


booking_router = APIRouter(
    prefix="/api/v1/bookings", tags=["bookings"], route_class=DishkaRoute, dependencies=[Depends(require_api_key)]
)


def _actor(source: str, uid: UUID | None) -> ActorDTO:
    return ActorDTO(source=source, user_id=uid)


@booking_router.post("", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
async def create_booking(
    body: CreateBookingRequest,
    service: FromDishka[IBookingService],
    actor_source: Annotated[str, Header()] = "api",
    actor_user_id: Annotated[UUID | None, Header()] = None,
) -> BookingResponse:
    dto = CreateBookingDTO(body.event_type_id, body.client_user_id, body.start_time, body.attendee_time_zone)
    return BookingResponse.from_dto(await service.create(dto, _actor(actor_source, actor_user_id)))


@booking_router.get("", response_model=BookingListResponse)
async def list_bookings(
    service: FromDishka[IBookingService],
    host_user_id: UUID | None = None,
    client_user_id: UUID | None = None,
    from_: Annotated[datetime | None, Query(alias="from")] = None,
    to: Annotated[datetime | None, Query(alias="to")] = None,
) -> BookingListResponse:
    if (host_user_id is None) == (client_user_id is None):
        raise ValidationError("exactly one of host_user_id or client_user_id is required")
    rows = await service.list_by(host_user_id, client_user_id, from_, to)
    return BookingListResponse(bookings=[BookingResponse.from_dto(b) for b in rows])


@booking_router.get("/{booking_id}", response_model=BookingResponse)
async def get_booking(booking_id: UUID, service: FromDishka[IBookingService]) -> BookingResponse:
    return BookingResponse.from_dto(await service.get(booking_id))


@booking_router.post("/{booking_id}/cancel", response_model=BookingResponse)
async def cancel_booking(
    booking_id: UUID,
    service: FromDishka[IBookingService],
    actor_source: Annotated[str, Header()] = "api",
    actor_user_id: Annotated[UUID | None, Header()] = None,
) -> BookingResponse:
    return BookingResponse.from_dto(await service.cancel(booking_id, _actor(actor_source, actor_user_id)))


@booking_router.post("/{booking_id}/reschedule", response_model=BookingResponse)
async def reschedule_booking(
    booking_id: UUID,
    body: RescheduleRequest,
    service: FromDishka[IBookingService],
    actor_source: Annotated[str, Header()] = "api",
    actor_user_id: Annotated[UUID | None, Header()] = None,
) -> BookingResponse:
    return BookingResponse.from_dto(
        await service.reschedule(booking_id, body.start_time, _actor(actor_source, actor_user_id))
    )


@booking_router.get("/{booking_id}/history", response_model=BookingHistoryResponse)
async def booking_history(booking_id: UUID, service: FromDishka[IBookingService]) -> BookingHistoryResponse:
    entries = await service.history(booking_id)
    return BookingHistoryResponse(entries=[ChangeEntryModel(**e.__dict__) for e in entries])


@booking_router.get("/{booking_id}/detail", response_model=BookingDetailResponse)
async def booking_detail(booking_id: UUID, service: FromDishka[IBookingDetailService]) -> BookingDetailResponse:
    detail = await service.detail(booking_id)
    if detail is None:
        raise NotFoundError(f"booking {booking_id} not found")
    return BookingDetailResponse.from_dto(detail)
