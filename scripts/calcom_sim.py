#!/usr/bin/env -S uv run --script
# /// script
# requires-python = ">=3.12"
# dependencies = ["httpx", "asyncpg"]
# ///
"""cal.com webhook simulator: signed BOOKING_CREATED/CANCELLED/RESCHEDULED webhooks + fixture DB rows.

Payloads mirror the real captures in event-booking/requests.jsonl. The cal.com DB
(calcom database in the shared postgres instance, published on localhost:5432) is written
first because event-booking enriches bookings straight from that DB. Defaults come from
the root .env / .env.example.

    uv run scripts/calcom_sim.py create [--starts-in 2h] [--locale ru] [--attendee-email x@y.z]
    uv run scripts/calcom_sim.py cancel <uid> [--reason "..."]
    uv run scripts/calcom_sim.py reschedule <uid> [--starts-in 3d]
    uv run scripts/calcom_sim.py lifecycle [--pause 3]

Note: event-receiver dedupes identical payloads for ~10 min; every run randomizes ids, so
repeated runs always pass. Reminders fire only for bookings starting 55-65 min ahead.
"""

import argparse
import asyncio
import hashlib
import hmac
import json
import random
import re
import string
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import asyncpg
import httpx

ROOT = Path(__file__).resolve().parent.parent
SIGNATURE_HEADER = "X-Cal-Signature-256"
DEFAULT_SECRET = "dev-calcom-webhook-9d2c4f7a1e6b8350"
DEFAULT_DSN = "postgresql://calcom:calcom@localhost:5432/calcom"

# Seeded fixture rows (docker/calcom-init/01-schema.sql) — event-booking enriches by these ids.
ORGANIZER = {
    "id": 4,
    "email": "lelikovas@gmail.com",
    "name": "Александр",
    "username": "chief.bread",
    "timeZone": "Europe/Madrid",
    "locale": "ru",
}
EVENT_TYPE_ID = 2

NAMES = [
    "Мария Соколова",
    "Дмитрий Орлов",
    "Anna Petrova",
    "Игорь Лебедев",
    "Elena Smirnova",
    "Олег Виноградов",
    "Kate Morozova",
    "Сергей Журавлёв",
    "Pavel Sorokin",
    "Наталья Ким",
]
TIMEZONES = ["Europe/Madrid", "Europe/Moscow", "Europe/Berlin", "Asia/Yerevan", "America/New_York"]
TITLES = ["Консультация", "Стратегическая сессия", "Demo call", "Интервью", "Разбор проекта"]
EMAIL_DOMAINS = ["gmail.com", "yandex.ru", "outlook.com", "proton.me"]
UID_ALPHABET = string.ascii_letters + string.digits
UTC_OFFSETS = {
    "Europe/Madrid": 120,
    "Europe/Moscow": 180,
    "Europe/Berlin": 120,
    "Asia/Yerevan": 240,
    "America/New_York": -240,
}


def load_env_defaults() -> dict[str, str]:
    """Plain KEY=VALUE parser over root .env (fallback .env.example)."""
    for name in (".env", ".env.example"):
        path = ROOT / name
        if not path.is_file():
            continue
        values: dict[str, str] = {}
        for line in path.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip('"').strip("'")
        return values
    return {}


def default_url(env: dict[str, str]) -> str:
    """event-receiver cal.com endpoint on the published host port (RECEIVER_PORT)."""
    return f"http://localhost:{env.get('RECEIVER_PORT', '8888')}/event/calcom"


def default_dsn(env: dict[str, str]) -> str:
    """Map the docker-internal CALCOM_DATABASE_URL onto the published host port (PG_PORT)."""
    host_port = env.get("PG_PORT", "5432")
    dsn = env.get("CALCOM_DATABASE_URL", DEFAULT_DSN.replace(":5432/", f":{host_port}/"))
    dsn = dsn.replace("postgresql+asyncpg://", "postgresql://")
    return dsn.replace("@postgres:5432", f"@localhost:{host_port}")


def parse_starts_in(value: str) -> timedelta:
    match = re.fullmatch(r"(\d+)([mhd])", value)
    if not match:
        raise argparse.ArgumentTypeError(f"--starts-in must look like 12m / 2h / 3d, got {value!r}")
    amount = int(match.group(1))
    units = {"m": timedelta(minutes=amount), "h": timedelta(hours=amount), "d": timedelta(days=amount)}
    return units[match.group(2)]


