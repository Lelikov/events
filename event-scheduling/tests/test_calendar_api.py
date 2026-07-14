from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_create_list_delete(client) -> None:
    host = str(uuid4())
    r = client.post("/api/v1/calendars", json={"host_user_id": host, "url": "https://cal/x.ics"})
    assert r.status_code == 201
    cal_id = r.json()["id"]
    assert r.json()["kind"] == "ical_url"
    assert r.json()["enabled"] is True

    dup = client.post("/api/v1/calendars", json={"host_user_id": host, "url": "https://cal/x.ics"})
    assert dup.status_code == 409

    bad = client.post("/api/v1/calendars", json={"host_user_id": host, "url": "file:///etc/passwd"})
    assert bad.status_code == 422

    lst = client.get(f"/api/v1/calendars?host_user_id={host}")
    assert [c["id"] for c in lst.json()["items"]] == [cal_id]

    assert client.delete(f"/api/v1/calendars/{cal_id}").status_code == 204
    assert client.get(f"/api/v1/calendars?host_user_id={host}").json()["items"] == []
