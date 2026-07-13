import uuid
from datetime import datetime

from event_scheduling.publishing.dto import ParticipantInfo
from event_scheduling.reminders.dto import DueBookingDTO


# Fixed namespace so ce-ids are reproducible across restarts (idempotent redelivery).
_REMINDER_NS = uuid.UUID("a3f1c2d4-5e6b-4a7c-8d9e-0f1a2b3c4d5e")


def _ce_id(key: str) -> str:
    return str(uuid.uuid5(_REMINDER_NS, key))


def _headers(ce_type: str, ce_id: str, now: datetime) -> dict[str, str]:
    return {
        "ce-specversion": "1.0",
        "ce-id": ce_id,
        "ce-source": "booking",
        "ce-type": ce_type,
        "ce-time": now.isoformat(),
    }


def _recipient(email: str, role: str, locale: str | None) -> dict[str, str]:
    recipient = {"email": email, "role": role}
    if locale:
        recipient["locale"] = locale
    return recipient


def build_reminder_command(
    due: DueBookingDTO, host: ParticipantInfo, client: ParticipantInfo, now: datetime
) -> tuple[dict[str, str], dict]:
    uid = str(due.id)
    body = {
        "booking_uid": uid,
        "booking_id": uid,
        "trigger_event": "BOOKING_REMINDER",
        "recipients": [
            _recipient(host.email, "organizer", host.locale),
            _recipient(client.email, "client", client.locale),
        ],
        "template_data": {
            "booking_uid": uid,
            "start_time": due.start_time.isoformat(),
            "end_time": due.end_time.isoformat(),
            "title": due.title,
            "organizer_name": host.name,
            "organizer_email": host.email,
            "client_name": client.name,
            "client_email": client.email,
        },
    }
    return _headers("notification.send_requested", _ce_id(f"reminder:{uid}"), now), body


def build_reminder_sent(due: DueBookingDTO, client: ParticipantInfo, now: datetime) -> tuple[dict[str, str], dict]:
    uid = str(due.id)
    body = {"booking_uid": uid, "email": client.email}
    return _headers("booking.reminder_sent", _ce_id(f"reminder_sent:{uid}"), now), body
