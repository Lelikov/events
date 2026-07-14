import asyncio
import contextlib
from typing import TYPE_CHECKING

import structlog

from event_scheduling.adapters.sql import SqlExecutor as _SqlExec
from event_scheduling.calendar.read_adapter import CalendarReadAdapter
from event_scheduling.calendar.sync_service import sync_calendar


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from event_scheduling.calendar.interfaces import IICalClient, IICalParser
    from event_scheduling.slots.interfaces import Clock

logger = structlog.get_logger(__name__)


async def run_calendar_sync_loop(
    sessionmaker: async_sessionmaker,
    client: IICalClient,
    parser: IICalParser,
    clock: Clock,
    *,
    interval_s: float,
    window_days: int,
    stop: asyncio.Event,
) -> None:
    """Background poller: own session per tick, sync each enabled calendar in isolation, commit."""
    while not stop.is_set():
        try:
            async with sessionmaker() as session:
                sql = _SqlExec(session)
                calendars = await CalendarReadAdapter(sql).list_enabled()
                for calendar in calendars:
                    await sync_calendar(sql, client, parser, clock, calendar, window_days)
                await session.commit()
        except Exception:
            logger.exception("calendar sync tick failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
