import json
from datetime import datetime
from uuid import UUID, uuid4

from event_scheduling.booking.dto import BookingDTO
from event_scheduling.interfaces.sql import ISqlExecutor


class OutboxWriter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def write(
        self,
        event_type: str,
        booking: BookingDTO,
        *,
        previous_start_time: datetime | None = None,
        cancellation_reason: str | None = None,
        previous_host_user_id: UUID | None = None,
    ) -> None:
        payload = {
            "host_user_id": str(booking.host_user_id),
            "client_user_id": str(booking.client_user_id),
            "start_time": booking.start_time.isoformat(),
            "end_time": booking.end_time.isoformat(),
            "attendee_time_zone": booking.attendee_time_zone,
            "field_answers": [
                {"key": a.key, "label": a.label, "type": a.field_type, "value": a.value} for a in booking.field_answers
            ],
        }
        if previous_start_time is not None:
            payload["previous_start_time"] = previous_start_time.isoformat()
        if cancellation_reason is not None:
            payload["cancellation_reason"] = cancellation_reason
        if previous_host_user_id is not None:
            payload["previous_host_user_id"] = str(previous_host_user_id)
        await self._sql.execute(
            """
            INSERT INTO outbox (event_ce_id, event_type, booking_uid, payload)
            VALUES (:ce, :type, :uid, CAST(:payload AS jsonb))
            """,
            {"ce": uuid4(), "type": event_type, "uid": str(booking.id), "payload": json.dumps(payload)},
        )
