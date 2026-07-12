import datetime as dt
from uuid import UUID

from event_scheduling.booking.assignment import pick_host, rank_hosts
from event_scheduling.booking.dto import HostStat


A = UUID(int=1)
B = UUID(int=2)
C = UUID(int=3)


def test_fewest_future_wins() -> None:
    stats = [HostStat(A, 3, None), HostStat(B, 1, None), HostStat(C, 2, None)]
    assert rank_hosts(stats) == [B, C, A]


def test_tiebreak_least_recently_assigned() -> None:
    old = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    new = dt.datetime(2026, 6, 1, tzinfo=dt.UTC)
    stats = [HostStat(A, 2, new), HostStat(B, 2, old)]
    assert rank_hosts(stats) == [B, A]  # same future; B assigned longer ago → first


def test_never_assigned_beats_assigned_on_tie() -> None:
    stats = [HostStat(A, 2, dt.datetime(2026, 1, 1, tzinfo=dt.UTC)), HostStat(B, 2, None)]
    assert rank_hosts(stats) == [B, A]  # None (never assigned) first


def test_pick_host_none_when_empty() -> None:
    assert pick_host([]) is None
    assert pick_host([HostStat(A, 0, None)]) == A
