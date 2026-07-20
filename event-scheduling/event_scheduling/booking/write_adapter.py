import json
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from event_scheduling.booking.dto import BookingDTO
from event_scheduling.booking_fields.dto import AnsweredFieldDTO
from event_scheduling.dto.schedule import ActorDTO
from event_scheduling.errors import ConflictError
from event_scheduling.interfaces.sql import ISqlExecutor


_COLS = (
    "id, event_type_id, host_user_id, client_user_id, start_time, end_time, status, attendee_time_zone, "
    "created_at, field_answers"
)


def _row_to_dto(r) -> BookingDTO:  # noqa: ANN001
    return BookingDTO(
        id=r["id"],
        event_type_id=r["event_type_id"],
        host_user_id=r["host_user_id"],
        client_user_id=r["client_user_id"],
        start_time=r["start_time"],
        end_time=r["end_time"],
        status=r["status"],
        attendee_time_zone=r["attendee_time_zone"],
        created_at=r["created_at"],
        field_answers=[
            AnsweredFieldDTO(key=x["key"], label=x["label"], field_type=x["type"], value=x["value"])
            for x in (r["field_answers"] or [])
        ],
    )


class BookingWriteAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def insert(
        self,
        event_type_id: UUID,
        host_user_id: UUID,
        client_user_id: UUID,
        start: datetime,
        end: datetime,
        tz: str,
        field_answers: list[AnsweredFieldDTO],
    ) -> BookingDTO:
        # Each attempt runs inside its own SAVEPOINT (nested transaction). The
        # exclusion constraint (ex_booking_no_overlap) raises IntegrityError when the
        # host is no longer free; BookingService.create retries the NEXT ranked host.
        # A bare IntegrityError aborts the OUTER transaction, so without begin_nested()
        # here every subsequent statement (including the next insert attempt) would
        # fail with "current transaction is aborted" for the rest of the request.
        # The `async with` block rolls back just the SAVEPOINT on error, leaving the
        # outer transaction (and the session) healthy for the caller's next attempt.
        answers_json = json.dumps(
            [{"key": a.key, "label": a.label, "type": a.field_type, "value": a.value} for a in field_answers]
        )
        try:
            async with self._sql.begin_nested():
                row = await self._sql.fetch_one(
                    f"""
                    INSERT INTO booking (event_type_id, host_user_id, client_user_id, start_time, end_time,
                                          attendee_time_zone, field_answers)
                    VALUES (:et, :h, :c, :s, :e, :tz, CAST(:fa AS JSONB)) RETURNING {_COLS}
                    """,  # noqa: S608
                    {
                        "et": event_type_id,
                        "h": host_user_id,
                        "c": client_user_id,
                        "s": start,
                        "e": end,
                        "tz": tz,
                        "fa": answers_json,
                    },
                )
        except IntegrityError as e:
            raise ConflictError("slot taken") from e
        return _row_to_dto(row)

    async def update_times(self, booking_id: UUID, start: datetime, end: datetime) -> BookingDTO:
        # Same SAVEPOINT rationale as insert(): a concurrent same-host booking can
        # take the slot between the service's availability re-check and this UPDATE,
        # tripping the exclusion constraint. Wrap in begin_nested() and map the
        # resulting IntegrityError to ConflictError so the caller sees 409, not a
        # 500 from an uncaught IntegrityError (and so the outer transaction survives).
        try:
            async with self._sql.begin_nested():
                row = await self._sql.fetch_one(
                    f"UPDATE booking SET start_time=:s, end_time=:e, reminder_sent_at=NULL, "  # noqa: S608
                    f"updated_at=now() WHERE id=:id RETURNING {_COLS}",
                    {"id": booking_id, "s": start, "e": end},
                )
        except IntegrityError as e:
            raise ConflictError("slot taken") from e
        return _row_to_dto(row)

    async def update_host(self, booking_id: UUID, new_host_user_id: UUID) -> BookingDTO:
        # Same SAVEPOINT rationale as update_times(): the new host may take an
        # overlapping slot between the service's availability check and this UPDATE,
        # tripping the exclusion constraint. reminder_sent_at is intentionally NOT
        # reset — a pending reminder resolves the host at send time and reaches the
        # new host, and the reassignment itself notifies both parties.
        try:
            async with self._sql.begin_nested():
                row = await self._sql.fetch_one(
                    f"UPDATE booking SET host_user_id=:h, updated_at=now() WHERE id=:id RETURNING {_COLS}",  # noqa: S608
                    {"id": booking_id, "h": new_host_user_id},
                )
        except IntegrityError as e:
            raise ConflictError("host already has a booking at this time") from e
        return _row_to_dto(row)

    async def set_cancelled(self, booking_id: UUID) -> BookingDTO:
        row = await self._sql.fetch_one(
            f"UPDATE booking SET status='cancelled', updated_at=now() WHERE id=:id RETURNING {_COLS}",  # noqa: S608
            {"id": booking_id},
        )
        return _row_to_dto(row)

    async def append_log(
        self,
        booking_id: UUID,
        kind: str,
        from_start: datetime | None,
        from_end: datetime | None,
        to_start: datetime | None,
        to_end: datetime | None,
        actor: ActorDTO,
    ) -> None:
        await self._sql.execute(
            """
            INSERT INTO booking_change_log (booking_id, kind, from_start, from_end, to_start, to_end,
                                             actor_source, actor_user_id)
            VALUES (:b, :k, :fs, :fe, :ts, :te, :src, :uid)
            """,
            {
                "b": booking_id,
                "k": kind,
                "fs": from_start,
                "fe": from_end,
                "ts": to_start,
                "te": to_end,
                "src": actor.source,
                "uid": actor.user_id,
            },
        )
