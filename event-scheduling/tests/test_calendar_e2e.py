import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text


HDRS = {"actor-source": "admin"}


async def _seed_event_type(client, owner: str) -> str:
    """Mirror tests/test_slots_api.py::_seed_event_type for a single host.

    A schedule Thu 09:00-17:00 Europe/Berlin backs one host on a fresh event
    type, so a known slot exists at 2026-10-01T07:00:00Z (Thu 09:00 CEST).
    """
    client.put(
        f"/api/v1/schedules/{owner}",
        json={
            "name": "s",
            "time_zone": "Europe/Berlin",
            "weekly_hours": [{"day_of_week": 4, "start_time": "09:00", "end_time": "17:00"}],
            "date_overrides": [],
        },
        headers=HDRS,
    )
    sid = client.get(f"/api/v1/schedules/{owner}").json()["schedule"]["id"]
    body = {
        "slug": f"et-{uuid4().hex[:8]}",
        "title": "Intro",
        "duration_minutes": 60,
        "slot_interval_minutes": 30,
        "min_booking_notice_minutes": 0,
        "buffer_before_minutes": 0,
        "buffer_after_minutes": 0,
        "hosts": [{"user_id": owner, "schedule_id": sid}],
        "booking_limits": [],
    }
    return client.post("/api/v1/event-types", json=body).json()["id"]


@pytest.mark.asyncio
async def test_external_busy_excludes_slot(client, sessionmaker_fixture) -> None:
    host = uuid4()
    et_id = await _seed_event_type(client, str(host))

    # 1) Without any external calendar, the known slot T is present.
    resp_before = client.get(
        "/api/v1/slots",
        params={
            "event_type_id": et_id,
            "start": "2026-10-01T00:00:00Z",
            "end": "2026-10-02T00:00:00Z",
            "time_zone": "Europe/Berlin",
        },
    )
    assert resp_before.status_code == 200
    slots_before = resp_before.json()["slots"]
    t = "2026-10-01T07:00:00Z"
    assert t in slots_before["2026-10-01"]

    # 2) Seed an enabled external calendar for the host with an event covering T.
    cal_id = uuid4()
    async with sessionmaker_fixture() as s:
        await s.execute(
            text("INSERT INTO external_calendar (id, host_user_id, url, enabled) VALUES (:id,:h,:u,true)"),
            {"id": cal_id, "h": host, "u": f"https://c/{cal_id}.ics"},
        )
        await s.execute(
            text("INSERT INTO external_calendar_event (calendar_id, busy_start, busy_end) VALUES (:c,:s,:e)"),
            {
                "c": cal_id,
                "s": dt.datetime(2026, 10, 1, 7, tzinfo=dt.UTC),
                "e": dt.datetime(2026, 10, 1, 8, tzinfo=dt.UTC),
            },
        )
        await s.commit()

    # 3) T is now absent from every date bucket.
    resp_after = client.get(
        "/api/v1/slots",
        params={
            "event_type_id": et_id,
            "start": "2026-10-01T00:00:00Z",
            "end": "2026-10-02T00:00:00Z",
            "time_zone": "Europe/Berlin",
        },
    )
    assert resp_after.status_code == 200
    slots_after = resp_after.json()["slots"]
    for bucket in slots_after.values():
        assert t not in bucket
