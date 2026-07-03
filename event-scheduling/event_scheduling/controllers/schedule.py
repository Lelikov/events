from uuid import UUID

from event_scheduling.dto.schedule import ActorDTO, ChangeLogEntryDTO, ScheduleBundleDTO, TravelDTO, UpsertScheduleDTO
from event_scheduling.errors import NotFoundError
from event_scheduling.interfaces.schedule import IScheduleDBAdapter
from event_scheduling.validation import validate_date_overrides, validate_time_zone, validate_weekly_hours


def _bundle_to_snapshot(bundle: ScheduleBundleDTO) -> dict:
    return {
        "schedule": {"name": bundle.schedule.name, "time_zone": bundle.schedule.time_zone},
        "weekly_hours": [
            {
                "day_of_week": w.day_of_week,
                "start_time": w.start_time.isoformat(),
                "end_time": w.end_time.isoformat(),
            }
            for w in bundle.weekly_hours
        ],
        "date_overrides": [
            {
                "date": o.date.isoformat(),
                "start_time": o.start_time.isoformat() if o.start_time else None,
                "end_time": o.end_time.isoformat() if o.end_time else None,
            }
            for o in bundle.date_overrides
        ],
        "travel_schedules": [
            {
                "time_zone": t.time_zone,
                "start_date": t.start_date.isoformat(),
                "end_date": t.end_date.isoformat() if t.end_date else None,
                "prev_time_zone": t.prev_time_zone,
            }
            for t in bundle.travel_schedules
        ],
    }


class ScheduleController:
    def __init__(self, db: IScheduleDBAdapter) -> None:
        self._db = db

    async def get_schedule(self, owner_user_id: UUID) -> ScheduleBundleDTO:
        bundle = await self._db.get_bundle(owner_user_id)
        if bundle is None:
            raise NotFoundError(f"schedule for owner {owner_user_id} not found")
        return bundle

    async def upsert_schedule(self, owner_user_id: UUID, dto: UpsertScheduleDTO, actor: ActorDTO) -> ScheduleBundleDTO:
        validate_time_zone(dto.time_zone)
        validate_weekly_hours(dto.weekly_hours)
        validate_date_overrides(dto.date_overrides)
        bundle = await self._db.replace_schedule(owner_user_id, dto)
        await self._db.append_change_log(owner_user_id, bundle.schedule.id, actor, _bundle_to_snapshot(bundle))
        return bundle

    async def list_change_log(self, owner_user_id: UUID, limit: int, offset: int) -> list[ChangeLogEntryDTO]:
        return await self._db.list_change_log(owner_user_id, limit, offset)

    async def replace_travel(self, owner_user_id: UUID, travels: list[TravelDTO], actor: ActorDTO) -> ScheduleBundleDTO:
        existing = await self._db.get_bundle(owner_user_id)
        if existing is None:
            raise NotFoundError(f"schedule for owner {owner_user_id} not found")
        for t in travels:
            validate_time_zone(t.time_zone)
            if t.prev_time_zone is not None:
                validate_time_zone(t.prev_time_zone)
        await self._db.replace_travel(existing.schedule.id, travels)
        bundle = await self._db.get_bundle(owner_user_id)
        if bundle is None:
            msg = f"schedule disappeared after travel replace for owner {owner_user_id}"
            raise RuntimeError(msg)
        await self._db.append_change_log(owner_user_id, bundle.schedule.id, actor, _bundle_to_snapshot(bundle))
        return bundle
