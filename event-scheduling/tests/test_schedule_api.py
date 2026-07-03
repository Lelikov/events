from uuid import uuid4

OWNER = str(uuid4())
HDRS = {"actor-source": "admin"}


def _bundle() -> dict:
    return {
        "name": "Консультации",
        "time_zone": "Europe/Moscow",
        "weekly_hours": [
            {"day_of_week": 1, "start_time": "09:00", "end_time": "17:00"},
            {"day_of_week": 2, "start_time": "09:00", "end_time": "13:00"},
        ],
        "date_overrides": [
            {"date": "2026-01-07", "start_time": None, "end_time": None},
            {"date": "2026-01-08", "start_time": "10:00", "end_time": "12:00"},
        ],
    }


def test_put_creates_schedule_and_returns_bundle(client) -> None:
    resp = client.put(f"/api/v1/schedules/{OWNER}", json=_bundle(), headers=HDRS)
    assert resp.status_code == 200
    body = resp.json()
    assert body["schedule"]["time_zone"] == "Europe/Moscow"
    assert len(body["weekly_hours"]) == 2
    assert len(body["date_overrides"]) == 2


def test_put_is_replace_all(client) -> None:
    client.put(f"/api/v1/schedules/{OWNER}", json=_bundle(), headers=HDRS)
    smaller = _bundle()
    smaller["weekly_hours"] = [{"day_of_week": 3, "start_time": "08:00", "end_time": "10:00"}]
    smaller["date_overrides"] = []
    resp = client.put(f"/api/v1/schedules/{OWNER}", json=smaller, headers=HDRS)
    assert resp.status_code == 200
    body = resp.json()
    assert len(body["weekly_hours"]) == 1
    assert body["weekly_hours"][0]["day_of_week"] == 3
    assert body["date_overrides"] == []


def test_put_rejects_bad_timezone(client) -> None:
    bad = _bundle()
    bad["time_zone"] = "Mars/Phobos"
    resp = client.put(f"/api/v1/schedules/{OWNER}", json=bad, headers=HDRS)
    assert resp.status_code == 422
