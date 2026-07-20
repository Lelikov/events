from typing import Annotated

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends

from event_organizer.adapters.interfaces import ISchedulingClient
from event_organizer.auth.identity import OrganizerIdentity, require_organizer
from event_organizer.errors import NotFoundError
from event_organizer.schemas.me import (
    BookingDetailItem,
    BookingFieldAnswer,
    BookingItem,
    PasswordChangeRequest,
    ProfilePutRequest,
    ProfileResponse,
    SchedulePutRequest,
)
from event_organizer.services.password_change_service import PasswordChangeService
from event_organizer.services.profile_service import ProfileService

me_router = APIRouter(prefix="/api/me", tags=["me"], route_class=DishkaRoute)

RequireOrganizer = Annotated[OrganizerIdentity, Depends(require_organizer)]


@me_router.get("/schedule")
async def get_schedule(scheduling: FromDishka[ISchedulingClient], me: RequireOrganizer) -> dict:
    return await scheduling.get_schedule(me.user_id)


@me_router.put("/schedule")
async def put_schedule(
    body: SchedulePutRequest, scheduling: FromDishka[ISchedulingClient], me: RequireOrganizer
) -> dict:
    return await scheduling.put_schedule(me.user_id, body.model_dump(mode="json"))


@me_router.put("/schedule/travel")
async def put_travel(body: dict, scheduling: FromDishka[ISchedulingClient], me: RequireOrganizer) -> dict:
    return await scheduling.put_travel(me.user_id, body)


@me_router.get("/bookings", response_model=list[BookingItem])
async def get_bookings(scheduling: FromDishka[ISchedulingClient], me: RequireOrganizer) -> list[BookingItem]:
    rows = await scheduling.get_bookings(me.user_id)
    return [
        BookingItem(id=r["id"], start_time=r["start_time"], end_time=r["end_time"], status=r["status"]) for r in rows
    ]


def _stringify(value: object) -> str:
    if isinstance(value, bool):
        return "Да" if value else "Нет"
    if isinstance(value, list):
        return ", ".join(str(v) for v in value)
    return str(value)


@me_router.get("/bookings/{booking_id}", response_model=BookingDetailItem)
async def get_booking_detail(
    booking_id: str, scheduling: FromDishka[ISchedulingClient], me: RequireOrganizer
) -> BookingDetailItem:
    # Ownership by construction: the booking id must be one of this organizer's own
    # bookings, else 404 — no cross-organizer read. The id is opaque here;
    # event-scheduling validates the UUID downstream.
    rows = await scheduling.get_bookings(me.user_id)
    row = next((r for r in rows if r["id"] == booking_id), None)
    if row is None:
        raise NotFoundError("booking not found")
    detail = await scheduling.get_booking_detail(booking_id)
    client = detail.get("client") or {}
    return BookingDetailItem(
        id=detail["uid"],
        title=detail["title"],
        start_time=detail["start_time"],
        end_time=detail["end_time"],
        status=detail["status"],
        client_name=client.get("name"),
        client_email=client.get("email"),
        client_time_zone=row.get("attendee_time_zone"),
        created_at=row.get("created_at"),
        field_answers=[
            BookingFieldAnswer(label=a["label"], value=_stringify(a["value"])) for a in row.get("field_answers", [])
        ],
    )


@me_router.get("/profile", response_model=ProfileResponse)
async def get_profile(profile: FromDishka[ProfileService], me: RequireOrganizer) -> ProfileResponse:
    return ProfileResponse(**await profile.get(me.user_id))


@me_router.put("/profile", response_model=ProfileResponse)
async def put_profile(
    body: ProfilePutRequest, profile: FromDishka[ProfileService], me: RequireOrganizer
) -> ProfileResponse:
    return ProfileResponse(**await profile.update(me.user_id, body.name, body.time_zone))


@me_router.put("/password", status_code=204)
async def change_password(
    body: PasswordChangeRequest, service: FromDishka[PasswordChangeService], me: RequireOrganizer
) -> None:
    await service.change(me.user_id, me.email, body.old_password, body.new_password)
