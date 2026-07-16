from uuid import UUID

from event_organizer.adapters.interfaces import IUsersClient


class ProfileService:
    def __init__(self, users: IUsersClient) -> None:
        self._users = users

    async def get(self, user_id: UUID) -> dict:
        u = await self._users.get_user(user_id)
        return {"name": u.get("name"), "email": u["email"], "time_zone": u.get("time_zone")}

    async def update(self, user_id: UUID, name: str, time_zone: str) -> dict:
        u = await self._users.patch_user(user_id, {"name": name, "time_zone": time_zone})
        return {"name": u.get("name"), "email": u["email"], "time_zone": u.get("time_zone")}
