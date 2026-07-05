import datetime as dt

from event_scheduling.dto.schedule import TravelDTO
from event_scheduling.slots.timezones import (
    effective_time_zone,
    group_slots_by_local_date,
    local_interval_to_utc,
)


def test_effective_time_zone_travel_override() -> None:
    travels = [TravelDTO("Asia/Almaty", dt.date(2026, 2, 1), dt.date(2026, 2, 10), "Europe/Berlin")]
    assert effective_time_zone(dt.date(2026, 2, 5), "Europe/Berlin", travels) == "Asia/Almaty"
    assert effective_time_zone(dt.date(2026, 1, 31), "Europe/Berlin", travels) == "Europe/Berlin"
    assert effective_time_zone(dt.date(2026, 2, 11), "Europe/Berlin", travels) == "Europe/Berlin"


def test_effective_time_zone_open_ended_travel() -> None:
    travels = [TravelDTO("Asia/Almaty", dt.date(2026, 2, 1), None, None)]
    assert effective_time_zone(dt.date(2027, 1, 1), "Europe/Berlin", travels) == "Asia/Almaty"


def test_local_interval_to_utc_dst_boundary() -> None:
    # Europe/Berlin springs forward 2026-03-29: CET (+1) before, CEST (+2) after.
    before = local_interval_to_utc(dt.date(2026, 3, 28), dt.time(9), dt.time(17), "Europe/Berlin")
    after = local_interval_to_utc(dt.date(2026, 3, 30), dt.time(9), dt.time(17), "Europe/Berlin")
    assert before[0] == dt.datetime(2026, 3, 28, 8, tzinfo=dt.UTC)  # 09:00 CET → 08:00Z
    assert after[0] == dt.datetime(2026, 3, 30, 7, tzinfo=dt.UTC)  # 09:00 CEST → 07:00Z


def test_group_slots_by_local_date_buckets_and_z_format() -> None:
    slots = [
        dt.datetime(2026, 10, 1, 6, 0, tzinfo=dt.UTC),
        dt.datetime(2026, 10, 1, 21, 30, tzinfo=dt.UTC),  # 2026-10-02 00:30 Moscow (+3)
    ]
    grouped = group_slots_by_local_date(slots, "Europe/Moscow")
    assert grouped == {
        "2026-10-01": ["2026-10-01T06:00:00Z"],
        "2026-10-02": ["2026-10-01T21:30:00Z"],
    }
