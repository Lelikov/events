from uuid import uuid4


HDRS = {"actor-source": "admin"}


def _bundle():
    return {
        "name": "s",
        "time_zone": "Europe/Moscow",
        "weekly_hours": [{"day_of_week": 1, "start_time": "09:00", "end_time": "17:00"}],
        "date_overrides": [],
    }


def test_each_put_appends_one_snapshot(client) -> None:
    owner = str(uuid4())
    client.put(f"/api/v1/schedules/{owner}", json=_bundle(), headers=HDRS)
    second = _bundle()
    second["name"] = "s2"
    client.put(f"/api/v1/schedules/{owner}", json=second, headers=HDRS)
    resp = client.get(f"/api/v1/schedules/{owner}/change-log")
    assert resp.status_code == 200
    log = resp.json()["entries"]
    assert len(log) == 2
    # по убыванию at: свежий первый
    assert log[0]["snapshot"]["schedule"]["name"] == "s2"
    assert log[0]["actor_source"] == "admin"
