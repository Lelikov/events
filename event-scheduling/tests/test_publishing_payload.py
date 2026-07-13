import datetime as dt

from event_scheduling.publishing.dto import ParticipantInfo
from event_scheduling.publishing.payload import build_cloudevent


HOST = ParticipantInfo("org@x.io", "Europe/Berlin")
CLIENT = ParticipantInfo("cli@x.io", None)
NOW = dt.datetime(2026, 7, 13, 12, 0, tzinfo=dt.UTC)


def _payload(**kw) -> dict:
    base = {
        "host_user_id": "11111111-1111-1111-1111-111111111111",
        "client_user_id": "22222222-2222-2222-2222-222222222222",
        "start_time": "2026-10-01T07:00:00Z",
        "end_time": "2026-10-01T08:00:00Z",
        "attendee_time_zone": "Europe/Moscow",
    }
    base.update(kw)
    return base


def test_created_body_and_headers() -> None:
    headers, body = build_cloudevent("booking.created", "bk-1", "ce-1", _payload(), HOST, CLIENT, NOW)
    assert headers == {
        "ce-specversion": "1.0",
        "ce-id": "ce-1",
        "ce-source": "booking",
        "ce-type": "booking.created",
        "ce-time": "2026-07-13T12:00:00+00:00",
    }
    assert body["booking_uid"] == "bk-1"
    assert body["start_time"] == "2026-10-01T07:00:00Z"
    assert body["volunteer_id"] == "11111111-1111-1111-1111-111111111111"
    assert body["client_id"] == "22222222-2222-2222-2222-222222222222"
    org = next(u for u in body["users"] if u["role"] == "organizer")
    cli = next(u for u in body["users"] if u["role"] == "client")
    assert org == {"email": "org@x.io", "role": "organizer", "time_zone": "Europe/Berlin"}
    assert cli == {"email": "cli@x.io", "role": "client", "time_zone": "Europe/Moscow"}  # attendee_tz


def test_rescheduled_includes_previous_start() -> None:
    _, body = build_cloudevent(
        "booking.rescheduled", "bk-1", "ce-2", _payload(previous_start_time="2026-10-01T06:00:00Z"), HOST, CLIENT, NOW
    )
    assert body["previous_start_time"] == "2026-10-01T06:00:00Z"
    assert body["start_time"] == "2026-10-01T07:00:00Z"
    assert "volunteer_id" not in body  # reschedule body per spec omits it


def test_cancelled_includes_reason() -> None:
    _, body = build_cloudevent(
        "booking.cancelled", "bk-1", "ce-3", _payload(cancellation_reason="client no-show"), HOST, CLIENT, NOW
    )
    assert body["cancellation_reason"] == "client no-show"
    assert body["booking_uid"] == "bk-1"
    assert {u["role"] for u in body["users"]} == {"organizer", "client"}
