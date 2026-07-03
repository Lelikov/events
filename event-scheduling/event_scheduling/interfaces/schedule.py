from typing import Protocol
from uuid import UUID

from event_scheduling.dto.schedule import ActorDTO, ScheduleBundleDTO, TravelDTO, UpsertScheduleDTO


class IScheduleDBAdapter(Protocol):
    async def get_bundle(self, owner_user_id: UUID) -> ScheduleBundleDTO | None: ...

    async def replace_schedule(self, owner_user_id: UUID, dto: UpsertScheduleDTO) -> ScheduleBundleDTO: ...

    async def append_change_log(
        self, owner_user_id: UUID, schedule_id: UUID, actor: ActorDTO, snapshot: dict
    ) -> None: ...

    async def replace_travel(self, schedule_id: UUID, travels: list[TravelDTO]) -> None: ...


class IScheduleController(Protocol):
    async def get_schedule(self, owner_user_id: UUID) -> ScheduleBundleDTO: ...

    async def upsert_schedule(
        self, owner_user_id: UUID, dto: UpsertScheduleDTO, actor: ActorDTO
    ) -> ScheduleBundleDTO: ...

    async def replace_travel(
        self, owner_user_id: UUID, travels: list[TravelDTO], actor: ActorDTO
    ) -> ScheduleBundleDTO: ...
