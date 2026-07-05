import json
from uuid import UUID

from event_scheduling.dto.schedule import (
    ActorDTO,
    ChangeLogEntryDTO,
    DateOverrideDTO,
    ScheduleBundleDTO,
    ScheduleDTO,
    TravelDTO,
    UpsertScheduleDTO,
    WeeklyHourDTO,
)
from event_scheduling.interfaces.sql import ISqlExecutor


class ScheduleDBAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def _upsert_schedule_row(self, owner_user_id: UUID, name: str, time_zone: str) -> UUID:
        row = await self._sql.fetch_one(
            """
            INSERT INTO schedule (owner_user_id, name, time_zone)
            VALUES (:owner, :name, :tz)
            ON CONFLICT (owner_user_id)
            DO UPDATE SET name = EXCLUDED.name, time_zone = EXCLUDED.time_zone, updated_at = now()
            RETURNING id
            """,
            {"owner": owner_user_id, "name": name, "tz": time_zone},
        )
        return row["id"]

    async def replace_schedule(self, owner_user_id: UUID, dto: UpsertScheduleDTO) -> ScheduleBundleDTO:
        sid = await self._upsert_schedule_row(owner_user_id, dto.name, dto.time_zone)
        await self._sql.execute("DELETE FROM weekly_hours WHERE schedule_id = :sid", {"sid": sid})
        await self._sql.execute("DELETE FROM date_override WHERE schedule_id = :sid", {"sid": sid})
        for w in dto.weekly_hours:
            await self._sql.execute(
                "INSERT INTO weekly_hours (schedule_id, day_of_week, start_time, end_time) VALUES (:sid, :d, :s, :e)",
                {"sid": sid, "d": w.day_of_week, "s": w.start_time, "e": w.end_time},
            )
        for o in dto.date_overrides:
            await self._sql.execute(
                "INSERT INTO date_override (schedule_id, date, start_time, end_time) VALUES (:sid, :date, :s, :e)",
                {"sid": sid, "date": o.date, "s": o.start_time, "e": o.end_time},
            )
        bundle = await self.get_bundle(owner_user_id)
        if bundle is None:
            msg = f"schedule row missing immediately after upsert for owner {owner_user_id}"
            raise RuntimeError(msg)
        return bundle

    async def get_bundle(self, owner_user_id: UUID) -> ScheduleBundleDTO | None:
        srow = await self._sql.fetch_one(
            "SELECT id, owner_user_id, name, time_zone FROM schedule WHERE owner_user_id = :owner",
            {"owner": owner_user_id},
        )
        if srow is None:
            return None
        sid = srow["id"]
        whs = await self._sql.fetch_all(
            "SELECT day_of_week, start_time, end_time FROM weekly_hours WHERE schedule_id = :sid "
            "ORDER BY day_of_week, start_time",
            {"sid": sid},
        )
        ovs = await self._sql.fetch_all(
            "SELECT date, start_time, end_time FROM date_override WHERE schedule_id = :sid ORDER BY date, start_time",
            {"sid": sid},
        )
        trs = await self._sql.fetch_all(
            "SELECT time_zone, start_date, end_date, prev_time_zone FROM travel_schedule "
            "WHERE schedule_id = :sid ORDER BY start_date",
            {"sid": sid},
        )
        return ScheduleBundleDTO(
            schedule=ScheduleDTO(srow["id"], srow["owner_user_id"], srow["name"], srow["time_zone"]),
            weekly_hours=[WeeklyHourDTO(r["day_of_week"], r["start_time"], r["end_time"]) for r in whs],
            date_overrides=[DateOverrideDTO(r["date"], r["start_time"], r["end_time"]) for r in ovs],
            travel_schedules=[
                TravelDTO(r["time_zone"], r["start_date"], r["end_date"], r["prev_time_zone"]) for r in trs
            ],
        )

    async def append_change_log(self, owner_user_id: UUID, schedule_id: UUID, actor: ActorDTO, snapshot: dict) -> None:
        await self._sql.execute(
            """
            INSERT INTO schedule_change_log (owner_user_id, schedule_id, actor_source, actor_user_id, snapshot)
            VALUES (:owner, :sid, :src, :uid, CAST(:snap AS jsonb))
            """,
            {
                "owner": owner_user_id,
                "sid": schedule_id,
                "src": actor.source,
                "uid": actor.user_id,
                "snap": json.dumps(snapshot),
            },
        )

    async def list_change_log(self, owner_user_id: UUID, limit: int, offset: int) -> list[ChangeLogEntryDTO]:
        rows = await self._sql.fetch_all(
            "SELECT id, at, actor_source, actor_user_id, snapshot FROM schedule_change_log "
            "WHERE owner_user_id = :owner ORDER BY at DESC, id DESC LIMIT :limit OFFSET :offset",
            {"owner": owner_user_id, "limit": limit, "offset": offset},
        )
        return [ChangeLogEntryDTO(r["id"], r["at"], r["actor_source"], r["actor_user_id"], r["snapshot"]) for r in rows]

    async def replace_travel(self, schedule_id: UUID, travels: list[TravelDTO]) -> None:
        await self._sql.execute("DELETE FROM travel_schedule WHERE schedule_id = :sid", {"sid": schedule_id})
        for t in travels:
            await self._sql.execute(
                "INSERT INTO travel_schedule (schedule_id, time_zone, start_date, end_date, prev_time_zone) "
                "VALUES (:sid, :tz, :sd, :ed, :prev)",
                {"sid": schedule_id, "tz": t.time_zone, "sd": t.start_date, "ed": t.end_date, "prev": t.prev_time_zone},
            )
