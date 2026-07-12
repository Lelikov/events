import datetime as dt

from event_scheduling.booking.limits import limit_exceeded, period_bounds_utc


def test_day_bounds_in_host_tz() -> None:
    # 2026-10-01 23:30 UTC is 2026-10-02 01:30 in Berlin (CEST +2) → the Berlin *day* is Oct 2.
    start = dt.datetime(2026, 10, 1, 23, 30, tzinfo=dt.UTC)
    lo, hi = period_bounds_utc(start, "day", "Europe/Berlin")
    assert lo == dt.datetime(2026, 10, 1, 22, tzinfo=dt.UTC)  # Oct 2 00:00 CEST → Oct 1 22:00Z
    assert hi == dt.datetime(2026, 10, 2, 22, tzinfo=dt.UTC)  # Oct 3 00:00 CEST → Oct 2 22:00Z


def test_week_bounds_iso_monday() -> None:
    # 2026-10-01 is a Thursday; ISO week Mon = 2026-09-28.
    start = dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC)
    lo, hi = period_bounds_utc(start, "week", "Europe/Berlin")
    assert lo == dt.datetime(2026, 9, 27, 22, tzinfo=dt.UTC)  # Mon 2026-09-28 00:00 CEST → 09-27 22:00Z
    assert hi == dt.datetime(2026, 10, 4, 22, tzinfo=dt.UTC)  # next Mon 2026-10-05 00:00 CEST


def test_month_and_year_bounds() -> None:
    start = dt.datetime(2026, 10, 15, 12, tzinfo=dt.UTC)
    mlo, mhi = period_bounds_utc(start, "month", "Europe/Berlin")
    assert mlo == dt.datetime(2026, 9, 30, 22, tzinfo=dt.UTC)  # Oct 1 00:00 CEST
    assert mhi == dt.datetime(2026, 10, 31, 23, tzinfo=dt.UTC)  # Nov 1 00:00 CET (+1, DST ended) → Oct 31 23:00Z
    ylo, yhi = period_bounds_utc(start, "year", "Europe/Berlin")
    assert ylo == dt.datetime(2025, 12, 31, 23, tzinfo=dt.UTC)  # 2026-01-01 00:00 CET
    assert yhi == dt.datetime(2026, 12, 31, 23, tzinfo=dt.UTC)  # 2027-01-01 00:00 CET


def test_limit_exceeded_count_and_duration() -> None:
    assert limit_exceeded("booking_count", 3, 3, 0, 60) is True  # already at 3
    assert limit_exceeded("booking_count", 3, 2, 0, 60) is False
    assert limit_exceeded("booking_duration", 120, 0, 90, 60) is True  # 90+60 > 120
    assert limit_exceeded("booking_duration", 120, 0, 60, 60) is False  # 60+60 == 120 ok
    assert limit_exceeded("unknown", 1, 99, 99, 99) is False
