from datetime import datetime

from event_scheduling.publishing.dto import ParticipantInfo


def _users(
    host: ParticipantInfo,
    client: ParticipantInfo,
    attendee_tz: str | None,
    previous_host: ParticipantInfo | None = None,
) -> list[dict]:
    users = [
        {"email": host.email, "role": "organizer", "time_zone": host.time_zone},
        {"email": client.email, "role": "client", "time_zone": attendee_tz},
    ]
    if previous_host is not None:
        users.append(
            {"email": previous_host.email, "role": "previous_organizer", "time_zone": previous_host.time_zone}
        )
    return users


def _created_body(booking_uid: str, payload: dict, users: list[dict]) -> dict:
    return {
        "users": users,
        "start_time": payload["start_time"],
        "end_time": payload["end_time"],
        "volunteer_id": payload["host_user_id"],
        "client_id": payload["client_user_id"],
        "booking_uid": booking_uid,
        "field_answers": payload.get("field_answers", []),
    }


def _rescheduled_body(booking_uid: str, payload: dict, users: list[dict]) -> dict:
    return {
        "users": users,
        "start_time": payload["start_time"],
        "end_time": payload["end_time"],
        "previous_start_time": payload.get("previous_start_time"),
        "booking_uid": booking_uid,
    }


def _cancelled_body(booking_uid: str, payload: dict, users: list[dict]) -> dict:
    body = {"users": users, "booking_uid": booking_uid}
    reason = payload.get("cancellation_reason")
    if reason is not None:
        body["cancellation_reason"] = reason
    return body


def _reassigned_body(booking_uid: str, payload: dict, users: list[dict]) -> dict:  # noqa: ARG001 - payload unused
    previous = next((u["email"] for u in users if u["role"] == "previous_organizer"), None)
    return {"users": users, "booking_uid": booking_uid, "previous_organizer_email": previous}


_BUILDERS = {
    "booking.created": _created_body,
    "booking.rescheduled": _rescheduled_body,
    "booking.reassigned": _reassigned_body,
    "booking.cancelled": _cancelled_body,
}


def build_cloudevent(
    event_type: str,
    booking_uid: str,
    ce_id: str,
    payload: dict,
    host: ParticipantInfo,
    client: ParticipantInfo,
    now: datetime,
    previous_host: ParticipantInfo | None = None,
) -> tuple[dict[str, str], dict]:
    builder = _BUILDERS.get(event_type)
    if builder is None:
        msg = f"unknown event_type: {event_type!r}"
        raise ValueError(msg)
    users = _users(host, client, payload.get("attendee_time_zone"), previous_host)
    body = builder(booking_uid, payload, users)
    headers = {
        "ce-specversion": "1.0",
        "ce-id": ce_id,
        "ce-source": "booking",
        "ce-type": event_type,
        "ce-time": now.isoformat(),
    }
    return headers, body
