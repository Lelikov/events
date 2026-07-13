import asyncio
import contextlib
from typing import TYPE_CHECKING

import structlog

from event_scheduling.adapters.sql import SqlExecutor as _SqlExec
from event_scheduling.reminders.dto import DueBookingDTO
from event_scheduling.reminders.payload import build_reminder_command, build_reminder_sent
from event_scheduling.reminders.read_adapter import ReminderReadAdapter
from event_scheduling.reminders.write_adapter import ReminderWriteAdapter


if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from event_scheduling.publishing.interfaces import IReceiverClient, IUsersClient
    from event_scheduling.reminders.interfaces import IReminderReadAdapter, IReminderWriteAdapter
    from event_scheduling.slots.interfaces import Clock

logger = structlog.get_logger(__name__)


async def _remind_one(
    due: DueBookingDTO,
    write: IReminderWriteAdapter,
    users: IUsersClient,
    receiver: IReceiverClient,
    clock: Clock,
) -> bool:
    resolved = await users.by_ids([due.host_user_id, due.client_user_id])
    host = resolved.get(due.host_user_id)
    client = resolved.get(due.client_user_id)
    if host is None or client is None:
        logger.warning("reminder skipped: participant unresolved", booking_uid=str(due.id))
        return False
    now = clock.now()
    cmd_headers, cmd_body = build_reminder_command(due, host, client, now)
    await receiver.publish(cmd_headers, cmd_body)
    sent_headers, sent_body = build_reminder_sent(due, client, now)
    await receiver.publish(sent_headers, sent_body)
    await write.mark_sent(due.id, now)
    return True


async def remind_once(
    read: IReminderReadAdapter,
    write: IReminderWriteAdapter,
    users: IUsersClient,
    receiver: IReceiverClient,
    clock: Clock,
    *,
    shift_from_minutes: int,
    shift_to_minutes: int,
    batch_size: int,
) -> int:
    due = await read.due_bookings(
        now=clock.now(), shift_from_minutes=shift_from_minutes, shift_to_minutes=shift_to_minutes, limit=batch_size
    )
    count = 0
    for booking in due:
        try:
            sent = await _remind_one(booking, write, users, receiver, clock)
        except Exception:
            logger.exception("reminder failed", booking_uid=str(booking.id))
            continue
        if sent:
            count += 1
    if count:
        logger.info("reminders sent", count=count)
    return count


async def run_reminder_loop(
    sessionmaker: async_sessionmaker,
    users: IUsersClient,
    receiver: IReceiverClient,
    clock: Clock,
    *,
    interval_s: float,
    shift_from_minutes: int,
    shift_to_minutes: int,
    batch_size: int,
    stop: asyncio.Event,
) -> None:
    """Background poller: own session per tick, commit after each batch, survive a failing tick."""
    while not stop.is_set():
        try:
            async with sessionmaker() as session:
                sql = _SqlExec(session)
                await remind_once(
                    ReminderReadAdapter(sql),
                    ReminderWriteAdapter(sql),
                    users,
                    receiver,
                    clock,
                    shift_from_minutes=shift_from_minutes,
                    shift_to_minutes=shift_to_minutes,
                    batch_size=batch_size,
                )
                await session.commit()
        except Exception:
            logger.exception("reminder tick failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
