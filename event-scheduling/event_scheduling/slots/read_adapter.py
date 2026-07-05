from uuid import UUID

from event_scheduling.dto.schedule import DateOverrideDTO, TravelDTO, WeeklyHourDTO
from event_scheduling.interfaces.sql import ISqlExecutor
from event_scheduling.slots.dto import EventTypeConfig, HostSchedule, SlotBundle


class SlotsReadAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def load(self, event_type_id: UUID) -> SlotBundle | None:
        et = await self._sql.fetch_one(
            """
            SELECT duration_minutes, slot_interval_minutes, min_booking_notice_minutes,
                   buffer_before_minutes, buffer_after_minutes
            FROM event_type WHERE id = :id
            """,
            {"id": event_type_id},
        )
        if et is None:
            return None
        config = EventTypeConfig(
            duration_minutes=et["duration_minutes"],
            slot_interval_minutes=et["slot_interval_minutes"],
            min_booking_notice_minutes=et["min_booking_notice_minutes"],
            buffer_before_minutes=et["buffer_before_minutes"],
            buffer_after_minutes=et["buffer_after_minutes"],
        )
        host_rows = await self._sql.fetch_all(
            "SELECT user_id, schedule_id FROM host WHERE event_type_id = :id",
            {"id": event_type_id},
        )
        if not host_rows:
            return SlotBundle(event_type=config, hosts=[])
        schedule_ids = [r["schedule_id"] for r in host_rows]
        schedules = {
            r["id"]: r["time_zone"]
            for r in await self._sql.fetch_all(
                "SELECT id, time_zone FROM schedule WHERE id = ANY(:ids)", {"ids": schedule_ids}
            )
        }
        weekly = self._group(
            await self._sql.fetch_all(
                "SELECT schedule_id, day_of_week, start_time, end_time "
                "FROM weekly_hours WHERE schedule_id = ANY(:ids)",
                {"ids": schedule_ids},
            ),
            lambda r: WeeklyHourDTO(r["day_of_week"], r["start_time"], r["end_time"]),
        )
        overrides = self._group(
            await self._sql.fetch_all(
                "SELECT schedule_id, date, start_time, end_time "
                "FROM date_override WHERE schedule_id = ANY(:ids)",
                {"ids": schedule_ids},
            ),
            lambda r: DateOverrideDTO(r["date"], r["start_time"], r["end_time"]),
        )
        travels = self._group(
            await self._sql.fetch_all(
                "SELECT schedule_id, time_zone, start_date, end_date, prev_time_zone "
                "FROM travel_schedule WHERE schedule_id = ANY(:ids)",
                {"ids": schedule_ids},
            ),
            lambda r: TravelDTO(r["time_zone"], r["start_date"], r["end_date"], r["prev_time_zone"]),
        )
        hosts = [
            HostSchedule(
                user_id=r["user_id"],
                time_zone=schedules[r["schedule_id"]],
                weekly_hours=weekly.get(r["schedule_id"], []),
                date_overrides=overrides.get(r["schedule_id"], []),
                travels=travels.get(r["schedule_id"], []),
            )
            for r in host_rows
        ]
        return SlotBundle(event_type=config, hosts=hosts)

    @staticmethod
    def _group(rows, make):  # noqa: ANN001, ANN205
        grouped: dict = {}
        for r in rows:
            grouped.setdefault(r["schedule_id"], []).append(make(r))
        return grouped
