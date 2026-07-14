from datetime import timedelta

import structlog

from event_scheduling.calendar.dto import ExternalCalendarDTO
from event_scheduling.calendar.interfaces import IICalClient, IICalParser
from event_scheduling.calendar.write_adapter import CalendarWriteAdapter
from event_scheduling.interfaces.busy_times import TimeWindow
from event_scheduling.interfaces.sql import ISqlExecutor
from event_scheduling.slots.interfaces import Clock


logger = structlog.get_logger(__name__)


async def sync_calendar(
    sql: ISqlExecutor,
    client: IICalClient,
    parser: IICalParser,
    clock: Clock,
    calendar: ExternalCalendarDTO,
    window_days: int,
) -> None:
    write = CalendarWriteAdapter(sql)
    now = clock.now()
    try:
        ics_bytes = await client.fetch(calendar.url)
    except Exception as exc:  # noqa: BLE001 - fetch failure keeps the last good cache
        logger.warning("calendar sync fetch failed", calendar_id=str(calendar.id), error=str(exc))
        await write.mark_error(calendar.id, now, "fetch_failed")
        return
    try:
        events = parser.expand(ics_bytes, TimeWindow(now, now + timedelta(days=window_days)))
    except Exception as exc:  # noqa: BLE001 - parse failure keeps the last good cache
        logger.warning("calendar sync parse failed", calendar_id=str(calendar.id), error=str(exc))
        await write.mark_error(calendar.id, now, "parse_failed")
        return
    await write.replace_cache(calendar.id, events)
    await write.mark_synced(calendar.id, now)