def iso_z(dt: datetime) -> str:
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def naive_utc(dt: datetime) -> datetime:
    """cal.com timestamps are timestamp(3) WITHOUT time zone (naive UTC)."""
    return dt.astimezone(UTC).replace(tzinfo=None)


def new_uid() -> str:
    return "".join(random.choices(UID_ALPHABET, k=22))


def new_booking_id() -> int:
    return random.randint(10_000, 999_999)


def random_attendee(locale: str, email: str | None) -> dict[str, str]:
    name = random.choice(NAMES)
    slug = re.sub(r"[^a-z0-9]+", ".", name.lower()).strip(".") or f"guest.{random.choice(['anna', 'oleg', 'kate'])}"
    address = email or f"{slug}{random.randint(1, 99)}@{random.choice(EMAIL_DOMAINS)}"
    return {"name": name, "email": address, "timeZone": random.choice(TIMEZONES), "locale": locale}


# --------------------------------------------------------------------------
# Payload builders — key sets match the captures in event-booking/requests.jsonl
# --------------------------------------------------------------------------


def _organizer_block() -> dict[str, Any]:
    return {
        "email": ORGANIZER["email"],
        "id": ORGANIZER["id"],
        "language": {"locale": ORGANIZER["locale"]},
        "name": ORGANIZER["name"],
        "timeFormat": "HH:mm",
        "timeZone": ORGANIZER["timeZone"],
        "username": ORGANIZER["username"],
        "utcOffset": UTC_OFFSETS[ORGANIZER["timeZone"]],
    }


def _attendee_block(attendee: dict[str, str]) -> dict[str, Any]:
    return {
        "email": attendee["email"],
        "firstName": "",
        "language": {"locale": attendee["locale"]},
        "lastName": "",
        "name": attendee["name"],
        "timeZone": attendee["timeZone"],
        "utcOffset": UTC_OFFSETS[attendee["timeZone"]],
    }


def _responses_full(attendee: dict[str, str], location: str) -> dict[str, Any]:
    return {
        "email": {"isHidden": False, "label": "email_address", "value": attendee["email"]},
        "guests": {"isHidden": False, "label": "additional_guests", "value": []},
        "location": {"isHidden": False, "label": "location", "value": {"optionValue": "", "value": location}},
        "name": {"isHidden": False, "label": "your_name", "value": attendee["name"]},
        "notes": {"isHidden": False, "label": "additional_notes"},
        "rescheduleReason": {"isHidden": False, "label": "reason_for_reschedule"},
        "title": {"isHidden": True, "label": "what_is_this_meeting_about"},
    }


def _common_event_fields(event_title: str, attendee: dict[str, str]) -> dict[str, Any]:
    return {
        "bookerUrl": "http://localhost:3000",
        "currency": "usd",
        "customInputs": {},
        "eventTitle": event_title,
        "eventTypeId": EVENT_TYPE_ID,
        "length": 30,
        "team": {"id": 1, "members": [], "name": "ddd"},
        "title": f"{event_title} {attendee['name']}",
        "type": event_title,
        "userFieldsResponses": {},
    }


def build_created(
    *,
    uid: str,
    booking_id: int,
    event_title: str,
    attendee: dict[str, str],
    start: datetime,
    end: datetime,
    location: str,
) -> dict[str, Any]:
    payload = _common_event_fields(event_title, attendee)
    payload.update(
        {
            "additionalNotes": "",
            "attendees": [_attendee_block(attendee)],
            "bookingId": booking_id,
            "description": "",
            "destinationCalendar": None,
            "endTime": iso_z(end),
            "eventDescription": "",
            "hideCalendarEventDetails": False,
            "hideCalendarNotes": False,
            "iCalSequence": 0,
            "iCalUID": f"{uid}@Cal.com",
            "location": location,
            "metadata": {"videoCallUrl": location},
            "oneTimePassword": None,
            "organizer": _organizer_block(),
            "price": 0,
            "requiresConfirmation": False,
            "responses": _responses_full(attendee, location),
            "schedulingType": "ROUND_ROBIN",
            "seatsPerTimeSlot": None,
            "seatsShowAttendees": True,
            "seatsShowAvailabilityCount": True,
            "startTime": iso_z(start),
            "status": "ACCEPTED",
            "uid": uid,
        }
    )
    return payload


