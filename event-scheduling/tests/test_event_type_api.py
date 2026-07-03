from uuid import uuid4


HDRS = {"authorization": "ignored-by-fixture"}  # client fixture already sets bearer


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


def _payload(slug: str, owner: str, sid: str) -> dict:
    return {
        "slug": slug,
        "title": "Разбор",
        "duration_minutes": 60,
        "slot_interval_minutes": 30,
        "min_booking_notice_minutes": 120,
        "buffer_before_minutes": 5,
        "buffer_after_minutes": 5,
        "hosts": [{"user_id": owner, "schedule_id": sid}],
        "booking_limits": [{"limit_type": "booking_count", "period": "day", "value": 3}],
    }


def test_create_and_get_event_type(client) -> None:
    owner, sid = _sched_owner(client)
    created = client.post("/api/v1/event-types", json=_payload("razbor", owner, sid))
    assert created.status_code == 201
    et_id = created.json()["id"]
    got = client.get(f"/api/v1/event-types/{et_id}")
    assert got.status_code == 200
    assert got.json()["duration_minutes"] == 60
    assert len(got.json()["hosts"]) == 1
    assert len(got.json()["booking_limits"]) == 1


def test_update_replaces_hosts_and_limits(client) -> None:
    owner, sid = _sched_owner(client)
    et_id = client.post("/api/v1/event-types", json=_payload("upd", owner, sid)).json()["id"]
    upd = _payload("upd", owner, sid)
    upd["booking_limits"] = []
    resp = client.put(f"/api/v1/event-types/{et_id}", json=upd)
    assert resp.status_code == 200
    assert resp.json()["booking_limits"] == []


def test_create_rejects_zero_limit(client) -> None:
    owner, sid = _sched_owner(client)
    bad = _payload("bad", owner, sid)
    bad["booking_limits"] = [{"limit_type": "booking_count", "period": "day", "value": 0}]
    assert client.post("/api/v1/event-types", json=bad).status_code == 422


def test_delete_event_type(client) -> None:
    owner, sid = _sched_owner(client)
    et_id = client.post("/api/v1/event-types", json=_payload("del", owner, sid)).json()["id"]
    assert client.delete(f"/api/v1/event-types/{et_id}").status_code == 204
    assert client.get(f"/api/v1/event-types/{et_id}").status_code == 404
