import asyncio
import contextlib
from datetime import timedelta
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from event_scheduling.adapters.sql import SqlExecutor as _SqlExec
from event_scheduling.publishing.payload import build_cloudevent


if TYPE_CHECKING:
    from sqlalchemy.engine import RowMapping
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from event_scheduling.interfaces.sql import ISqlExecutor
    from event_scheduling.publishing.interfaces import IReceiverClient, IUsersClient
    from event_scheduling.slots.interfaces import Clock

logger = structlog.get_logger(__name__)

_PERMANENT = {400, 401}
_SELECT = (
    "SELECT id, event_ce_id, event_type, booking_uid, payload, attempts "
    "FROM outbox WHERE status = 'pending' AND next_attempt_at <= now() "
    "ORDER BY created_at LIMIT :batch FOR UPDATE SKIP LOCKED"
)


async def dispatch_once(
    sql: ISqlExecutor,
    users: IUsersClient,
    receiver: IReceiverClient,
    clock: Clock,
    max_backoff_s: int,
    batch_size: int,
) -> int:
    rows = await sql.fetch_all(_SELECT, {"batch": batch_size})
    for row in rows:
        await _dispatch_row(sql, row, users, receiver, clock, max_backoff_s)
    return len(rows)


async def _mark_sent(sql: ISqlExecutor, row_id: UUID) -> None:
    await sql.execute("UPDATE outbox SET status='sent', sent_at=now() WHERE id=:id", {"id": row_id})


async def _mark_failed(sql: ISqlExecutor, row_id: UUID, err: str) -> None:
    await sql.execute("UPDATE outbox SET status='failed', last_error=:e WHERE id=:id", {"id": row_id, "e": err})


async def _mark_retry(
    sql: ISqlExecutor, row_id: UUID, attempts: int, clock: Clock, max_backoff_s: int, err: str
) -> None:
    delay = min(max_backoff_s, 5 * (2**attempts))
    nxt = clock.now() + timedelta(seconds=delay)
    await sql.execute(
        "UPDATE outbox SET attempts=attempts+1, next_attempt_at=:n, last_error=:e WHERE id=:id",
        {"id": row_id, "n": nxt, "e": err},
    )


async def _resolve_participants(
    sql: ISqlExecutor, row: RowMapping, users: IUsersClient, clock: Clock, max_backoff_s: int
) -> tuple | None:
    """Resolve host/client (and, if present, previous host) emails via event-users.

    Returns (host, client, previous_host_or_None) or None on failure. The previous
    host is best-effort — a booking.reassigned payload carries previous_host_user_id
    for the previous_organizer participant, but a missing one does not block the emit.
    """
    payload = row["payload"]
    try:
        host_id = UUID(payload["host_user_id"])
        client_id = UUID(payload["client_user_id"])
    except (KeyError, ValueError) as exc:
        await _mark_failed(sql, row["id"], f"malformed-payload:{exc}")
        return None
    ids = [host_id, client_id]
    prev_id: UUID | None = None
    prev_raw = payload.get("previous_host_user_id")
    if prev_raw is not None:
        try:
            prev_id = UUID(prev_raw)
            ids.append(prev_id)
        except ValueError:
            prev_id = None
    try:
        resolved = await users.by_ids(ids)
    except Exception as exc:  # noqa: BLE001 - transient users-service failure, retry
        await _mark_retry(sql, row["id"], row["attempts"], clock, max_backoff_s, f"users:{exc}")
        return None
    host = resolved.get(host_id)
    client = resolved.get(client_id)
    if host is None or client is None:
        await _mark_retry(sql, row["id"], row["attempts"], clock, max_backoff_s, "email-not-found")
        return None
    previous = resolved.get(prev_id) if prev_id is not None else None
    return host, client, previous


async def _dispatch_row(
    sql: ISqlExecutor,
    row: RowMapping,
    users: IUsersClient,
    receiver: IReceiverClient,
    clock: Clock,
    max_backoff_s: int,
) -> None:
    participants = await _resolve_participants(sql, row, users, clock, max_backoff_s)
    if participants is None:
        return
    host, client, previous_host = participants
    try:
        headers, body = build_cloudevent(
            row["event_type"],
            row["booking_uid"],
            str(row["event_ce_id"]),
            row["payload"],
            host,
            client,
            clock.now(),
            previous_host=previous_host,
        )
    except (KeyError, ValueError) as exc:
        await _mark_failed(sql, row["id"], f"malformed-payload:{exc}")
        return
    try:
        status = await receiver.publish(headers, body)
    except Exception as exc:  # noqa: BLE001 - transient transport failure, retry
        await _mark_retry(sql, row["id"], row["attempts"], clock, max_backoff_s, f"transport:{exc}")
        return
    if status == 202:
        await _mark_sent(sql, row["id"])
        return
    if status in _PERMANENT:
        await _mark_failed(sql, row["id"], f"http:{status}")
        return
    await _mark_retry(sql, row["id"], row["attempts"], clock, max_backoff_s, f"http:{status}")


async def run_dispatcher_loop(
    sessionmaker: async_sessionmaker,
    users: IUsersClient,
    receiver: IReceiverClient,
    clock: Clock,
    interval_s: float,
    max_backoff_s: int,
    batch_size: int,
    stop: asyncio.Event,
) -> None:
    """Background poller: opens its own session per tick, commits after each batch.

    Survives a failing tick (logs and continues) and sleeps interruptibly on ``stop``
    so shutdown doesn't block for a full ``interval_s``.
    """
    while not stop.is_set():
        try:
            async with sessionmaker() as session:
                await dispatch_once(_SqlExec(session), users, receiver, clock, max_backoff_s, batch_size)
                await session.commit()
        except Exception:
            logger.exception("dispatcher tick failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