def build_cancelled(
    *,
    uid: str,
    booking_id: int,
    event_title: str,
    attendee: dict[str, str],
    start: datetime,
    end: datetime,
    location: str,
    reason: str,
) -> dict[str, Any]:
    payload = _common_event_fields(event_title, attendee)
    payload.update(
        {
            "attendees": [
                {
                    "email": attendee["email"],
                    "language": {"locale": attendee["locale"]},
                    "name": attendee["name"],
                    "phoneNumber": None,
                    "timeZone": attendee["timeZone"],
                    "utcOffset": UTC_OFFSETS[attendee["timeZone"]],
                }
            ],
            "bookingId": booking_id,
            "cancellationReason": reason,
            "cancelledBy": ORGANIZER["email"],
            "destinationCalendar": [],
            "endTime": iso_z(end),
            "eventDescription": None,
            "iCalSequence": 1,
            "iCalUID": f"{uid}@Cal.com",
            "location": location,
            "organizer": _organizer_block(),
            "price": None,
            "requiresConfirmation": None,
            "responses": {
                "email": {"label": "email", "value": attendee["email"]},
                "guests": {"label": "guests", "value": []},
                "location": {"label": "location", "value": {"optionValue": "", "value": "link"}},
                "name": {"label": "name", "value": attendee["name"]},
            },
            "seatsPerTimeSlot": None,
            "seatsShowAttendees": False,
            "startTime": iso_z(start),
            "status": "CANCELLED",
            "uid": uid,
        }
    )
    return payload


def build_rescheduled(
    *,
    uid: str,
    booking_id: int,
    event_title: str,
    attendee: dict[str, str],
    start: datetime,
    end: datetime,
    location: str,
    old_uid: str,
    old_booking_id: int,
    old_start: datetime,
    old_end: datetime,
) -> dict[str, Any]:
    payload = _common_event_fields(event_title, attendee)
    payload.update(
        {
            "additionalNotes": "",
            "appsStatus": [],
            "attendees": [_attendee_block(attendee)],
            "bookingId": booking_id,
            "description": "",
            "destinationCalendar": None,
            "endTime": iso_z(end),
            "eventDescription": "",
            "hideCalendarEventDetails": False,
            "hideCalendarNotes": False,
            "iCalSequence": 1,
            "location": location,
            "metadata": {"videoCallUrl": location},
            "oneTimePassword": None,
            "organizer": _organizer_block(),
            "price": 0,
            "requiresConfirmation": False,
            "rescheduleEndTime": iso_z(old_end),
            "rescheduleId": old_booking_id,
            "rescheduleStartTime": iso_z(old_start),
            "rescheduleUid": old_uid,
            "rescheduledBy": ORGANIZER["email"],
            "responses": _responses_full(attendee, location),
            "schedulingType": "ROUND_ROBIN",
            "seatsPerTimeSlot": None,
            "seatsShowAttendees": True,
            "seatsShowAvailabilityCount": True,
            "startTime": iso_z(start),
            "status": "ACCEPTED",
            "uid": uid,
        }
    )
    return payload


# --------------------------------------------------------------------------
# cal.com fixture DB (writes mirror what real cal.com would persist)
# --------------------------------------------------------------------------


async def db_insert_booking(
    conn: asyncpg.Connection,
    *,
    booking_id: int,
    uid: str,
    title: str,
    start: datetime,
    end: datetime,
    attendee: dict[str, str],
    from_reschedule: str | None = None,
) -> None:
    await conn.execute(
        'INSERT INTO "Booking" (id, uid, "userId", "eventTypeId", title, status, "startTime", "endTime",'
        ' "createdAt", metadata, "fromReschedule") VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb, $11)',
        booking_id,
        uid,
        ORGANIZER["id"],
        EVENT_TYPE_ID,
        title,
        "accepted",
        naive_utc(start),
        naive_utc(end),
        naive_utc(datetime.now(UTC)),
        "{}",
        from_reschedule,
    )
    await conn.execute(
        'INSERT INTO "Attendee" ("bookingId", name, email, "timeZone", locale) VALUES ($1, $2, $3, $4, $5)',
        booking_id,
        attendee["name"],
        attendee["email"],
        attendee["timeZone"],
        attendee["locale"],
    )


async def db_cancel_booking(conn: asyncpg.Connection, uid: str) -> None:
    await conn.execute('UPDATE "Booking" SET status = $2 WHERE uid = $1', uid, "cancelled")


