AUTH = {"Authorization": "Bearer test-scheduling-key"}


def test_booking_requires_and_stores_answers(client, bookable_event_type):
    et_id, client_user_id, a_valid_start = bookable_event_type  # fixture: seeds a host+schedule so a slot is bookable
    # configure a required textarea
    client.put(
        f"/api/v1/event-types/{et_id}/booking-fields",
        headers=AUTH,
        json={"items": [{"field_type": "textarea", "label": "Reason", "required": True}]},
    )
    base = {
        "event_type_id": str(et_id),
        "client_user_id": str(client_user_id),
        "start_time": a_valid_start,
        "attendee_time_zone": "UTC",
    }

    # missing the required answer → 422
    r = client.post("/api/v1/bookings", json=base, headers=AUTH)
    assert r.status_code == 422

    # with the answer → 201 and it's stored + echoed on the response
    r2 = client.post(
        "/api/v1/bookings",
        json={**base, "field_answers": [{"key": "reason", "value": "help"}]},
        headers=AUTH,
    )
    assert r2.status_code == 201
    answers = r2.json()["field_answers"]
    assert answers == [{"key": "reason", "label": "Reason", "type": "textarea", "value": "help"}]
