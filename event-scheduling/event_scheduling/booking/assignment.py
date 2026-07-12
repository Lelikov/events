from uuid import UUID

from event_scheduling.booking.dto import HostStat


def _sort_key(stat: HostStat) -> tuple[int, int, float]:
    # fewest future first; then never-assigned (0) before assigned (1); then oldest assignment first
    if stat.last_assigned_at is None:
        return (stat.future_count, 0, 0.0)
    return (stat.future_count, 1, stat.last_assigned_at.timestamp())


def rank_hosts(stats: list[HostStat]) -> list[UUID]:
    return [s.user_id for s in sorted(stats, key=_sort_key)]


def pick_host(stats: list[HostStat]) -> UUID | None:
    ranked = rank_hosts(stats)
    if not ranked:
        return None
    return ranked[0]
