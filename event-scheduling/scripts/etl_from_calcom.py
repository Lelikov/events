"""ETL orchestration: migrate cal.com schedules into the event_scheduling DB.

event_type ETL (EventType / Host / booking limits) is deferred — this module
migrates schedules only. The EtlReport dataclass reserves the ``event_type``
counter for a future branch; it will remain 0 until that branch is added.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from scripts.etl_mapping import expand_weekly, resolve_time_zone


@dataclass
class EtlReport:
    migrated: dict[str, int] = field(default_factory=lambda: {"schedule": 0, "event_type": 0})
    skipped: dict[str, int] = field(default_factory=lambda: {"schedule": 0, "event_type": 0})
    skips: list[tuple[str, str]] = field(default_factory=list)


async def run_etl(
    calcom_dsn: str,
    target_dsn: str,
    resolve_email_to_uuid: Callable[[str], UUID | None],
) -> EtlReport:
    """Migrate cal.com schedules to the target DB.

    Only the organizer's ``defaultScheduleId`` schedule is migrated per user;
    all other schedules are recorded as skipped. Emails without a matching
    UUID are likewise skipped with a log entry. Each bad row is appended to
    ``report.skips`` so the overall run is row-resilient and never aborts.
    """
    report = EtlReport()
    src = create_async_engine(calcom_dsn)
    dst = create_async_engine(target_dsn)
    try:
        async with src.connect() as sconn:
            users = {
                r[0]: {"email": r[1], "tz": r[2], "default": r[3]}
                for r in (
                    await sconn.execute(text('SELECT id, email, "timeZone", "defaultScheduleId" FROM users'))
                ).all()
            }
            schedules = (await sconn.execute(text('SELECT id, "userId", "timeZone" FROM "Schedule"'))).all()
            for sid, uid, sched_tz in schedules:
                user = users.get(uid)
                if user is None or user["default"] != sid:
                    report.skipped["schedule"] += 1
                    report.skips.append(("schedule", "non-default-or-missing-user"))
                    continue
                target_uuid = resolve_email_to_uuid(user["email"])
                if target_uuid is None:
                    report.skipped["schedule"] += 1
                    report.skips.append(("schedule", f"email-not-found:{user['email']}"))
                    continue
                avails = (
                    await sconn.execute(
                        text('SELECT days, "startTime", "endTime", date FROM "Availability" WHERE "scheduleId" = :sid'),
                        {"sid": sid},
                    )
                ).all()
                tz = resolve_time_zone(sched_tz, user["tz"])
                try:
                    await _write_schedule(dst, target_uuid, tz, avails)
                    report.migrated["schedule"] += 1
                except Exception as exc:  # noqa: BLE001
                    report.skipped["schedule"] += 1
                    report.skips.append(("schedule", f"write-error:{exc}"))
        return report
    finally:
        await src.dispose()
        await dst.dispose()


async def _write_schedule(dst: AsyncEngine, owner_uuid: UUID, tz: str, avails: list) -> None:
    """Upsert one schedule (sub-rows: weekly_hours, date_override) and record a baseline snapshot."""
    async with dst.begin() as conn:
        new_sid = (
            await conn.execute(
                text(
                    """
                    INSERT INTO schedule (owner_user_id, name, time_zone)
                    VALUES (:o, 'Imported', :tz)
                    ON CONFLICT (owner_user_id) DO UPDATE
                        SET time_zone = EXCLUDED.time_zone, updated_at = now()
                    RETURNING id
                    """
                ),
                {"o": owner_uuid, "tz": tz},
            )
        ).scalar()
        if new_sid is None:
            msg = f"INSERT INTO schedule returned no id for owner {owner_uuid}"
            raise RuntimeError(msg)
        await conn.execute(text("DELETE FROM weekly_hours WHERE schedule_id = :s"), {"s": new_sid})
        await conn.execute(text("DELETE FROM date_override WHERE schedule_id = :s"), {"s": new_sid})
        weekly_snap: list[dict] = []
        override_snap: list[dict] = []
        for days, start, end, date in avails:
            if date is not None:
                await conn.execute(
                    text(
                        "INSERT INTO date_override (schedule_id, date, start_time, end_time) VALUES (:s, :d, :st, :e)"
                    ),
                    {"s": new_sid, "d": date, "st": start, "e": end},
                )
                override_snap.append(
                    {
                        "date": date.isoformat(),
                        "start_time": start.isoformat() if start is not None else None,
                        "end_time": end.isoformat() if end is not None else None,
                    }
                )
                continue
            for wh in expand_weekly(list(days or []), start, end):
                await conn.execute(
                    text(
                        "INSERT INTO weekly_hours (schedule_id, day_of_week, start_time, end_time)"
                        " VALUES (:s, :d, :st, :e)"
                    ),
                    {"s": new_sid, "d": wh.day_of_week, "st": wh.start_time, "e": wh.end_time},
                )
                weekly_snap.append(
                    {
                        "day_of_week": wh.day_of_week,
                        "start_time": wh.start_time.isoformat(),
                        "end_time": wh.end_time.isoformat(),
                    }
                )
        snapshot = {
            "schedule": {"name": "Imported", "time_zone": tz},
            "weekly_hours": weekly_snap,
            "date_overrides": override_snap,
            "travel_schedules": [],
        }
        await conn.execute(
            text(
                """
                INSERT INTO schedule_change_log (owner_user_id, schedule_id, actor_source, snapshot)
                VALUES (:o, :s, 'etl', CAST(:snap AS jsonb))
                """
            ),
            {"o": owner_uuid, "s": new_sid, "snap": json.dumps(snapshot)},
        )