async def db_fetch_booking(conn: asyncpg.Connection, uid: str) -> dict[str, Any] | None:
    row = await conn.fetchrow(
        'SELECT b.id, b.title, b."startTime", b."endTime", a.name, a.email, a."timeZone", a.locale'
        ' FROM "Booking" b LEFT JOIN "Attendee" a ON a."bookingId" = b.id WHERE b.uid = $1'
        " ORDER BY a.id ASC NULLS LAST LIMIT 1",
        uid,
    )
    if row is None:
        return None
    attendee = {
        "name": row["name"] or random.choice(NAMES),
        "email": row["email"] or "guest@example.com",
        "timeZone": row["timeZone"] or "Europe/Madrid",
        "locale": row["locale"] or "ru",
    }
    return {
        "booking_id": row["id"],
        "title": row["title"],
        "event_title": row["title"].removesuffix(f" {attendee['name']}"),
        "start": row["startTime"].replace(tzinfo=UTC),
        "end": row["endTime"].replace(tzinfo=UTC),
        "attendee": attendee,
    }


# --------------------------------------------------------------------------
# Send + report
# --------------------------------------------------------------------------


async def send_webhook(args: argparse.Namespace, trigger: str, payload: dict[str, Any]) -> None:
    webhook = {"createdAt": iso_z(datetime.now(UTC)), "payload": payload, "triggerEvent": trigger}
    body = json.dumps(webhook, ensure_ascii=False).encode()
    if args.dry_run:
        print(json.dumps(webhook, ensure_ascii=False, indent=2))
        return
    signature = hmac.new(args.secret.encode(), body, hashlib.sha256).hexdigest()
    async with httpx.AsyncClient(timeout=15) as client:
        response = await client.post(
            args.url, content=body, headers={"Content-Type": "application/json", SIGNATURE_HEADER: signature}
        )
    print(f"{trigger}: HTTP {response.status_code}  uid={payload['uid']}  bookingId={payload['bookingId']}")
    if response.status_code >= 300:
        print(f"  response: {response.text[:300]}")
        sys.exit(1)
    print(
        "  check saver:    docker compose exec pg-saver psql -U postgres -d event_saver -c "
        '"SELECT booking_uid, current_status FROM bookings ORDER BY last_seen_at DESC LIMIT 5;"'
    )
    print(
        "  check notifier: docker compose exec pg-notifier psql -U postgres -d event_notifier -c "
        '"SELECT recipient_email, channel, trigger_event, status FROM notification_outbox '
        'ORDER BY created_at DESC LIMIT 5;"'
    )
    print("  check wiremock: http://localhost:8089/__admin/requests")


async def with_db(args: argparse.Namespace) -> asyncpg.Connection | None:
    if args.dry_run or args.no_db:
        return None
    return await asyncpg.connect(args.calcom_dsn)


# --------------------------------------------------------------------------
# Commands
# --------------------------------------------------------------------------


def _fallback_booking(locale: str, *, hours_ahead: int) -> dict[str, Any]:
    """Synthesized previous-booking data for --dry-run / --no-db / unknown uid."""
    attendee = random_attendee(locale, None)
    event_title = random.choice(TITLES)
    start = datetime.now(UTC) + timedelta(hours=hours_ahead)
    return {
        "booking_id": new_booking_id(),
        "title": f"{event_title} {attendee['name']}",
        "event_title": event_title,
        "start": start,
        "end": start + timedelta(minutes=30),
        "attendee": attendee,
    }


async def cmd_create(args: argparse.Namespace) -> str:
    attendee = random_attendee(args.locale, args.attendee_email)
    uid, booking_id = new_uid(), new_booking_id()
    event_title = random.choice(TITLES)
    start = datetime.now(UTC) + parse_starts_in(args.starts_in)
    end = start + timedelta(minutes=30)
    location = "https://ya.ru"
    conn = await with_db(args)
    if conn is not None:
        await db_insert_booking(
            conn,
            booking_id=booking_id,
            uid=uid,
            title=f"{event_title} {attendee['name']}",
            start=start,
            end=end,
            attendee=attendee,
        )
        await conn.close()
    payload = build_created(
        uid=uid,
        booking_id=booking_id,
        event_title=event_title,
        attendee=attendee,
        start=start,
        end=end,
        location=location,
    )
    await send_webhook(args, "BOOKING_CREATED", payload)
    return uid


