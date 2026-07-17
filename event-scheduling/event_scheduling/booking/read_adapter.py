from datetime import datetime
from uuid import UUID

from event_scheduling.booking.dto import BookingChangeEntryDTO, BookingDTO, HostStat
from event_scheduling.booking_fields.dto import AnsweredFieldDTO
from event_scheduling.dto.event_type import BookingLimitDTO
from event_scheduling.interfaces.sql import ISqlExecutor


_COLS = (
    "id, event_type_id, host_user_id, client_user_id, start_time, end_time, status, attendee_time_zone, "
    "created_at, field_answers"
)


class BookingReadAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def get(self, booking_id: UUID) -> BookingDTO | None:
        row = await self._sql.fetch_one(f"SELECT {_COLS} FROM booking WHERE id = :id", {"id": booking_id})  # noqa: S608
        if row is None:
            return None
        return self._to_booking(row)

    async def list_by(
        self,
        host_user_id: UUID | None,
        client_user_id: UUID | None,
        from_utc: datetime | None,
        to_utc: datetime | None,
    ) -> list[BookingDTO]:
        rows = await self._sql.fetch_all(
            f"""
            SELECT {_COLS} FROM booking
            WHERE (CAST(:host AS uuid) IS NULL OR host_user_id = CAST(:host AS uuid))
              AND (CAST(:client AS uuid) IS NULL OR client_user_id = CAST(:client AS uuid))
              AND (CAST(:from_utc AS timestamptz) IS NULL OR start_time >= CAST(:from_utc AS timestamptz))
              AND (CAST(:to_utc AS timestamptz) IS NULL OR start_time < CAST(:to_utc AS timestamptz))
            ORDER BY start_time ASC
            """,  # noqa: S608
            {"host": host_user_id, "client": client_user_id, "from_utc": from_utc, "to_utc": to_utc},
        )
        return [self._to_booking(r) for r in rows]

    async def history(self, booking_id: UUID) -> list[BookingChangeEntryDTO]:
        rows = await self._sql.fetch_all(
            "SELECT kind, from_start, from_end, to_start, to_end, actor_source, actor_user_id, at "
            "FROM booking_change_log WHERE booking_id = :id ORDER BY at ASC, id ASC",
            {"id": booking_id},
        )
        return [
            BookingChangeEntryDTO(
                kind=r["kind"],
                from_start=r["from_start"],
                from_end=r["from_end"],
                to_start=r["to_start"],
                to_end=r["to_end"],
                actor_source=r["actor_source"],
                actor_user_id=r["actor_user_id"],
                at=r["at"],
            )
            for r in rows
        ]

    async def limits(self, event_type_id: UUID) -> list[BookingLimitDTO]:
        rows = await self._sql.fetch_all(
            "SELECT limit_type, period, value FROM booking_limit WHERE event_type_id = :et",
            {"et": event_type_id},
        )
        return [BookingLimitDTO(limit_type=r["limit_type"], period=r["period"], value=r["value"]) for r in rows]

    async def host_stats(self, user_ids: list[UUID], now: datetime) -> list[HostStat]:
        rows = await self._sql.fetch_all(
            """
            SELECT u AS user_id,
                   (SELECT count(*) FROM booking b
                    WHERE b.host_user_id = u AND b.status = 'confirmed' AND b.start_time >= :now) AS future_count,
                   (SELECT max(created_at) FROM booking b
                    WHERE b.host_user_id = u AND b.status = 'confirmed') AS last_assigned_at
            FROM unnest(CAST(:users AS uuid[])) AS u
            """,
            {"users": user_ids, "now": now},
        )
        return [
            HostStat(user_id=r["user_id"], future_count=r["future_count"], last_assigned_at=r["last_assigned_at"])
            for r in rows
        ]

    async def period_counts(self, event_type_id: UUID, lo: datetime, hi: datetime) -> tuple[int, int]:
        row = await self._sql.fetch_one(
            """
            SELECT count(*) AS c, COALESCE(sum(EXTRACT(EPOCH FROM (end_time - start_time)) / 60), 0)::int AS mins
            FROM booking
            WHERE event_type_id = :et AND status = 'confirmed' AND start_time >= :lo AND start_time < :hi
            """,
            {"et": event_type_id, "lo": lo, "hi": hi},
        )
        return row["c"], row["mins"]

    async def event_type_title(self, event_type_id: UUID) -> str | None:
        row = await self._sql.fetch_one("SELECT title FROM event_type WHERE id = :id", {"id": event_type_id})
        if row is None:
            return None
        return row["title"]

    @staticmethod
    def _to_booking(row) -> BookingDTO:  # noqa: ANN001
        return BookingDTO(
            id=row["id"],
            event_type_id=row["event_type_id"],
            host_user_id=row["host_user_id"],
            client_user_id=row["client_user_id"],
            start_time=row["start_time"],
            end_time=row["end_time"],
            status=row["status"],
            attendee_time_zone=row["attendee_time_zone"],
            created_at=row["created_at"],
            field_answers=[
                AnsweredFieldDTO(key=x["key"], label=x["label"], field_type=x["type"], value=x["value"])
                for x in (row["field_answers"] or [])
            ],
        )
