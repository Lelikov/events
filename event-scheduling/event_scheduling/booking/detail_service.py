from uuid import UUID

from event_scheduling.booking.dto import BookingDetailDTO, ParticipantDetail
from event_scheduling.booking.interfaces import IBookingReadAdapter
from event_scheduling.publishing.interfaces import IUsersClient


class BookingDetailService:
    def __init__(self, read: IBookingReadAdapter, users: IUsersClient) -> None:
        self._read = read
        self._users = users

    async def detail(self, booking_id: UUID) -> BookingDetailDTO | None:
        booking = await self._read.get(booking_id)
        if booking is None:
            return None
        title = await self._read.event_type_title(booking.event_type_id) or ""
        resolved = await self._users.by_ids([booking.host_user_id, booking.client_user_id])
        host = resolved.get(booking.host_user_id)
        client = resolved.get(booking.client_user_id)
        return BookingDetailDTO(
            uid=str(booking.id),
            title=title,
            start_time=booking.start_time,
            end_time=booking.end_time,
            status=booking.status,
            host=ParticipantDetail(
                email=host.email if host else "",
                name=host.name if host else None,
                time_zone=host.time_zone if host else None,
                locale=host.locale if host else None,
            ),
            client=ParticipantDetail(
                email=client.email if client else "",
                name=client.name if client else None,
                time_zone=booking.attendee_time_zone,
                locale=client.locale if client else None,
            ),
        )
