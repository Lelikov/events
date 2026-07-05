from uuid import UUID, uuid4

import pytest

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.slots.read_adapter import SlotsReadAdapter


HDRS = {"actor-source": "admin"}


def _slots(client, et_id: str, start: str, end: str, tz: str = "Europe/Berlin"):
    return client.get("/api/v1/slots", params={"event_type_id": et_id, "start": start, "end": end, "time_zone": tz})


async def _seed_event_type(client, owners: list[str]) -> str:
    # Create a schedule per owner (Mon 09:00-17:00), then an event type hosting them.
    sids = []
    for owner in owners:
        client.put(f"/api/v1/schedules/{owner}", json={
            "name": "s", "time_zone": "Europe/Berlin",
            "weekly_hours": [{"day_of_week": 4, "start_time": "09:00", "end_time": "17:00"}],
            "date_overrides": [],
        }, headers=HDRS)
        sids.append(client.get(f"/api/v1/schedules/{owner}").json()["schedule"]["id"])
    hosts = [{"user_id": o, "schedule_id": s} for o, s in zip(owners, sids, strict=True)]
    body = {"slug": f"et-{uuid4().hex[:8]}", "title": "Intro", "duration_minutes": 60,
            "slot_interval_minutes": 30, "min_booking_notice_minutes": 0,
            "buffer_before_minutes": 0, "buffer_after_minutes": 0,
            "hosts": hosts, "booking_limits": []}
    return client.post("/api/v1/event-types", json=body).json()["id"]


@pytest.mark.asyncio
async def test_read_adapter_loads_bundle(client, sessionmaker_fixture) -> None:
    owner = str(uuid4())
    et_id = await _seed_event_type(client, [owner])
    async with sessionmaker_fixture() as session:
        bundle = await SlotsReadAdapter(SqlExecutor(session)).load(UUID(et_id))
    assert bundle is not None
    assert bundle.event_type.duration_minutes == 60
    assert bundle.event_type.slot_interval_minutes == 30
    assert len(bundle.hosts) == 1
    assert bundle.hosts[0].time_zone == "Europe/Berlin"
    assert bundle.hosts[0].weekly_hours[0].day_of_week == 4


@pytest.mark.asyncio
async def test_read_adapter_missing_event_type_returns_none(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as session:
        assert await SlotsReadAdapter(SqlExecutor(session)).load(uuid4()) is None


@pytest.mark.asyncio
async def test_slots_endpoint_two_hosts(client) -> None:
    o1, o2 = str(uuid4()), str(uuid4())
    et_id = await _seed_event_type(client, [o1, o2])
    resp = _slots(client, et_id, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")
    assert resp.status_code == 200
    body = resp.json()
    assert body["event_type_id"] == et_id
    # Thu 09:00-17:00 Berlin (CEST +2) → first slot 07:00Z
    assert body["slots"]["2026-10-01"][0] == "2026-10-01T07:00:00Z"


@pytest.mark.asyncio
async def test_slots_unknown_event_type_404(client) -> None:
    resp = _slots(client, str(uuid4()), "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z")
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_slots_bad_timezone_422(client) -> None:
    o = str(uuid4())
    et_id = await _seed_event_type(client, [o])
    resp = _slots(client, et_id, "2026-10-01T00:00:00Z", "2026-10-02T00:00:00Z", tz="Mars/Base")
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_slots_window_too_large_422(client) -> None:
    o = str(uuid4())
    et_id = await _seed_event_type(client, [o])
    resp = _slots(client, et_id, "2026-10-01T00:00:00Z", "2027-01-01T00:00:00Z")  # > 62 days
    assert resp.status_code == 422


@pytest.mark.asyncio
async def test_slots_end_before_start_422(client) -> None:
    o = str(uuid4())
    et_id = await _seed_event_type(client, [o])
    resp = _slots(client, et_id, "2026-10-02T00:00:00Z", "2026-10-01T00:00:00Z")
    assert resp.status_code == 422
