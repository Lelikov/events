from datetime import datetime
from uuid import UUID

from event_booker.dto import AnswerDTO, BookingConfirmation
from event_booker.interfaces.clients import ISchedulingClient, IUsersClient


class GuestBookingService:
    def __init__(self, scheduling: ISchedulingClient, users: IUsersClient) -> None:
        self._scheduling = scheduling
        self._users = users

    async def book(
        self,
        event_type_id: UUID,
        name: str,
        email: str,
        start_time: datetime,
        time_zone: str,
        answers: list[AnswerDTO] | None = None,
    ) -> BookingConfirmation:
        client_user_id = await self._resolve_client(email, name, time_zone)
        booking = await self._scheduling.create_booking(
            event_type_id, client_user_id, start_time, time_zone, field_answers=answers or []
        )
        event_type = await self._scheduling.get_event_type(event_type_id)
        return BookingConfirmation(
            booking_id=booking.id,
            event_type_title=event_type.title,
            start_time=booking.start_time,
            end_time=booking.end_time,
            status=booking.status,
            time_zone=time_zone,
        )

    async def _resolve_client(self, email: str, name: str, time_zone: str) -> UUID:
        existing = await self._users.get_client_by_email(email)
        if existing is not None:
            return existing
        return await self._users.create_client(email, name, time_zone)
