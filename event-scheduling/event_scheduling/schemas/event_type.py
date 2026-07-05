from uuid import UUID

from pydantic import BaseModel

from event_scheduling.dto.event_type import BookingLimitDTO, EventTypeDTO, HostDTO, UpsertEventTypeDTO


class HostModel(BaseModel):
    user_id: UUID
    schedule_id: UUID


class BookingLimitModel(BaseModel):
    limit_type: str
    period: str
    value: int


class UpsertEventTypeRequest(BaseModel):
    slug: str
    title: str
    scheduling_type: str = "round_robin"
    duration_minutes: int
    slot_interval_minutes: int | None = None
    min_booking_notice_minutes: int = 0
    buffer_before_minutes: int = 0
    buffer_after_minutes: int = 0
    hosts: list[HostModel] = []
    booking_limits: list[BookingLimitModel] = []

    def to_dto(self) -> UpsertEventTypeDTO:
        return UpsertEventTypeDTO(
            slug=self.slug,
            title=self.title,
            scheduling_type=self.scheduling_type,
            duration_minutes=self.duration_minutes,
            slot_interval_minutes=self.slot_interval_minutes,
            min_booking_notice_minutes=self.min_booking_notice_minutes,
            buffer_before_minutes=self.buffer_before_minutes,
            buffer_after_minutes=self.buffer_after_minutes,
            hosts=[HostDTO(user_id=h.user_id, schedule_id=h.schedule_id) for h in self.hosts],
            booking_limits=[
                BookingLimitDTO(limit_type=b.limit_type, period=b.period, value=b.value) for b in self.booking_limits
            ],
        )


class EventTypeResponse(BaseModel):
    id: UUID
    slug: str
    title: str
    scheduling_type: str
    duration_minutes: int
    slot_interval_minutes: int | None
    min_booking_notice_minutes: int
    buffer_before_minutes: int
    buffer_after_minutes: int
    hosts: list[HostModel]
    booking_limits: list[BookingLimitModel]

    @classmethod
    def from_dto(cls, dto: EventTypeDTO) -> EventTypeResponse:
        return cls(
            id=dto.id,
            slug=dto.slug,
            title=dto.title,
            scheduling_type=dto.scheduling_type,
            duration_minutes=dto.duration_minutes,
            slot_interval_minutes=dto.slot_interval_minutes,
            min_booking_notice_minutes=dto.min_booking_notice_minutes,
            buffer_before_minutes=dto.buffer_before_minutes,
            buffer_after_minutes=dto.buffer_after_minutes,
            hosts=[HostModel(user_id=h.user_id, schedule_id=h.schedule_id) for h in dto.hosts],
            booking_limits=[
                BookingLimitModel(limit_type=b.limit_type, period=b.period, value=b.value) for b in dto.booking_limits
            ],
        )


class EventTypeListResponse(BaseModel):
    items: list[EventTypeResponse]
