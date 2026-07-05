import datetime as dt

from event_scheduling.slots.domain import (
    from_epoch_min,
    merge_intervals,
    slice_into_slots,
    subtract_intervals,
    to_epoch_min,
)
from event_scheduling.slots.dto import Interval


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
    # 09:00–11:00 (in minutes 540..660), duration 60, step 30 → 540, 570, 600 (600+60=660 ok); 630+60=690>660 no
    slots = slice_into_slots([Interval(540, 660)], duration_min=60, step_min=30, not_before_min=0)
    assert slots == [540, 570, 600]


def test_slice_respects_not_before() -> None:
    slots = slice_into_slots([Interval(540, 660)], duration_min=60, step_min=30, not_before_min=580)
    assert slots == [600]  # 540,570 dropped (< 580); 600 kept


def test_slice_aligns_to_each_interval_start() -> None:
    slots = slice_into_slots([Interval(0, 60), Interval(100, 160)], duration_min=60, step_min=30, not_before_min=0)
    assert slots == [0, 100]  # each interval starts its own stepping
