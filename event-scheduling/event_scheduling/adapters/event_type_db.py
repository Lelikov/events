from typing import TYPE_CHECKING
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from event_scheduling.booking_fields.dto import BookingFieldDTO, OptionDTO
from event_scheduling.dto.event_type import BookingLimitDTO, EventTypeDTO, HostDTO, UpsertEventTypeDTO


if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping
from event_scheduling.errors import ConflictError
from event_scheduling.interfaces.sql import ISqlExecutor


def _is_slug_conflict(error: IntegrityError) -> bool:
    return "uq_event_type_slug" in str(error.orig)


class EventTypeDBAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def _fetch_hosts(self, event_type_id: UUID) -> list[HostDTO]:
        rows = await self._sql.fetch_all(
            "SELECT user_id, schedule_id FROM host WHERE event_type_id = :et ORDER BY user_id",
            {"et": event_type_id},
        )
        return [HostDTO(user_id=r["user_id"], schedule_id=r["schedule_id"]) for r in rows]

    async def _fetch_limits(self, event_type_id: UUID) -> list[BookingLimitDTO]:
        rows = await self._sql.fetch_all(
            "SELECT limit_type, period, value FROM booking_limit WHERE event_type_id = :et ORDER BY limit_type, period",
            {"et": event_type_id},
        )
        return [BookingLimitDTO(limit_type=r["limit_type"], period=r["period"], value=r["value"]) for r in rows]

    async def _fetch_booking_fields(self, event_type_id: UUID) -> list[BookingFieldDTO]:
        rows = await self._sql.fetch_all(
            "SELECT field_key, field_type, label, placeholder, required, options, position "
            "FROM booking_field WHERE event_type_id = :et ORDER BY position",
            {"et": event_type_id},
        )
        result = []
        for r in rows:
            raw = r["options"]
            opts = [OptionDTO(value=o["value"], label=o["label"]) for o in (raw or [])]
            result.append(
                BookingFieldDTO(
                    field_key=r["field_key"],
                    field_type=r["field_type"],
                    label=r["label"],
                    placeholder=r["placeholder"],
                    required=r["required"],
                    options=opts,
                    position=r["position"],
                )
            )
        return result

    def _build_dto(
        self,
        row: RowMapping,
        hosts: list[HostDTO],
        limits: list[BookingLimitDTO],
        booking_fields: list[BookingFieldDTO],
    ) -> EventTypeDTO:
        return EventTypeDTO(
            id=row["id"],
            slug=row["slug"],
            title=row["title"],
            scheduling_type=row["scheduling_type"],
            duration_minutes=row["duration_minutes"],
            slot_interval_minutes=row["slot_interval_minutes"],
            min_booking_notice_minutes=row["min_booking_notice_minutes"],
            buffer_before_minutes=row["buffer_before_minutes"],
            buffer_after_minutes=row["buffer_after_minutes"],
            hosts=hosts,
            booking_limits=limits,
            booking_fields=booking_fields,
        )

    async def insert(self, dto: UpsertEventTypeDTO) -> EventTypeDTO:
        try:
            row = await self._sql.fetch_one(
                """
                INSERT INTO event_type (slug, title, scheduling_type, duration_minutes, slot_interval_minutes,
                                        min_booking_notice_minutes, buffer_before_minutes, buffer_after_minutes)
                VALUES (:slug, :title, :st, :dur, :si, :notice, :bb, :ba)
                RETURNING id
                """,
                {
                    "slug": dto.slug,
                    "title": dto.title,
                    "st": dto.scheduling_type,
                    "dur": dto.duration_minutes,
                    "si": dto.slot_interval_minutes,
                    "notice": dto.min_booking_notice_minutes,
                    "bb": dto.buffer_before_minutes,
                    "ba": dto.buffer_after_minutes,
                },
            )
        except IntegrityError as e:
            if _is_slug_conflict(e):
                raise ConflictError(f"event_type with slug {dto.slug!r} already exists") from e
            raise
        et_id: UUID = row["id"]
        for h in dto.hosts:
            await self._sql.execute(
                "INSERT INTO host (event_type_id, user_id, schedule_id) VALUES (:et, :uid, :sid)",
                {"et": et_id, "uid": h.user_id, "sid": h.schedule_id},
            )
        for bl in dto.booking_limits:
            await self._sql.execute(
                "INSERT INTO booking_limit (event_type_id, limit_type, period, value) VALUES (:et, :lt, :p, :v)",
                {"et": et_id, "lt": bl.limit_type, "p": bl.period, "v": bl.value},
            )
        result = await self.get(et_id)
        if result is None:
            msg = f"event_type row missing immediately after insert id={et_id}"
            raise RuntimeError(msg)
        return result

    async def get(self, event_type_id: UUID) -> EventTypeDTO | None:
        row = await self._sql.fetch_one(
            """
            SELECT id, slug, title, scheduling_type, duration_minutes, slot_interval_minutes,
                   min_booking_notice_minutes, buffer_before_minutes, buffer_after_minutes
            FROM event_type
            WHERE id = :id
            """,
            {"id": event_type_id},
        )
        if row is None:
            return None
        hosts = await self._fetch_hosts(event_type_id)
        limits = await self._fetch_limits(event_type_id)
        booking_fields = await self._fetch_booking_fields(event_type_id)
        return self._build_dto(row, hosts, limits, booking_fields)

    async def list_all(self) -> list[EventTypeDTO]:
        rows = await self._sql.fetch_all(
            """
            SELECT id, slug, title, scheduling_type, duration_minutes, slot_interval_minutes,
                   min_booking_notice_minutes, buffer_before_minutes, buffer_after_minutes
            FROM event_type
            ORDER BY slug
            """,
            {},
        )
        result = []
        for row in rows:
            et_id: UUID = row["id"]
            hosts = await self._fetch_hosts(et_id)
            limits = await self._fetch_limits(et_id)
            result.append(self._build_dto(row, hosts, limits, []))
        return result

    async def update(self, event_type_id: UUID, dto: UpsertEventTypeDTO) -> EventTypeDTO | None:
        try:
            row = await self._sql.fetch_one(
                """
                UPDATE event_type
                SET slug = :slug, title = :title, scheduling_type = :st,
                    duration_minutes = :dur, slot_interval_minutes = :si,
                    min_booking_notice_minutes = :notice,
                    buffer_before_minutes = :bb, buffer_after_minutes = :ba,
                    updated_at = now()
                WHERE id = :id
                RETURNING id
                """,
                {
                    "id": event_type_id,
                    "slug": dto.slug,
                    "title": dto.title,
                    "st": dto.scheduling_type,
                    "dur": dto.duration_minutes,
                    "si": dto.slot_interval_minutes,
                    "notice": dto.min_booking_notice_minutes,
                    "bb": dto.buffer_before_minutes,
                    "ba": dto.buffer_after_minutes,
                },
            )
        except IntegrityError as e:
            if _is_slug_conflict(e):
                raise ConflictError(f"event_type with slug {dto.slug!r} already exists") from e
            raise
        if row is None:
            return None
        await self._sql.execute("DELETE FROM host WHERE event_type_id = :id", {"id": event_type_id})
        await self._sql.execute("DELETE FROM booking_limit WHERE event_type_id = :id", {"id": event_type_id})
        for h in dto.hosts:
            await self._sql.execute(
                "INSERT INTO host (event_type_id, user_id, schedule_id) VALUES (:et, :uid, :sid)",
                {"et": event_type_id, "uid": h.user_id, "sid": h.schedule_id},
            )
        for bl in dto.booking_limits:
            await self._sql.execute(
                "INSERT INTO booking_limit (event_type_id, limit_type, period, value) VALUES (:et, :lt, :p, :v)",
                {"et": event_type_id, "lt": bl.limit_type, "p": bl.period, "v": bl.value},
            )
        return await self.get(event_type_id)

    async def delete(self, event_type_id: UUID) -> bool:
        row = await self._sql.fetch_one(
            "DELETE FROM event_type WHERE id = :id RETURNING id",
            {"id": event_type_id},
        )
        return row is not None

    async def get_schedule_owner(self, schedule_id: UUID) -> UUID | None:
        row = await self._sql.fetch_one(
            "SELECT owner_user_id FROM schedule WHERE id = :sid",
            {"sid": schedule_id},
        )
        if row is None:
            return None
        return row["owner_user_id"]
