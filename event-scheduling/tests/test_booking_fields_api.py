from uuid import uuid4

import pytest


AUTH = {"Authorization": "Bearer test-scheduling-key"}


def _sched_owner(client) -> tuple[str, str]:
    owner = str(uuid4())
    client.put(
        f"/api/v1/schedules/{owner}",
        json={
            "name": "s",
            "time_zone": "Europe/Moscow",
            "weekly_hours": [{"day_of_week": 1, "start_time": "09:00", "end_time": "17:00"}],
            "date_overrides": [],
        },
        headers={"actor-source": "admin"},
    )
    sid = client.get(f"/api/v1/schedules/{owner}").json()["schedule"]["id"]
    return owner, sid


@pytest.fixture
def seeded_event_type_id(client) -> str:
    owner, sid = _sched_owner(client)
    payload = {
        "slug": f"et-{uuid4().hex[:8]}",
        "title": "Consult",
        "duration_minutes": 60,
        "slot_interval_minutes": 30,
        "min_booking_notice_minutes": 0,
        "buffer_before_minutes": 0,
        "buffer_after_minutes": 0,
        "hosts": [{"user_id": owner, "schedule_id": sid}],
        "booking_limits": [],
    }
    created = client.post("/api/v1/event-types", json=payload)
    assert created.status_code == 201
    return created.json()["id"]


def test_put_then_get_and_event_type_exposes_fields(client, seeded_event_type_id) -> None:
    body = {
        "items": [
            {"field_type": "textarea", "label": "Reason", "required": True},
            {"field_type": "select", "label": "Topic", "options": [{"value": "a", "label": "A"}]},
        ]
    }
    r = client.put(f"/api/v1/event-types/{seeded_event_type_id}/booking-fields", json=body, headers=AUTH)
    assert r.status_code == 200
    keys = [f["field_key"] for f in r.json()["items"]]
    assert keys == ["reason", "topic"]

    g = client.get(f"/api/v1/event-types/{seeded_event_type_id}/booking-fields", headers=AUTH)
    assert [f["field_key"] for f in g.json()["items"]] == ["reason", "topic"]

    # the event-type read now carries booking_fields
    et = client.get(f"/api/v1/event-types/{seeded_event_type_id}", headers=AUTH)
    assert [f["field_key"] for f in et.json()["booking_fields"]] == ["reason", "topic"]


def test_put_invalid_option_type_is_422(client, seeded_event_type_id) -> None:
    body = {"items": [{"field_type": "select", "label": "NoOpts"}]}  # option type, no options
    r = client.put(f"/api/v1/event-types/{seeded_event_type_id}/booking-fields", json=body, headers=AUTH)
    assert r.status_code == 422


def test_get_unknown_event_type_returns_empty_list(client) -> None:
    r = client.get(f"/api/v1/event-types/{uuid4()}/booking-fields", headers=AUTH)
    assert r.status_code == 200
    assert r.json()["items"] == []


def test_put_unknown_event_type_404(client) -> None:
    body = {"items": [{"field_type": "text", "label": "X"}]}
    r = client.put(f"/api/v1/event-types/{uuid4()}/booking-fields", json=body, headers=AUTH)
    assert r.status_code == 404
