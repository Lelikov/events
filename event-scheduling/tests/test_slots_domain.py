import datetime as dt
from uuid import uuid4

from event_scheduling.dto.schedule import DateOverrideDTO, TravelDTO, WeeklyHourDTO
from event_scheduling.slots.domain import (
    from_epoch_min,
    host_availability_intervals,
    merge_intervals,
    slice_into_slots,
    subtract_intervals,
    to_epoch_min,
)
from event_scheduling.slots.dto import HostSchedule, Interval


def test_epoch_roundtrip() -> None:
    d = dt.datetime(2026, 10, 1, 6, 30, tzinfo=dt.UTC)
    assert from_epoch_min(to_epoch_min(d)) == d


def test_merge_unions_overlapping_and_adjacent() -> None:
    ivs = [Interval(0, 60), Interval(30, 90), Interval(90, 120), Interval(200, 210)]
    assert merge_intervals(ivs) == [Interval(0, 120), Interval(200, 210)]


def test_merge_empty() -> None:
    assert merge_intervals([]) == []


def test_subtract_removes_busy() -> None:
    base = [Interval(0, 120)]
    busy = [Interval(30, 60), Interval(100, 130)]
    assert subtract_intervals(base, busy) == [Interval(0, 30), Interval(60, 100)]


def test_slice_fits_duration_and_step() -> None:
    # 09:00-11:00 (in minutes 540..660), duration 60, step 30 -> 540, 570, 600 (600+60=660 ok); 630+60=690>660 no
    slots = slice_into_slots([Interval(540, 660)], duration_min=60, step_min=30, not_before_min=0)
    assert slots == [540, 570, 600]


def test_slice_respects_not_before() -> None:
    slots = slice_into_slots([Interval(540, 660)], duration_min=60, step_min=30, not_before_min=580)
    assert slots == [600]  # 540,570 dropped (< 580); 600 kept


def test_slice_aligns_to_each_interval_start() -> None:
    slots = slice_into_slots([Interval(0, 60), Interval(100, 160)], duration_min=60, step_min=30, not_before_min=0)
    assert slots == [0, 100]  # each interval starts its own stepping


# ---------------------------------------------------------------------------
# host_availability_intervals tests (Task 3)
# ---------------------------------------------------------------------------


def _host(**kw) -> HostSchedule:
    base = {"user_id": uuid4(), "time_zone": "Europe/Berlin", "weekly_hours": [], "date_overrides": [], "travels": []}
    base.update(kw)
    return HostSchedule(**base)


def test_host_weekly_hours_single_day() -> None:
    # 2026-10-01 is a Thursday (isoweekday 4). Berlin CEST (+2) in October.
    host = _host(weekly_hours=[WeeklyHourDTO(4, dt.time(9), dt.time(17))])
    ws = dt.datetime(2026, 10, 1, 0, tzinfo=dt.UTC)
    we = dt.datetime(2026, 10, 2, 0, tzinfo=dt.UTC)
    ivs = host_availability_intervals(host, ws, we)
    # 09:00 CEST → 07:00Z, 17:00 CEST → 15:00Z, clipped to the 24h UTC window
    start = to_epoch_min(dt.datetime(2026, 10, 1, 7, tzinfo=dt.UTC))
    end = to_epoch_min(dt.datetime(2026, 10, 1, 15, tzinfo=dt.UTC))
    assert ivs == [Interval(start, end)]


def test_host_date_override_replaces_weekly() -> None:
    host = _host(
        weekly_hours=[WeeklyHourDTO(4, dt.time(9), dt.time(17))],
        date_overrides=[DateOverrideDTO(dt.date(2026, 10, 1), dt.time(10), dt.time(12))],
    )
    ws = dt.datetime(2026, 10, 1, 0, tzinfo=dt.UTC)
    we = dt.datetime(2026, 10, 2, 0, tzinfo=dt.UTC)
    ivs = host_availability_intervals(host, ws, we)
    start = to_epoch_min(dt.datetime(2026, 10, 1, 8, tzinfo=dt.UTC))  # 10:00 CEST → 08:00Z
    end = to_epoch_min(dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC))  # 12:00 CEST → 10:00Z
    assert ivs == [Interval(start, end)]


def test_host_full_day_block_override_yields_nothing() -> None:
    host = _host(
        weekly_hours=[WeeklyHourDTO(4, dt.time(9), dt.time(17))],
        date_overrides=[DateOverrideDTO(dt.date(2026, 10, 1), None, None)],
    )
    ws = dt.datetime(2026, 10, 1, 0, tzinfo=dt.UTC)
    we = dt.datetime(2026, 10, 2, 0, tzinfo=dt.UTC)
    assert host_availability_intervals(host, ws, we) == []


def test_host_travel_shifts_timezone() -> None:
    host = _host(
        weekly_hours=[WeeklyHourDTO(4, dt.time(9), dt.time(17))],
        travels=[TravelDTO("Asia/Almaty", dt.date(2026, 10, 1), dt.date(2026, 10, 3), "Europe/Berlin")],
    )
    ws = dt.datetime(2026, 10, 1, 0, tzinfo=dt.UTC)
    we = dt.datetime(2026, 10, 2, 0, tzinfo=dt.UTC)
    ivs = host_availability_intervals(host, ws, we)
    # Almaty UTC+5 (no DST): 09:00 → 04:00Z, 17:00 → 12:00Z
    start = to_epoch_min(dt.datetime(2026, 10, 1, 4, tzinfo=dt.UTC))
    end = to_epoch_min(dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC))
    assert ivs == [Interval(start, end)]
