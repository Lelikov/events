from datetime import datetime, timedelta
from typing import Annotated
from uuid import UUID
from zoneinfo import ZoneInfo

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends

from event_organizer.adapters.interfaces import ISchedulingClient, IUsersClient
from event_organizer.auth.identity import OrganizerIdentity, require_organizer
from event_organizer.errors import NotFoundError
from event_organizer.schemas.me import (
    BookingDetailItem,
    BookingFieldAnswer,
    BookingItem,
    BookingSlotsResponse,
    PasswordChangeRequest,
    ProfilePutRequest,
    ProfileResponse,
    ReassignRequest,
    ReassignTarget,
    RescheduleRequest,
    SchedulePutRequest,
)
from event_organizer.services.password_change_service import PasswordChangeService
from event_organizer.services.profile_service import ProfileService

me_router = APIRouter(prefix="/api/me", tags=["me"], route_class=DishkaRoute)

RequireOrganizer = Annotated[OrganizerIdentity, Depends(require_organizer)]


def _day_window_utc(date_str: str, time_zone: str) -> tuple[str, str]:
    tz = ZoneInfo(time_zone)
    day = datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=tz)
    start = day.astimezone(ZoneInfo("UTC"))
    end = (day + timedelta(days=1)).astimezone(ZoneInfo("UTC"))
    return start.strftime("%Y-%m-%dT%H:%M:%SZ"), end.strftime("%Y-%m-%dT%H:%M:%SZ")


async def _owned_row(scheduling: ISchedulingClient, user_id: UUID, booking_id: str) -> dict:
    # Ownership by construction: the booking must be one of this organizer's own.
    rows = await scheduling.get_bookings(user_id)
    row = next((r for r in rows if r["id"] == booking_id), None)
    if row is None:
        raise NotFoundError("booking not found")
    return row


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
    # Ownership by construction — the id is opaque here; event-scheduling validates
    # the UUID downstream.
    row = await _owned_row(scheduling, me.user_id, booking_id)
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


@me_router.get("/bookings/{booking_id}/slots", response_model=BookingSlotsResponse)
async def get_booking_slots(
    booking_id: str, date: str, time_zone: str, scheduling: FromDishka[ISchedulingClient], me: RequireOrganizer
) -> BookingSlotsResponse:
    row = await _owned_row(scheduling, me.user_id, booking_id)
    start_iso, end_iso = _day_window_utc(date, time_zone)
    result = await scheduling.get_slots(row["event_type_id"], start_iso, end_iso, time_zone)
    slots = result.get("slots", {}).get(date, [])
    return BookingSlotsResponse(date=date, time_zone=time_zone, slots=slots)


@me_router.post("/bookings/{booking_id}/reschedule")
async def reschedule_booking(
    booking_id: str, body: RescheduleRequest, scheduling: FromDishka[ISchedulingClient], me: RequireOrganizer
) -> dict:
    await _owned_row(scheduling, me.user_id, booking_id)
    return await scheduling.reschedule_booking(booking_id, body.start_time, me.user_id)


@me_router.get("/bookings/{booking_id}/reassign-targets", response_model=list[ReassignTarget])
async def reassign_targets(
    booking_id: str,
    scheduling: FromDishka[ISchedulingClient],
    users: FromDishka[IUsersClient],
    me: RequireOrganizer,
) -> list[ReassignTarget]:
    row = await _owned_row(scheduling, me.user_id, booking_id)
    event_type = await scheduling.get_event_type(row["event_type_id"])
    current = row["host_user_id"]
    targets: list[ReassignTarget] = []
    for host in event_type.get("hosts", []):
        if host["user_id"] == current:
            continue
        user = await users.get_user(UUID(host["user_id"]))
        targets.append(ReassignTarget(user_id=host["user_id"], name=user.get("name"), email=user["email"]))
    return targets


@me_router.post("/bookings/{booking_id}/reassign")
async def reassign_booking(
    booking_id: str, body: ReassignRequest, scheduling: FromDishka[ISchedulingClient], me: RequireOrganizer
) -> dict:
    await _owned_row(scheduling, me.user_id, booking_id)
    return await scheduling.reassign_booking(booking_id, body.new_host_user_id, me.user_id)


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
