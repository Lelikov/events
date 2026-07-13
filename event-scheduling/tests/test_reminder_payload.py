"""Reminder CloudEvent builders — shape parity with event-booking + deterministic ce-id."""

import datetime as dt
from uuid import uuid4

from event_scheduling.publishing.dto import ParticipantInfo
from event_scheduling.reminders.dto import DueBookingDTO
from event_scheduling.reminders.payload import build_reminder_command, build_reminder_sent


NOW = dt.datetime(2026, 10, 1, 8, 0, tzinfo=dt.UTC)
BID = uuid4()
DUE = DueBookingDTO(
    id=BID,
    event_type_id=uuid4(),
    host_user_id=uuid4(),
    client_user_id=uuid4(),
    start_time=dt.datetime(2026, 10, 1, 9, 0, tzinfo=dt.UTC),
    end_time=dt.datetime(2026, 10, 1, 10, 0, tzinfo=dt.UTC),
    attendee_time_zone="America/New_York",
    title="Intro call",
)
HOST = ParticipantInfo("host@x.io", "Europe/Berlin", "Hostie", "de")
CLIENT = ParticipantInfo("client@x.io", "America/New_York", "Clint", None)


def test_command_headers_and_body_match_contract() -> None:
    headers, body = build_reminder_command(DUE, HOST, CLIENT, NOW)
    assert headers["ce-type"] == "notification.send_requested"
    assert headers["ce-source"] == "booking"
    assert headers["ce-specversion"] == "1.0"
    assert body["booking_uid"] == str(BID)
    assert body["booking_id"] == str(BID)
    assert body["trigger_event"] == "BOOKING_REMINDER"
    assert body["recipients"] == [
        {"email": "host@x.io", "role": "organizer", "locale": "de"},
        {"email": "client@x.io", "role": "client"},  # locale omitted when falsy
    ]
    td = body["template_data"]
    assert td["booking_uid"] == str(BID)
    assert td["title"] == "Intro call"
    assert td["start_time"] == "2026-10-01T09:00:00+00:00"
    assert td["end_time"] == "2026-10-01T10:00:00+00:00"
    assert td["organizer_name"] == "Hostie"
    assert td["organizer_email"] == "host@x.io"
    assert td["client_name"] == "Clint"
    assert td["client_email"] == "client@x.io"


def test_reminder_sent_body() -> None:
    headers, body = build_reminder_sent(DUE, CLIENT, NOW)
    assert headers["ce-type"] == "booking.reminder_sent"
    assert headers["ce-source"] == "booking"
    assert body == {"booking_uid": str(BID), "email": "client@x.io"}


def test_ce_id_is_deterministic_per_booking_and_type() -> None:
    h1, _ = build_reminder_command(DUE, HOST, CLIENT, NOW)
    h2, _ = build_reminder_command(DUE, HOST, CLIENT, dt.datetime(2026, 10, 1, 8, 1, tzinfo=dt.UTC))
    assert h1["ce-id"] == h2["ce-id"]  # stable across ticks
    hs, _ = build_reminder_sent(DUE, CLIENT, NOW)
    assert hs["ce-id"] != h1["ce-id"]  # different event → different id