async def cmd_cancel(args: argparse.Namespace) -> str:
    booking = _fallback_booking(args.locale, hours_ahead=2)
    conn = await with_db(args)
    if conn is not None:
        booking = await db_fetch_booking(conn, args.uid) or booking
        await db_cancel_booking(conn, args.uid)
        await conn.close()
    payload = build_cancelled(
        uid=args.uid,
        booking_id=booking["booking_id"],
        event_title=booking["event_title"],
        attendee=booking["attendee"],
        start=booking["start"],
        end=booking["end"],
        location="https://ya.ru",
        reason=args.reason,
    )
    await send_webhook(args, "BOOKING_CANCELLED", payload)
    return args.uid


async def cmd_reschedule(args: argparse.Namespace) -> str:
    old = _fallback_booking(args.locale, hours_ahead=1)
    new_uid_value, booking_id = new_uid(), new_booking_id()
    start = datetime.now(UTC) + parse_starts_in(args.starts_in)
    end = start + timedelta(minutes=30)
    conn = await with_db(args)
    if conn is not None:
        old = await db_fetch_booking(conn, args.uid) or old
        await db_insert_booking(
            conn,
            booking_id=booking_id,
            uid=new_uid_value,
            title=old["title"],
            start=start,
            end=end,
            attendee=old["attendee"],
            from_reschedule=args.uid,
        )
        await db_cancel_booking(conn, args.uid)
        await conn.close()
    payload = build_rescheduled(
        uid=new_uid_value,
        booking_id=booking_id,
        event_title=old["event_title"],
        attendee=old["attendee"],
        start=start,
        end=end,
        location="https://ya.ru",
        old_uid=args.uid,
        old_booking_id=old["booking_id"],
        old_start=old["start"],
        old_end=old["end"],
    )
    await send_webhook(args, "BOOKING_RESCHEDULED", payload)
    if not args.dry_run:
        print(f"  new uid={new_uid_value} (rescheduleUid={args.uid})")
    return new_uid_value


async def cmd_lifecycle(args: argparse.Namespace) -> str:
    uid = await cmd_create(args)
    await asyncio.sleep(0 if args.dry_run else args.pause)
    args.uid = uid
    uid = await cmd_reschedule(args)
    await asyncio.sleep(0 if args.dry_run else args.pause)
    args.uid = uid
    return await cmd_cancel(args)


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def build_parser(env: dict[str, str]) -> argparse.ArgumentParser:
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--url", default=default_url(env), help="event-receiver cal.com endpoint")
    common.add_argument(
        "--secret",
        default=env.get("CALCOM_WEBHOOK_SECRET", DEFAULT_SECRET),
        help="webhook HMAC secret (CALCOM_WEBHOOK_SECRET)",
    )
    common.add_argument("--calcom-dsn", default=default_dsn(env), help="cal.com fixture DB DSN")
    common.add_argument("--dry-run", action="store_true", help="print the webhook, no DB writes / no POST")
    common.add_argument("--no-db", action="store_true", help="POST only, skip cal.com DB writes")
    common.add_argument(
        "--starts-in", default="2h", type=str, help="booking start offset: 12m / 2h / 3d (reminder window: 55-65m)"
    )
    common.add_argument("--locale", default="ru", choices=("ru", "en"))
    common.add_argument("--attendee-email", default=None)
    common.add_argument("--reason", default="Планы изменились", help="cancellation reason")
    common.add_argument("--pause", type=float, default=3.0, help="seconds between lifecycle steps")

    parser = argparse.ArgumentParser(description="cal.com webhook simulator (root compose stack)")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("create", parents=[common], help="new booking").set_defaults(handler=cmd_create)
    cancel = sub.add_parser("cancel", parents=[common], help="cancel an existing booking uid")
    cancel.add_argument("uid")
    cancel.set_defaults(handler=cmd_cancel)
    reschedule = sub.add_parser("reschedule", parents=[common], help="reschedule an existing booking uid")
    reschedule.add_argument("uid")
    reschedule.set_defaults(handler=cmd_reschedule)
    sub.add_parser("lifecycle", parents=[common], help="create -> reschedule -> cancel").set_defaults(
        handler=cmd_lifecycle
    )
    return parser


def main() -> None:
    args = build_parser(load_env_defaults()).parse_args()
    parse_starts_in(args.starts_in)  # fail fast on bad format
    asyncio.run(args.handler(args))


if __name__ == "__main__":
    main()
