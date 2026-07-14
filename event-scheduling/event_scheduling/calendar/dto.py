from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class ExternalCalendarDTO:
    id: UUID
    host_user_id: UUID
    kind: str
    url: str
    enabled: bool
    last_synced_at: datetime | None
    last_error: str | None
