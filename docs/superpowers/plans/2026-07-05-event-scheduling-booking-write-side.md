# event-scheduling — write-side бронирования (срез 3) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в `event-scheduling` доменное бронирование: таблица `booking` с exclusion-constraint против двойной брони, round-robin-назначение хоста, создание/отмена/перенос через HTTP, реальный `BookingBusyTimesSource` (замыкает seam слотов), enforcement `booking_limit`.

**Architecture:** Изолированный модуль `event_scheduling/booking/` (по образцу `slots/`): чистые `assignment.py` (getLuckyUser) и `limits.py` (границы периода) без IO; IO-слой — read/write-адаптеры + `BookingBusyTimesSource` + сервис-оркестратор. Инвариант «нет двойной брони» живёт в схеме (Postgres `EXCLUDE USING gist`); создание — оптимистичная вставка + retry по exclusion над пулом свободных хостов. Доступность пере-валидируется переиспользованием чистого ядра `slots/domain.py`.

**Tech Stack:** Python 3.14, FastAPI, Dishka, SQLAlchemy async (raw SQL via `SqlExecutor`), Postgres `btree_gist` + `EXCLUDE USING gist`, `zoneinfo`, pytest.

**Spec:** `docs/superpowers/specs/2026-07-05-event-scheduling-booking-write-side-design.md`
**Target service (main):** `event-scheduling/` — модель среза 1 (8 таблиц) + движок слотов среза 2 (`event_scheduling/slots/`).

## Global Constraints

- **Python `>=3.14`**; deps через `uv`; работать из `event-scheduling/`.
- **Ruff** 120 / py314; **NO `elif`; avoid `else`** — guard clauses / early returns / mapping dicts.
- **Raw SQL только** через `SqlExecutor` (`:param`); ORM в `db/models.py` — только для alembic. DTO frozen; Pydantic только в `schemas/`.
- **Anti-двойная-бронь — Postgres exclusion constraint** на сырых `[start,end)`; буферы — в `BookingBusyTimesSource`, не в constraint.
- **Назначение хоста** — наименьшее число предстоящих confirmed-броней; тай-брейк «наименее недавно назначенному» (`MAX(created_at)`, NULL первым).
- **Клиент** — `client_user_id` (opaque UUID) + `attendee_time_zone`; без PII.
- **Lifecycle** — soft-cancel (`status='cancelled'`); reschedule **in-place, тот же хост**; append-only `booking_change_log` (created/rescheduled/cancelled), пишется в той же транзакции.
- **Лимиты** — per event_type; границы периода в **tz расписания хоста**.
- Ветка реализации: `feat/booking-write-side-impl` (спек уже на этой линии).
- Все внутренние времена — UTC-aware `datetime`; движок слотов — epoch-минуты (переиспользуем `slots/domain.py`).

## Reuse from earlier slices

- `event_scheduling/dto/schedule.py`: `ActorDTO(source, user_id)`, `WeeklyHourDTO`, `DateOverrideDTO`, `TravelDTO`.
- `event_scheduling/dto/event_type.py`: `BookingLimitDTO(limit_type, period, value)`.
- `event_scheduling/slots/dto.py`: `EventTypeConfig`, `HostSchedule`, `Interval`, `SlotBundle`.
- `event_scheduling/slots/read_adapter.py`: `SlotsReadAdapter.load(event_type_id) -> SlotBundle | None` (event_type config + hosts).
- `event_scheduling/slots/domain.py`: `host_availability_intervals`, `merge_intervals`, `subtract_intervals`, `to_epoch_min`, `from_epoch_min`.
- `event_scheduling/slots/interfaces.py`: `Clock`. `event_scheduling/slots/service.py`: `SystemClock`.
- `event_scheduling/interfaces/busy_times.py`: `BusyTimesSource`, `TimeWindow`, `BusyInterval`, `StubBusyTimesSource`.
- `event_scheduling/errors.py`: `ValidationError`(422), `NotFoundError`(404), `ConflictError`(409).
- `event_scheduling/validation.py`: `validate_time_zone`.
- `event_scheduling/adapters/sql.py`/`interfaces/sql.py`: `SqlExecutor`/`ISqlExecutor`.

---

## File Structure

```
event-scheduling/
├── alembic/versions/0002_booking.py            # btree_gist + booking + booking_change_log
├── event_scheduling/
│   ├── db/models.py                            # + Booking, BookingChangeLog ORM (modify)
│   ├── booking/
│   │   ├── __init__.py
│   │   ├── dto.py                              # BookingDTO, CreateBookingDTO, HostStat, BookingChangeEntryDTO
│   │   ├── assignment.py                       # rank_hosts / pick_host (pure)
│   │   ├── limits.py                           # period_bounds_utc, limit_exceeded (pure)
│   │   ├── interfaces.py                        # IBookingReadAdapter, IBookingWriteAdapter, IBookingService
│   │   ├── read_adapter.py                      # BookingReadAdapter (get/list/history/limits/host_stats/period counts)
│   │   ├── busy_source.py                       # BookingBusyTimesSource
│   │   ├── write_adapter.py                     # BookingWriteAdapter (insert/update/cancel/append log)
│   │   └── service.py                           # BookingService (create/cancel/reschedule/get/list/history)
│   ├── routers/booking.py                      # /api/v1/bookings
│   ├── schemas/booking.py                      # Pydantic req/resp
│   ├── ioc.py                                  # + booking providers; swap Stub→Booking busy source (modify)
│   └── main.py                                 # + include booking_router (modify)
├── tests/conftest.py                           # + include booking_router in app fixture (modify)
├── tests/test_booking_schema.py
├── tests/test_booking_assignment.py
├── tests/test_booking_limits.py
├── tests/test_booking_busy_source.py
├── tests/test_booking_api.py
```

---

## Task 1: Migration 0002 + ORM + booking DTOs + exclusion constraint

**Files:**
- Create: `alembic/versions/0002_booking.py`, `event_scheduling/booking/__init__.py`, `event_scheduling/booking/dto.py`
- Modify: `event_scheduling/db/models.py`
- Test: `tests/test_booking_schema.py`

**Interfaces:**
- Produces: tables `booking`, `booking_change_log`; ORM `Booking`, `BookingChangeLog`; DTOs `BookingDTO`, `CreateBookingDTO`, `HostStat`, `BookingChangeEntryDTO`.

- [ ] **Step 1: Failing test `tests/test_booking_schema.py`**

```python
import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

_INSERT = (
    "INSERT INTO booking (event_type_id, host_user_id, client_user_id, start_time, end_time, "
    "status, attendee_time_zone) VALUES (:et, :h, :c, :s, :e, :st, 'Europe/Berlin')"
)


async def _seed_event_type_row(conn, et_id) -> None:
    await conn.execute(text(
        "INSERT INTO event_type (id, slug, title, duration_minutes) "
        "VALUES (:id, :slug, 't', 60)"), {"id": et_id, "slug": f"et-{et_id}"})


@pytest.mark.asyncio
async def test_overlapping_confirmed_same_host_rejected(_migrated: str) -> None:
    eng = create_async_engine(_migrated)
    et, host = uuid4(), uuid4()
    async with eng.begin() as conn:
        await _seed_event_type_row(conn, et)
        await conn.execute(text(_INSERT), {"et": et, "h": host, "c": uuid4(),
            "s": dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC), "e": dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC),
            "st": "confirmed"})
    async with eng.begin() as conn:
        with pytest.raises(Exception):  # noqa: B017 - ExclusionViolation
            await conn.execute(text(_INSERT), {"et": et, "h": host, "c": uuid4(),
                "s": dt.datetime(2026, 10, 1, 9, 30, tzinfo=dt.UTC), "e": dt.datetime(2026, 10, 1, 10, 30, tzinfo=dt.UTC),
                "st": "confirmed"})
    await eng.dispose()


@pytest.mark.asyncio
async def test_cancelled_does_not_block(_migrated: str) -> None:
    eng = create_async_engine(_migrated)
    et, host = uuid4(), uuid4()
    async with eng.begin() as conn:
        await _seed_event_type_row(conn, et)
        await conn.execute(text(_INSERT), {"et": et, "h": host, "c": uuid4(),
            "s": dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC), "e": dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC),
            "st": "cancelled"})
        # overlapping confirmed is allowed because the other row is cancelled
        await conn.execute(text(_INSERT), {"et": et, "h": host, "c": uuid4(),
            "s": dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC), "e": dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC),
            "st": "confirmed"})
    await eng.dispose()
```

- [ ] **Step 2: Run — FAIL.** `cd event-scheduling && uv run pytest tests/test_booking_schema.py -v` (tables don't exist).

- [ ] **Step 3: ORM in `event_scheduling/db/models.py`** (append; import `JSONB` already present pattern)

```python
class Booking(Base):
    __tablename__ = "booking"
    id: Mapped[str] = _uuid_pk()
    event_type_id: Mapped[str] = mapped_column(UUID(as_uuid=True), ForeignKey("event_type.id", ondelete="RESTRICT"), nullable=False)
    host_user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    client_user_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    end_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'confirmed'"))
    attendee_time_zone: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    __table_args__ = (
        CheckConstraint("end_time > start_time", name="ck_booking_range"),
        CheckConstraint("status IN ('confirmed','cancelled')", name="ck_booking_status"),
        Index("ix_booking_host", "host_user_id", "status", "start_time"),
        Index("ix_booking_event_type", "event_type_id", "status", "start_time"),
        Index("ix_booking_client", "client_user_id"),
    )


class BookingChangeLog(Base):
    __tablename__ = "booking_change_log"
    id: Mapped[str] = _uuid_pk()
    booking_id: Mapped[str] = mapped_column(UUID(as_uuid=True), nullable=False)  # no FK: survives all
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    from_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    from_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    to_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    to_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    actor_source: Mapped[str] = mapped_column(Text, nullable=False)
    actor_user_id: Mapped[str | None] = mapped_column(UUID(as_uuid=True), nullable=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"), nullable=False)
    __table_args__ = (CheckConstraint("kind IN ('created','rescheduled','cancelled')", name="ck_booking_log_kind"),)
```
> The `EXCLUDE` constraint is NOT expressible in the ORM `__table_args__` cleanly across dialects; it is created in the migration via raw DDL (Step 4). The ORM is alembic-only, so omitting it from the model is acceptable (document with a comment on the `Booking` class).

- [ ] **Step 4: Migration `alembic/versions/0002_booking.py`**

```python
from collections.abc import Sequence
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels = None
depends_on = None
_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")
    op.create_table(
        "booking",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("event_type_id", _UUID, nullable=False),
        sa.Column("host_user_id", _UUID, nullable=False),
        sa.Column("client_user_id", _UUID, nullable=False),
        sa.Column("start_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("end_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.Text(), server_default=sa.text("'confirmed'"), nullable=False),
        sa.Column("attendee_time_zone", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["event_type_id"], ["event_type.id"], ondelete="RESTRICT"),
        sa.CheckConstraint("end_time > start_time", name="ck_booking_range"),
        sa.CheckConstraint("status IN ('confirmed','cancelled')", name="ck_booking_status"),
    )
    op.create_index("ix_booking_host", "booking", ["host_user_id", "status", "start_time"])
    op.create_index("ix_booking_event_type", "booking", ["event_type_id", "status", "start_time"])
    op.create_index("ix_booking_client", "booking", ["client_user_id"])
    op.execute(
        "ALTER TABLE booking ADD CONSTRAINT ex_booking_no_overlap "
        "EXCLUDE USING gist (host_user_id WITH =, tstzrange(start_time, end_time) WITH &&) "
        "WHERE (status = 'confirmed')"
    )
    op.create_table(
        "booking_change_log",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("booking_id", _UUID, nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("from_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("from_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("to_start", sa.DateTime(timezone=True), nullable=True),
        sa.Column("to_end", sa.DateTime(timezone=True), nullable=True),
        sa.Column("actor_source", sa.Text(), nullable=False),
        sa.Column("actor_user_id", _UUID, nullable=True),
        sa.Column("at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("kind IN ('created','rescheduled','cancelled')", name="ck_booking_log_kind"),
    )


def downgrade() -> None:
    op.drop_table("booking_change_log")
    op.drop_table("booking")  # drops ex_booking_no_overlap + indexes with it
    op.execute("DROP EXTENSION IF EXISTS btree_gist")
```

- [ ] **Step 5: `event_scheduling/booking/__init__.py`** (empty) + **`event_scheduling/booking/dto.py`**

```python
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class CreateBookingDTO:
    event_type_id: UUID
    client_user_id: UUID
    start_time: datetime
    attendee_time_zone: str


@dataclass(frozen=True)
class BookingDTO:
    id: UUID
    event_type_id: UUID
    host_user_id: UUID
    client_user_id: UUID
    start_time: datetime
    end_time: datetime
    status: str
    attendee_time_zone: str
    created_at: datetime


@dataclass(frozen=True)
class HostStat:
    user_id: UUID
    future_count: int
    last_assigned_at: datetime | None


@dataclass(frozen=True)
class BookingChangeEntryDTO:
    kind: str
    from_start: datetime | None
    from_end: datetime | None
    to_start: datetime | None
    to_end: datetime | None
    actor_source: str
    actor_user_id: UUID | None
    at: datetime
```

- [ ] **Step 6: Run — PASS.** `uv run pytest tests/test_booking_schema.py tests/test_schema.py -v` (booking exclusion + cancelled-allowed; slice-1 schema still green). Start scratch Postgres if `initdb`/`pg_ctl` unavailable (as in prior slices).

- [ ] **Step 7: Commit**
```bash
git add event_scheduling/db/models.py alembic/versions/0002_booking.py event_scheduling/booking tests/test_booking_schema.py
git commit -m "feat(booking): migration 0002 — booking + exclusion constraint + change log + DTOs"
```

---

## Task 2: getLuckyUser host ranking (pure)

**Files:**
- Create: `event_scheduling/booking/assignment.py`
- Test: `tests/test_booking_assignment.py`

**Interfaces:**
- Consumes: `dto.HostStat`.
- Produces: `assignment.rank_hosts(stats: list[HostStat]) -> list[UUID]` (best-first: fewest future, tie-break least-recently-assigned, never-assigned first); `assignment.pick_host(stats) -> UUID | None`.

- [ ] **Step 1: Failing test `tests/test_booking_assignment.py`**

```python
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
```

- [ ] **Step 2: Run — FAIL.** `uv run pytest tests/test_booking_assignment.py -v`.

- [ ] **Step 3: `event_scheduling/booking/assignment.py`**

```python
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
```

- [ ] **Step 4: Run — PASS.** `uv run pytest tests/test_booking_assignment.py -v`.

- [ ] **Step 5: Commit**
```bash
git add event_scheduling/booking/assignment.py tests/test_booking_assignment.py
git commit -m "feat(booking): getLuckyUser host ranking (fewest-future + least-recently-assigned)"
```

---

## Task 3: booking-limit period bounds + check (pure)

**Files:**
- Create: `event_scheduling/booking/limits.py`
- Test: `tests/test_booking_limits.py`

**Interfaces:**
- Consumes: `zoneinfo`.
- Produces:
  - `limits.period_bounds_utc(start: datetime, period: str, tz: str) -> tuple[datetime, datetime]` — the day/week(ISO Mon)/month/year window (in host tz) containing `start`, as `[start_utc, end_utc)` UTC-aware. Raises `ValueError` on unknown period.
  - `limits.limit_exceeded(limit_type: str, value: int, current_count: int, current_duration_min: int, new_duration_min: int) -> bool`.

- [ ] **Step 1: Failing test `tests/test_booking_limits.py`**

```python
import datetime as dt

from event_scheduling.booking.limits import limit_exceeded, period_bounds_utc


def test_day_bounds_in_host_tz() -> None:
    # 2026-10-01 23:30 UTC is 2026-10-02 01:30 in Berlin (CEST +2) → the Berlin *day* is Oct 2.
    start = dt.datetime(2026, 10, 1, 23, 30, tzinfo=dt.UTC)
    lo, hi = period_bounds_utc(start, "day", "Europe/Berlin")
    assert lo == dt.datetime(2026, 10, 1, 22, tzinfo=dt.UTC)   # Oct 2 00:00 CEST → Oct 1 22:00Z
    assert hi == dt.datetime(2026, 10, 2, 22, tzinfo=dt.UTC)   # Oct 3 00:00 CEST → Oct 2 22:00Z


def test_week_bounds_iso_monday() -> None:
    # 2026-10-01 is a Thursday; ISO week Mon = 2026-09-28.
    start = dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC)
    lo, hi = period_bounds_utc(start, "week", "Europe/Berlin")
    assert lo == dt.datetime(2026, 9, 27, 22, tzinfo=dt.UTC)   # Mon 2026-09-28 00:00 CEST → 09-27 22:00Z
    assert hi == dt.datetime(2026, 10, 4, 22, tzinfo=dt.UTC)   # next Mon 2026-10-05 00:00 CEST


def test_month_and_year_bounds() -> None:
    start = dt.datetime(2026, 10, 15, 12, tzinfo=dt.UTC)
    mlo, mhi = period_bounds_utc(start, "month", "Europe/Berlin")
    assert mlo == dt.datetime(2026, 9, 30, 22, tzinfo=dt.UTC)  # Oct 1 00:00 CEST
    assert mhi == dt.datetime(2026, 10, 31, 23, tzinfo=dt.UTC)  # Nov 1 00:00 CET (+1, DST ended) → Oct 31 23:00Z
    ylo, yhi = period_bounds_utc(start, "year", "Europe/Berlin")
    assert ylo == dt.datetime(2025, 12, 31, 23, tzinfo=dt.UTC)  # 2026-01-01 00:00 CET
    assert yhi == dt.datetime(2026, 12, 31, 23, tzinfo=dt.UTC)  # 2027-01-01 00:00 CET


def test_limit_exceeded_count_and_duration() -> None:
    assert limit_exceeded("booking_count", 3, 3, 0, 60) is True   # already at 3
    assert limit_exceeded("booking_count", 3, 2, 0, 60) is False
    assert limit_exceeded("booking_duration", 120, 0, 90, 60) is True   # 90+60 > 120
    assert limit_exceeded("booking_duration", 120, 0, 60, 60) is False  # 60+60 == 120 ok
    assert limit_exceeded("unknown", 1, 99, 99, 99) is False
```

- [ ] **Step 2: Run — FAIL.** `uv run pytest tests/test_booking_limits.py -v`.

- [ ] **Step 3: `event_scheduling/booking/limits.py`**

```python
from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo


def _local_midnight(d: date, zone: ZoneInfo) -> datetime:
    return datetime.combine(d, time(0), tzinfo=zone)


def _day_range(local: datetime) -> tuple[date, date]:
    d = local.date()
    return d, d + timedelta(days=1)


def _week_range(local: datetime) -> tuple[date, date]:
    monday = local.date() - timedelta(days=local.date().weekday())  # ISO Monday
    return monday, monday + timedelta(days=7)


def _month_range(local: datetime) -> tuple[date, date]:
    first = local.date().replace(day=1)
    next_first = (first.replace(day=28) + timedelta(days=4)).replace(day=1)
    return first, next_first


def _year_range(local: datetime) -> tuple[date, date]:
    return date(local.year, 1, 1), date(local.year + 1, 1, 1)


_PERIODS = {"day": _day_range, "week": _week_range, "month": _month_range, "year": _year_range}


def period_bounds_utc(start: datetime, period: str, tz: str) -> tuple[datetime, datetime]:
    ranger = _PERIODS.get(period)
    if ranger is None:
        msg = f"unknown period: {period!r}"
        raise ValueError(msg)
    zone = ZoneInfo(tz)
    local = start.astimezone(zone)
    lo_date, hi_date = ranger(local)
    return _local_midnight(lo_date, zone).astimezone(UTC), _local_midnight(hi_date, zone).astimezone(UTC)


def limit_exceeded(
    limit_type: str, value: int, current_count: int, current_duration_min: int, new_duration_min: int
) -> bool:
    if limit_type == "booking_count":
        return current_count >= value
    if limit_type == "booking_duration":
        return current_duration_min + new_duration_min > value
    return False
```
> Guard clauses + a period→function mapping — no `elif`/`else`. `_month_range` uses the "day 28 + 4 days → replace day 1" trick to land on the next month robustly.

- [ ] **Step 4: Run — PASS.** `uv run pytest tests/test_booking_limits.py -v`.

- [ ] **Step 5: Commit**
```bash
git add event_scheduling/booking/limits.py tests/test_booking_limits.py
git commit -m "feat(booking): booking-limit period bounds (host tz) + exceed check (pure)"
```

---

## Task 4: read adapter + BookingBusyTimesSource (DB)

**Files:**
- Create: `event_scheduling/booking/interfaces.py`, `event_scheduling/booking/read_adapter.py`, `event_scheduling/booking/busy_source.py`
- Test: `tests/test_booking_busy_source.py`

**Interfaces:**
- Consumes: `ISqlExecutor`, `BookingLimitDTO`, `BusyInterval`, `TimeWindow`, DTOs.
- Produces:
  - `interfaces.IBookingReadAdapter`: `get(id) -> BookingDTO | None`; `list_by(host_user_id|None, client_user_id|None, from_utc|None, to_utc|None) -> list[BookingDTO]`; `history(id) -> list[BookingChangeEntryDTO]`; `limits(event_type_id) -> list[BookingLimitDTO]`; `host_stats(user_ids, now) -> list[HostStat]`; `period_counts(event_type_id, lo, hi) -> tuple[int, int]` (count, total_minutes).
  - `busy_source.BookingBusyTimesSource(sql)` with `get_busy(user_ids, window, exclude_booking_id=None) -> list[BusyInterval]` (satisfies `BusyTimesSource`).

- [ ] **Step 1: Failing test `tests/test_booking_busy_source.py`** (integration; uses `sessionmaker_fixture` from slice 2)

```python
import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.booking.busy_source import BookingBusyTimesSource
from event_scheduling.booking.read_adapter import BookingReadAdapter
from event_scheduling.interfaces.busy_times import TimeWindow


async def _seed(session, *, buf_before=15, buf_after=15):
    et, host = uuid4(), uuid4()
    await session.execute(text(
        "INSERT INTO event_type (id, slug, title, duration_minutes, buffer_before_minutes, buffer_after_minutes) "
        "VALUES (:id, :slug, 't', 60, :bb, :ba)"), {"id": et, "slug": f"et-{et}", "bb": buf_before, "ba": buf_after})
    bid = uuid4()
    await session.execute(text(
        "INSERT INTO booking (id, event_type_id, host_user_id, client_user_id, start_time, end_time, status, attendee_time_zone) "
        "VALUES (:id, :et, :h, :c, :s, :e, 'confirmed', 'Europe/Berlin')"),
        {"id": bid, "et": et, "h": host, "c": uuid4(),
         "s": dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC), "e": dt.datetime(2026, 10, 1, 13, tzinfo=dt.UTC)})
    await session.commit()
    return et, host, bid


@pytest.mark.asyncio
async def test_busy_expands_by_buffers(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et, host, _ = await _seed(s, buf_before=15, buf_after=30)
    async with sessionmaker_fixture() as s:
        busy = await BookingBusyTimesSource(SqlExecutor(s)).get_busy(
            [host], TimeWindow(dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC)))
    assert len(busy) == 1
    assert busy[0].start == dt.datetime(2026, 10, 1, 11, 45, tzinfo=dt.UTC)  # 12:00 - 15min
    assert busy[0].end == dt.datetime(2026, 10, 1, 13, 30, tzinfo=dt.UTC)    # 13:00 + 30min


@pytest.mark.asyncio
async def test_busy_excludes_given_booking(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et, host, bid = await _seed(s)
    async with sessionmaker_fixture() as s:
        busy = await BookingBusyTimesSource(SqlExecutor(s)).get_busy(
            [host], TimeWindow(dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC)),
            exclude_booking_id=bid)
    assert busy == []


@pytest.mark.asyncio
async def test_period_counts(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        et, host, _ = await _seed(s)
        count, minutes = await BookingReadAdapter(SqlExecutor(s)).period_counts(
            et, dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC))
    assert count == 1
    assert minutes == 60
```

- [ ] **Step 2: Run — FAIL.** `uv run pytest tests/test_booking_busy_source.py -v`.

- [ ] **Step 3: `event_scheduling/booking/interfaces.py`**

```python
from datetime import datetime
from typing import Protocol
from uuid import UUID

from event_scheduling.booking.dto import BookingChangeEntryDTO, BookingDTO, HostStat
from event_scheduling.dto.event_type import BookingLimitDTO


class IBookingReadAdapter(Protocol):
    async def get(self, booking_id: UUID) -> BookingDTO | None: ...
    async def list_by(
        self, host_user_id: UUID | None, client_user_id: UUID | None,
        from_utc: datetime | None, to_utc: datetime | None,
    ) -> list[BookingDTO]: ...
    async def history(self, booking_id: UUID) -> list[BookingChangeEntryDTO]: ...
    async def limits(self, event_type_id: UUID) -> list[BookingLimitDTO]: ...
    async def host_stats(self, user_ids: list[UUID], now: datetime) -> list[HostStat]: ...
    async def period_counts(self, event_type_id: UUID, lo: datetime, hi: datetime) -> tuple[int, int]: ...
```

- [ ] **Step 4: `event_scheduling/booking/busy_source.py`**

```python
from collections.abc import Sequence
from uuid import UUID

from event_scheduling.interfaces.busy_times import BusyInterval, TimeWindow
from event_scheduling.interfaces.sql import ISqlExecutor


class BookingBusyTimesSource:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def get_busy(
        self, user_ids: Sequence[UUID], window: TimeWindow, exclude_booking_id: UUID | None = None
    ) -> list[BusyInterval]:
        rows = await self._sql.fetch_all(
            """
            SELECT b.start_time - make_interval(mins => et.buffer_before_minutes) AS busy_start,
                   b.end_time   + make_interval(mins => et.buffer_after_minutes)  AS busy_end
            FROM booking b
            JOIN event_type et ON et.id = b.event_type_id
            WHERE b.host_user_id = ANY(:users)
              AND b.status = 'confirmed'
              AND tstzrange(b.start_time, b.end_time) && tstzrange(:win_lo, :win_hi)
              AND (:exclude IS NULL OR b.id <> :exclude)
            """,
            {"users": list(user_ids), "win_lo": window.start, "win_hi": window.end, "exclude": exclude_booking_id},
        )
        return [BusyInterval(r["busy_start"], r["busy_end"]) for r in rows]
```

- [ ] **Step 5: `event_scheduling/booking/read_adapter.py`**

Implement `BookingReadAdapter` with the six methods. Key SQL:
```python
_COLS = "id, event_type_id, host_user_id, client_user_id, start_time, end_time, status, attendee_time_zone, created_at"

# get
row = await self._sql.fetch_one(f"SELECT {_COLS} FROM booking WHERE id = :id", {"id": booking_id})  # noqa: S608
# list_by — exactly one of host/client filter required (validated in the router), plus optional range
# history
"SELECT kind, from_start, from_end, to_start, to_end, actor_source, actor_user_id, at "
"FROM booking_change_log WHERE booking_id = :id ORDER BY at ASC, id ASC"
# limits
"SELECT limit_type, period, value FROM booking_limit WHERE event_type_id = :et"
# host_stats — future confirmed count + last assignment per user
"""
SELECT u AS user_id,
       (SELECT count(*) FROM booking b WHERE b.host_user_id = u AND b.status='confirmed' AND b.start_time >= :now) AS future_count,
       (SELECT max(created_at) FROM booking b WHERE b.host_user_id = u AND b.status='confirmed') AS last_assigned_at
FROM unnest(CAST(:users AS uuid[])) AS u
"""
# period_counts
"""
SELECT count(*) AS c, COALESCE(sum(EXTRACT(EPOCH FROM (end_time - start_time)) / 60), 0)::int AS mins
FROM booking WHERE event_type_id = :et AND status='confirmed' AND start_time >= :lo AND start_time < :hi
"""
```
Map rows → `BookingDTO`/`BookingChangeEntryDTO`/`BookingLimitDTO`/`HostStat`; `period_counts` returns `(row["c"], row["mins"])`. Raw SQL only, `:param`, no `elif`/`else`. Expand every method fully — no `...` in committed code.

- [ ] **Step 6: Run — PASS.** `uv run pytest tests/test_booking_busy_source.py -v`.

- [ ] **Step 7: Commit**
```bash
git add event_scheduling/booking/interfaces.py event_scheduling/booking/read_adapter.py event_scheduling/booking/busy_source.py tests/test_booking_busy_source.py
git commit -m "feat(booking): read adapter (stats/limits/history) + BookingBusyTimesSource (buffers)"
```

---

## Task 5: write adapter + create flow (service) + concurrency

**Files:**
- Create: `event_scheduling/booking/write_adapter.py`, `event_scheduling/booking/service.py`
- Modify: `event_scheduling/booking/interfaces.py` (add `IBookingWriteAdapter`, `IBookingService`)
- Test: `tests/test_booking_api.py` (service-level create + double-book; file shared with Task 7)

**Interfaces:**
- Consumes: `SlotsReadAdapter`, `slots.domain` (`host_availability_intervals`, `subtract_intervals`, `to_epoch_min`), `BookingBusyTimesSource`, `IBookingReadAdapter`, `assignment`, `limits`, `Clock`, `errors`.
- Produces:
  - `IBookingWriteAdapter`: `insert(event_type_id, host_user_id, client_user_id, start, end, tz) -> BookingDTO` (raises `sqlalchemy.exc.IntegrityError` on exclusion); `append_log(booking_id, kind, from_start, from_end, to_start, to_end, actor)`; `update_times(booking_id, start, end) -> BookingDTO`; `set_cancelled(booking_id) -> BookingDTO`.
  - `IBookingService.create(dto: CreateBookingDTO, actor: ActorDTO) -> BookingDTO`.

- [ ] **Step 1: Failing test (start `tests/test_booking_api.py`)**

```python
import asyncio
import datetime as dt
from uuid import UUID, uuid4

import pytest

HDRS = {"actor-source": "api"}


def _seed_single_host_et(client, *, notice=0) -> tuple[str, str]:
    owner = str(uuid4())
    client.put(f"/api/v1/schedules/{owner}", json={
        "name": "s", "time_zone": "Europe/Berlin",
        "weekly_hours": [{"day_of_week": 4, "start_time": "09:00", "end_time": "17:00"}],  # Thursday
        "date_overrides": [],
    }, headers={"actor-source": "admin"})
    sid = client.get(f"/api/v1/schedules/{owner}").json()["schedule"]["id"]
    et = client.post("/api/v1/event-types", json={
        "slug": f"et-{uuid4().hex[:8]}", "title": "Intro", "duration_minutes": 60,
        "slot_interval_minutes": 30, "min_booking_notice_minutes": notice,
        "buffer_before_minutes": 0, "buffer_after_minutes": 0,
        "hosts": [{"user_id": owner, "schedule_id": sid}], "booking_limits": [],
    }).json()["id"]
    return et, owner


def test_create_booking_assigns_host(client) -> None:
    et, owner = _seed_single_host_et(client)
    resp = client.post("/api/v1/bookings", headers=HDRS, json={
        "event_type_id": et, "client_user_id": str(uuid4()),
        "start_time": "2026-10-01T09:00:00Z", "attendee_time_zone": "Europe/Berlin"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["host_user_id"] == owner
    assert body["status"] == "confirmed"
    assert body["start_time"] == "2026-10-01T09:00:00Z"


def test_double_book_same_slot_conflicts(client) -> None:
    et, _ = _seed_single_host_et(client)
    payload = {"event_type_id": et, "client_user_id": str(uuid4()),
               "start_time": "2026-10-01T09:00:00Z", "attendee_time_zone": "Europe/Berlin"}
    first = client.post("/api/v1/bookings", headers=HDRS, json=payload)
    second = client.post("/api/v1/bookings", headers=HDRS, json={**payload, "client_user_id": str(uuid4())})
    assert first.status_code == 201
    assert second.status_code == 409  # single host already taken → no free candidate


def test_create_unknown_event_type_404(client) -> None:
    resp = client.post("/api/v1/bookings", headers=HDRS, json={
        "event_type_id": str(uuid4()), "client_user_id": str(uuid4()),
        "start_time": "2026-10-01T09:00:00Z", "attendee_time_zone": "Europe/Berlin"})
    assert resp.status_code == 404


def test_create_past_time_422(client) -> None:
    et, _ = _seed_single_host_et(client)
    resp = client.post("/api/v1/bookings", headers=HDRS, json={
        "event_type_id": et, "client_user_id": str(uuid4()),
        "start_time": "2020-01-01T09:00:00Z", "attendee_time_zone": "Europe/Berlin"})
    assert resp.status_code == 422
```
> These are HTTP-level tests — they need the router + DI (Task 7). To keep Task 5 independently green, run them AFTER Task 7 wiring, OR (recommended) in Task 5 write the service and a thin service-level test using the DI container directly. Simplest: fold the router+DI (Task 7) BEFORE these HTTP tests, and in Task 5 test `BookingService.create` directly with real adapters over a session. Use whichever keeps Task 5's deliverable independently testable; the plan's Task 7 adds the remaining endpoints. If you test the service directly here, construct it as: `BookingService(SlotsReadAdapter(sql), BookingReadAdapter(sql), BookingWriteAdapter(sql), BookingBusyTimesSource(sql), FixedClock(now))` and assert the returned `BookingDTO`.

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: `IBookingWriteAdapter` (in `interfaces.py`) + `event_scheduling/booking/write_adapter.py`**

```python
from datetime import datetime
from uuid import UUID

from event_scheduling.booking.dto import BookingDTO
from event_scheduling.dto.schedule import ActorDTO
from event_scheduling.interfaces.sql import ISqlExecutor

_COLS = "id, event_type_id, host_user_id, client_user_id, start_time, end_time, status, attendee_time_zone, created_at"


def _row_to_dto(r) -> BookingDTO:  # noqa: ANN001
    return BookingDTO(r["id"], r["event_type_id"], r["host_user_id"], r["client_user_id"],
                      r["start_time"], r["end_time"], r["status"], r["attendee_time_zone"], r["created_at"])


class BookingWriteAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def insert(self, event_type_id, host_user_id, client_user_id, start, end, tz) -> BookingDTO:  # noqa: ANN001
        row = await self._sql.fetch_one(
            f"""
            INSERT INTO booking (event_type_id, host_user_id, client_user_id, start_time, end_time, attendee_time_zone)
            VALUES (:et, :h, :c, :s, :e, :tz) RETURNING {_COLS}
            """,  # noqa: S608
            {"et": event_type_id, "h": host_user_id, "c": client_user_id, "s": start, "e": end, "tz": tz},
        )
        return _row_to_dto(row)

    async def update_times(self, booking_id, start, end) -> BookingDTO:  # noqa: ANN001
        row = await self._sql.fetch_one(
            f"UPDATE booking SET start_time=:s, end_time=:e, updated_at=now() WHERE id=:id RETURNING {_COLS}",  # noqa: S608
            {"id": booking_id, "s": start, "e": end})
        return _row_to_dto(row)

    async def set_cancelled(self, booking_id) -> BookingDTO:  # noqa: ANN001
        row = await self._sql.fetch_one(
            f"UPDATE booking SET status='cancelled', updated_at=now() WHERE id=:id RETURNING {_COLS}",  # noqa: S608
            {"id": booking_id})
        return _row_to_dto(row)

    async def append_log(self, booking_id, kind, from_start, from_end, to_start, to_end, actor: ActorDTO) -> None:  # noqa: ANN001, PLR0913
        await self._sql.execute(
            """
            INSERT INTO booking_change_log (booking_id, kind, from_start, from_end, to_start, to_end, actor_source, actor_user_id)
            VALUES (:b, :k, :fs, :fe, :ts, :te, :src, :uid)
            """,
            {"b": booking_id, "k": kind, "fs": from_start, "fe": from_end, "ts": to_start, "te": to_end,
             "src": actor.source, "uid": actor.user_id},
        )
```
(Add matching signatures to `IBookingWriteAdapter` in `interfaces.py`.)

- [ ] **Step 4: `event_scheduling/booking/service.py`** — `create` flow (§3)

```python
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from event_scheduling.booking.assignment import rank_hosts
from event_scheduling.booking.dto import BookingDTO, CreateBookingDTO
from event_scheduling.booking.limits import limit_exceeded, period_bounds_utc
from event_scheduling.dto.schedule import ActorDTO
from event_scheduling.errors import ConflictError, NotFoundError, ValidationError
from event_scheduling.interfaces.busy_times import TimeWindow
from event_scheduling.slots.domain import host_availability_intervals, subtract_intervals, to_epoch_min
from event_scheduling.slots.dto import HostSchedule, Interval
from event_scheduling.validation import validate_time_zone


class BookingService:
    def __init__(self, slots_read, read, write, busy, clock) -> None:  # noqa: ANN001, PLR0913
        self._slots = slots_read
        self._read = read
        self._write = write
        self._busy = busy
        self._clock = clock

    async def _free_host(self, host: HostSchedule, start: datetime, end: datetime,
                         notice_min: int, now: datetime, exclude_booking_id: UUID | None) -> bool:
        if start < now + timedelta(minutes=notice_min):
            return False
        window = TimeWindow(start, end)
        avail = host_availability_intervals(host, start, end)
        busy = await self._busy.get_busy([host.user_id], window, exclude_booking_id=exclude_booking_id)
        busy_iv = [Interval(to_epoch_min(b.start), to_epoch_min(b.end)) for b in busy]
        free = subtract_intervals(avail, busy_iv)
        need = Interval(to_epoch_min(start), to_epoch_min(end))
        return any(iv.start <= need.start and need.end <= iv.end for iv in free)

    async def create(self, dto: CreateBookingDTO, actor: ActorDTO) -> BookingDTO:
        validate_time_zone(dto.attendee_time_zone)
        now = self._clock.now()
        start = dto.start_time.astimezone(UTC)
        if start < now:
            raise ValidationError("start_time is in the past")
        bundle = await self._slots.load(dto.event_type_id)
        if bundle is None:
            raise NotFoundError(f"event_type {dto.event_type_id} not found")
        cfg = bundle.event_type
        end = start + timedelta(minutes=cfg.duration_minutes)

        free_hosts = [h for h in bundle.hosts
                      if await self._free_host(h, start, end, cfg.min_booking_notice_minutes, now, None)]
        if not free_hosts:
            raise ConflictError("no host available for the requested slot")

        stats = await self._read.host_stats([h.user_id for h in free_hosts], now)
        ranked = rank_hosts(stats)

        await self._enforce_limits(dto.event_type_id, ranked[0], start, cfg.duration_minutes, bundle.hosts)

        for host_id in ranked:
            try:
                booking = await self._write.insert(dto.event_type_id, host_id, dto.client_user_id, start, end,
                                                   dto.attendee_time_zone)
            except IntegrityError:
                continue
            await self._write.append_log(booking.id, "created", None, None, start, end, actor)
            return booking
        raise ConflictError("slot was taken concurrently")

    async def _enforce_limits(self, event_type_id: UUID, host_id: UUID, start: datetime,
                              duration_min: int, hosts: list[HostSchedule]) -> None:
        limits = await self._read.limits(event_type_id)
        if not limits:
            return
        host_tz = next(h.time_zone for h in hosts if h.user_id == host_id)
        for lim in limits:
            lo, hi = period_bounds_utc(start, lim.period, host_tz)
            count, minutes = await self._read.period_counts(event_type_id, lo, hi)
            if limit_exceeded(lim.limit_type, lim.value, count, minutes, duration_min):
                raise ConflictError(f"booking_limit exceeded: {lim.limit_type}/{lim.period}")
```
> Note: `_free_host` uses a `for`-comprehension with `await` — write it as an explicit loop if the list-comprehension-with-await trips ruff; behaviour identical. The exclusion `IntegrityError` drives the retry over `ranked`.

- [ ] **Step 5: Run — PASS** (service create + double-book + 404 + past-time). If testing HTTP here, do Task 7 wiring first; otherwise test `BookingService.create` directly.

- [ ] **Step 6: Commit**
```bash
git add event_scheduling/booking/write_adapter.py event_scheduling/booking/service.py event_scheduling/booking/interfaces.py tests/test_booking_api.py
git commit -m "feat(booking): create flow — availability re-check + getLuckyUser + optimistic insert/retry + limits"
```

---

## Task 6: cancel + reschedule (service)

**Files:**
- Modify: `event_scheduling/booking/service.py`, `event_scheduling/booking/interfaces.py`
- Test: `tests/test_booking_api.py` (extend)

**Interfaces:**
- Produces: `IBookingService.cancel(booking_id, actor) -> BookingDTO`; `IBookingService.reschedule(booking_id, new_start, actor) -> BookingDTO`; `IBookingService.get(id)`, `.list_by(...)`, `.history(id)` delegating to the read adapter.

- [ ] **Step 1: Failing tests (extend `tests/test_booking_api.py`)**

```python
def test_cancel_frees_slot_and_is_idempotent(client) -> None:
    et, _ = _seed_single_host_et(client)
    payload = {"event_type_id": et, "client_user_id": str(uuid4()),
               "start_time": "2026-10-01T09:00:00Z", "attendee_time_zone": "Europe/Berlin"}
    bid = client.post("/api/v1/bookings", headers=HDRS, json=payload).json()["id"]
    assert client.post(f"/api/v1/bookings/{bid}/cancel", headers=HDRS).status_code == 200
    assert client.post(f"/api/v1/bookings/{bid}/cancel", headers=HDRS).status_code == 200  # idempotent
    # slot is free again → re-book succeeds
    assert client.post("/api/v1/bookings", headers=HDRS, json={**payload, "client_user_id": str(uuid4())}).status_code == 201


def test_reschedule_same_host_to_free_slot(client) -> None:
    et, owner = _seed_single_host_et(client)
    bid = client.post("/api/v1/bookings", headers=HDRS, json={
        "event_type_id": et, "client_user_id": str(uuid4()),
        "start_time": "2026-10-01T09:00:00Z", "attendee_time_zone": "Europe/Berlin"}).json()["id"]
    resp = client.post(f"/api/v1/bookings/{bid}/reschedule", headers=HDRS, json={"start_time": "2026-10-01T11:00:00Z"})
    assert resp.status_code == 200
    assert resp.json()["start_time"] == "2026-10-01T11:00:00Z"
    assert resp.json()["host_user_id"] == owner  # same host


def test_history_chain(client) -> None:
    et, _ = _seed_single_host_et(client)
    bid = client.post("/api/v1/bookings", headers=HDRS, json={
        "event_type_id": et, "client_user_id": str(uuid4()),
        "start_time": "2026-10-01T09:00:00Z", "attendee_time_zone": "Europe/Berlin"}).json()["id"]
    client.post(f"/api/v1/bookings/{bid}/reschedule", headers=HDRS, json={"start_time": "2026-10-01T11:00:00Z"})
    client.post(f"/api/v1/bookings/{bid}/cancel", headers=HDRS)
    entries = client.get(f"/api/v1/bookings/{bid}/history").json()["entries"]
    assert [e["kind"] for e in entries] == ["created", "rescheduled", "cancelled"]
    assert entries[1]["from_start"] == "2026-10-01T09:00:00Z"
    assert entries[1]["to_start"] == "2026-10-01T11:00:00Z"
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Add `cancel`/`reschedule`/`get`/`list_by`/`history` to `BookingService`**

```python
    async def get(self, booking_id: UUID) -> BookingDTO:
        booking = await self._read.get(booking_id)
        if booking is None:
            raise NotFoundError(f"booking {booking_id} not found")
        return booking

    async def cancel(self, booking_id: UUID, actor: ActorDTO) -> BookingDTO:
        booking = await self.get(booking_id)
        if booking.status == "cancelled":
            return booking  # idempotent, no second log row
        cancelled = await self._write.set_cancelled(booking_id)
        await self._write.append_log(booking_id, "cancelled", booking.start_time, booking.end_time, None, None, actor)
        return cancelled

    async def reschedule(self, booking_id: UUID, new_start: datetime, actor: ActorDTO) -> BookingDTO:
        booking = await self.get(booking_id)
        if booking.status == "cancelled":
            raise ConflictError("cannot reschedule a cancelled booking")
        now = self._clock.now()
        start = new_start.astimezone(UTC)
        if start < now:
            raise ValidationError("start_time is in the past")
        bundle = await self._slots.load(booking.event_type_id)
        if bundle is None:
            raise NotFoundError(f"event_type {booking.event_type_id} not found")
        cfg = bundle.event_type
        end = start + timedelta(minutes=cfg.duration_minutes)
        host = next((h for h in bundle.hosts if h.user_id == booking.host_user_id), None)
        if host is None:
            raise ConflictError("assigned host is no longer on this event type")
        if not await self._free_host(host, start, end, cfg.min_booking_notice_minutes, now, booking_id):
            raise ConflictError("host is not available at the new time")
        updated = await self._write.update_times(booking_id, start, end)
        await self._write.append_log(booking_id, "rescheduled", booking.start_time, booking.end_time, start, end, actor)
        return updated

    async def list_by(self, host_user_id, client_user_id, from_utc, to_utc):  # noqa: ANN001, ANN201
        return await self._read.list_by(host_user_id, client_user_id, from_utc, to_utc)

    async def history(self, booking_id: UUID):  # noqa: ANN201
        return await self._read.history(booking_id)
```
(Add the new methods to `IBookingService` in `interfaces.py`.)

- [ ] **Step 4: Run — PASS** (cancel idempotent + frees slot; reschedule same host; history chain). Requires Task 7 wiring if run over HTTP.

- [ ] **Step 5: Commit**
```bash
git add event_scheduling/booking/service.py event_scheduling/booking/interfaces.py tests/test_booking_api.py
git commit -m "feat(booking): cancel (soft) + reschedule (in-place, same host) + get/list/history"
```

---

## Task 7: router + schemas + DI wiring + main + conftest

**Files:**
- Create: `event_scheduling/routers/booking.py`, `event_scheduling/schemas/booking.py`
- Modify: `event_scheduling/ioc.py`, `event_scheduling/main.py`, `tests/conftest.py`
- Test: `tests/test_booking_api.py` (all HTTP tests from Tasks 5–6 now run through the app) + a slots-excludes-booked test

**Interfaces:**
- Consumes: `IBookingService`.
- Produces: endpoints from spec §6; DI providers; `BookingBusyTimesSource` swapped in for `BusyTimesSource` (REQUEST scope).

- [ ] **Step 1: Failing test — slots now exclude a booked slot (add to `tests/test_booking_api.py`)**

```python
def test_booking_removes_slot_from_slots_endpoint(client) -> None:
    et, _ = _seed_single_host_et(client)
    before = client.get("/api/v1/slots", params={
        "event_type_id": et, "start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z",
        "time_zone": "Europe/Berlin"}).json()["slots"]
    assert "2026-10-01T07:00:00Z" in before["2026-10-01"]  # 09:00 CEST
    client.post("/api/v1/bookings", headers=HDRS, json={
        "event_type_id": et, "client_user_id": str(uuid4()),
        "start_time": "2026-10-01T07:00:00Z", "attendee_time_zone": "Europe/Berlin"})
    after = client.get("/api/v1/slots", params={
        "event_type_id": et, "start": "2026-10-01T00:00:00Z", "end": "2026-10-02T00:00:00Z",
        "time_zone": "Europe/Berlin"}).json()["slots"]
    assert "2026-10-01T07:00:00Z" not in after.get("2026-10-01", [])  # now busy → gone
```

- [ ] **Step 2: Run — FAIL** (booking routes 404 / slots still shows the slot because busy source is the stub).

- [ ] **Step 3: `event_scheduling/schemas/booking.py`** (Pydantic req/resp)

```python
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from event_scheduling.booking.dto import BookingDTO


class CreateBookingRequest(BaseModel):
    event_type_id: UUID
    client_user_id: UUID
    start_time: datetime
    attendee_time_zone: str


class RescheduleRequest(BaseModel):
    start_time: datetime


class BookingResponse(BaseModel):
    id: UUID
    event_type_id: UUID
    host_user_id: UUID
    client_user_id: UUID
    start_time: datetime
    end_time: datetime
    status: str
    attendee_time_zone: str
    created_at: datetime

    @classmethod
    def from_dto(cls, b: BookingDTO) -> "BookingResponse":
        return cls(**b.__dict__)


class BookingListResponse(BaseModel):
    bookings: list[BookingResponse]


class ChangeEntryModel(BaseModel):
    kind: str
    from_start: datetime | None
    from_end: datetime | None
    to_start: datetime | None
    to_end: datetime | None
    actor_source: str
    actor_user_id: UUID | None
    at: datetime


class BookingHistoryResponse(BaseModel):
    entries: list[ChangeEntryModel]
```
> Response times serialize as UTC ISO. Ensure the app emits `...Z` (FastAPI serializes aware datetimes with offset `+00:00`; the tests compare `2026-10-01T09:00:00Z`). To match, add a field serializer that formats UTC as `...Z`, OR assert with `+00:00` in tests. Pick one and keep tests + serializer consistent — the plan's tests use `...Z`, so add a `@field_serializer` on the datetime fields converting to `strftime("%Y-%m-%dT%H:%M:%SZ")`.

- [ ] **Step 4: `event_scheduling/routers/booking.py`**

```python
from datetime import datetime
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, Header, status

from event_scheduling.auth import require_api_key
from event_scheduling.booking.dto import CreateBookingDTO
from event_scheduling.booking.interfaces import IBookingService
from event_scheduling.dto.schedule import ActorDTO
from event_scheduling.errors import ValidationError
from event_scheduling.schemas.booking import (
    BookingHistoryResponse, BookingListResponse, BookingResponse, ChangeEntryModel,
    CreateBookingRequest, RescheduleRequest,
)

booking_router = APIRouter(prefix="/api/v1/bookings", tags=["bookings"],
                           route_class=DishkaRoute, dependencies=[Depends(require_api_key)])


def _actor(source: str, uid: UUID | None) -> ActorDTO:
    return ActorDTO(source=source, user_id=uid)


@booking_router.post("", response_model=BookingResponse, status_code=status.HTTP_201_CREATED)
async def create_booking(body: CreateBookingRequest, service: FromDishka[IBookingService],
                         actor_source: str = Header(default="api"),
                         actor_user_id: UUID | None = Header(default=None)) -> BookingResponse:
    dto = CreateBookingDTO(body.event_type_id, body.client_user_id, body.start_time, body.attendee_time_zone)
    return BookingResponse.from_dto(await service.create(dto, _actor(actor_source, actor_user_id)))


@booking_router.get("", response_model=BookingListResponse)
async def list_bookings(service: FromDishka[IBookingService],
                        host_user_id: UUID | None = None, client_user_id: UUID | None = None,
                        from_: datetime | None = None, to: datetime | None = None) -> BookingListResponse:
    if (host_user_id is None) == (client_user_id is None):
        raise ValidationError("exactly one of host_user_id or client_user_id is required")
    rows = await service.list_by(host_user_id, client_user_id, from_, to)
    return BookingListResponse(bookings=[BookingResponse.from_dto(b) for b in rows])


@booking_router.get("/{booking_id}", response_model=BookingResponse)
async def get_booking(booking_id: UUID, service: FromDishka[IBookingService]) -> BookingResponse:
    return BookingResponse.from_dto(await service.get(booking_id))


@booking_router.post("/{booking_id}/cancel", response_model=BookingResponse)
async def cancel_booking(booking_id: UUID, service: FromDishka[IBookingService],
                         actor_source: str = Header(default="api"),
                         actor_user_id: UUID | None = Header(default=None)) -> BookingResponse:
    return BookingResponse.from_dto(await service.cancel(booking_id, _actor(actor_source, actor_user_id)))


@booking_router.post("/{booking_id}/reschedule", response_model=BookingResponse)
async def reschedule_booking(booking_id: UUID, body: RescheduleRequest, service: FromDishka[IBookingService],
                             actor_source: str = Header(default="api"),
                             actor_user_id: UUID | None = Header(default=None)) -> BookingResponse:
    return BookingResponse.from_dto(await service.reschedule(booking_id, body.start_time, _actor(actor_source, actor_user_id)))


@booking_router.get("/{booking_id}/history", response_model=BookingHistoryResponse)
async def booking_history(booking_id: UUID, service: FromDishka[IBookingService]) -> BookingHistoryResponse:
    entries = await service.history(booking_id)
    return BookingHistoryResponse(entries=[ChangeEntryModel(**e.__dict__) for e in entries])
```
> FastAPI reserves `from` — use the query alias `from_` (the test passes `params={"from": ...}` only if you add `alias="from"`; the plan's tests don't filter by range, so the bare `from_`/`to` names are fine. If you later want `?from=`, add `Query(alias="from")`).

- [ ] **Step 5: DI wiring in `event_scheduling/ioc.py`** — swap busy source + add booking providers

```python
from event_scheduling.booking.busy_source import BookingBusyTimesSource
from event_scheduling.booking.read_adapter import BookingReadAdapter
from event_scheduling.booking.service import BookingService
from event_scheduling.booking.write_adapter import BookingWriteAdapter
from event_scheduling.booking.interfaces import IBookingReadAdapter, IBookingWriteAdapter, IBookingService
from event_scheduling.interfaces.busy_times import BusyTimesSource

    # REPLACE the APP-scope StubBusyTimesSource provider with a REQUEST-scope real one:
    @provide(scope=Scope.REQUEST)
    def provide_busy_source(self, sql: ISqlExecutor) -> BusyTimesSource:
        return BookingBusyTimesSource(sql)

    @provide(scope=Scope.REQUEST)
    def provide_booking_read(self, sql: ISqlExecutor) -> IBookingReadAdapter:
        return BookingReadAdapter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_booking_write(self, sql: ISqlExecutor) -> IBookingWriteAdapter:
        return BookingWriteAdapter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_booking_service(
        self, slots_read: ISlotsReadAdapter, read: IBookingReadAdapter,
        write: IBookingWriteAdapter, busy: BusyTimesSource, clock: Clock,
    ) -> IBookingService:
        return BookingService(slots_read, read, write, busy, clock)
```
> IMPORTANT: delete the old APP-scope `provide_busy_source` returning `StubBusyTimesSource`. The `SlotService` provider already depends on `BusyTimesSource`; moving it to REQUEST scope is compatible (REQUEST may depend on REQUEST). `Clock` stays APP.

- [ ] **Step 6: Include the router in `main.py` AND the test `app` fixture in `conftest.py`.**
`main.py`: `from event_scheduling.routers.booking import booking_router` + `app.include_router(booking_router)`.
`tests/conftest.py`: in the `app` fixture, `application.include_router(booking_router)` (import like the others). Without this every booking test 404s.

- [ ] **Step 7: Run — PASS.** `uv run pytest tests/test_booking_api.py -v` (all create/cancel/reschedule/history/slots-exclusion). Then FULL suite `uv run pytest` (slices 1–2 stay green — the busy-source swap must not break existing slots tests: those seed no bookings, so `BookingBusyTimesSource` returns `[]`, identical to the stub). Start scratch Postgres if needed.

- [ ] **Step 8: Commit**
```bash
git add event_scheduling/routers/booking.py event_scheduling/schemas/booking.py event_scheduling/ioc.py event_scheduling/main.py tests/conftest.py tests/test_booking_api.py
git commit -m "feat(booking): /api/v1/bookings endpoints + DI (real BusyTimesSource) + integration"
```

---

## Task 8: buffer/limit end-to-end tests + docs + final checks

**Files:**
- Test: `tests/test_booking_api.py` (extend — buffer + limit e2e)
- Modify: `event-scheduling/CLAUDE.md`, `event-scheduling/docs/{DATA_MODEL,API_CONTRACTS,SERVICE_OVERVIEW}.md`, root `docs/architecture/ARCHITECTURE.md`

- [ ] **Step 1: Buffer + limit end-to-end tests**

```python
def _seed_et_with(client, *, buffers=(0, 0), limits=None, notice=0) -> tuple[str, str]:
    owner = str(uuid4())
    client.put(f"/api/v1/schedules/{owner}", json={
        "name": "s", "time_zone": "Europe/Berlin",
        "weekly_hours": [{"day_of_week": 4, "start_time": "09:00", "end_time": "17:00"}], "date_overrides": []},
        headers={"actor-source": "admin"})
    sid = client.get(f"/api/v1/schedules/{owner}").json()["schedule"]["id"]
    et = client.post("/api/v1/event-types", json={
        "slug": f"et-{uuid4().hex[:8]}", "title": "t", "duration_minutes": 60, "slot_interval_minutes": 30,
        "min_booking_notice_minutes": notice, "buffer_before_minutes": buffers[0], "buffer_after_minutes": buffers[1],
        "hosts": [{"user_id": owner, "schedule_id": sid}], "booking_limits": limits or []}).json()["id"]
    return et, owner


def test_buffer_blocks_adjacent_slot(client) -> None:
    et, _ = _seed_et_with(client, buffers=(0, 30))  # 30-min after-buffer
    client.post("/api/v1/bookings", headers=HDRS, json={"event_type_id": et, "client_user_id": str(uuid4()),
        "start_time": "2026-10-01T07:00:00Z", "attendee_time_zone": "Europe/Berlin"})  # 09:00-10:00 CEST
    # 10:00 CEST (08:00Z) would start within the 30-min after-buffer of the 10:00 end → not offered
    slots = client.get("/api/v1/slots", params={"event_type_id": et, "start": "2026-10-01T00:00:00Z",
        "end": "2026-10-02T00:00:00Z", "time_zone": "Europe/Berlin"}).json()["slots"]["2026-10-01"]
    assert "2026-10-01T08:00:00Z" not in slots       # blocked by buffer
    assert "2026-10-01T08:30:00Z" in slots           # 10:30 CEST is clear


def test_booking_count_limit_enforced(client) -> None:
    et, _ = _seed_et_with(client, limits=[{"limit_type": "booking_count", "period": "day", "value": 1}])
    first = client.post("/api/v1/bookings", headers=HDRS, json={"event_type_id": et, "client_user_id": str(uuid4()),
        "start_time": "2026-10-01T07:00:00Z", "attendee_time_zone": "Europe/Berlin"})
    second = client.post("/api/v1/bookings", headers=HDRS, json={"event_type_id": et, "client_user_id": str(uuid4()),
        "start_time": "2026-10-01T08:00:00Z", "attendee_time_zone": "Europe/Berlin"})
    assert first.status_code == 201
    assert second.status_code == 409  # day limit of 1 reached
```

- [ ] **Step 2: Run — PASS.** `uv run pytest tests/test_booking_api.py -k "buffer or limit" -v`.

- [ ] **Step 3: Commit tests**
```bash
git add tests/test_booking_api.py
git commit -m "test(booking): buffer-blocks-adjacent + booking_count limit e2e"
```

- [ ] **Step 4: Docs.** `CLAUDE.md`: add `booking/` module + booking endpoints + note the busy source is now real (`BookingBusyTimesSource`, buffers applied; slots exclude confirmed bookings). `docs/DATA_MODEL.md`: `booking` + `booking_change_log` (2 new tables → "10 tables", exclusion constraint explained). `docs/API_CONTRACTS.md`: booking endpoints. `docs/SERVICE_OVERVIEW.md`: slice-3 maturity — real busy/buffer/limit enforcement now active; still no RabbitMQ/CloudEvents (slice 4). Root `docs/architecture/ARCHITECTURE.md`: slice 3 delivered; note booking write-side is HTTP-only, pipeline integration is slice 4.

- [ ] **Step 5: Full test + lint + smoke.** `cd event-scheduling && uv run pytest && ruff check . && ruff format --check .` — green. Compose smoke (best-effort): seed schedule+event-type, `POST /api/v1/bookings`, then `GET /api/v1/slots` and confirm the booked slot is gone; `POST .../cancel` and confirm it returns.

- [ ] **Step 6: Commit**
```bash
git add event-scheduling/CLAUDE.md event-scheduling/docs docs/architecture/ARCHITECTURE.md
git commit -m "docs(booking): document booking write-side (slice 3) + real busy/buffer/limit"
```

---

## Self-Review (проведён при написании плана)

**1. Покрытие спека:**
- §2 схема (booking + change_log + exclusion + btree_gist) → Task 1.
- §3 flow создания (пере-валидация → getLuckyUser → лимит → optimistic insert/retry → log) → Task 5 (+ assignment Task 2, limits Task 3, busy Task 4).
- §4 cancel/reschedule (soft, in-place same-host, exclude_booking_id) → Task 6.
- §5 BookingBusyTimesSource (буферы, exclude) + лимиты (период в tz хоста) → Task 4 + Task 3, применены в Task 5.
- §6 API → Task 7. §7 тесты (конкурентность→Task1 DB-level + Task5 double-book; getLuckyUser→Task2; лимиты→Task3+Task8; буферы→Task8; cancel/reschedule/history→Task6) . §9 DoR → Task 8.
- DI swap Stub→Booking (REQUEST) → Task 7.

**2. Плейсхолдеры:** код в шагах полный. Task 4 Step 5 (read_adapter) и Task 5 помечены «раскрыть полностью без `...`» — это указания развернуть по образцу, не заглушки; SQL приведён. Task 5/6 HTTP-тесты помечены «требуют Task 7 wiring» с явным указанием, как тестировать сервис напрямую для независимой проверяемости.

**3. Согласованность типов:** `HostStat`/`BookingDTO`/`CreateBookingDTO`/`BookingChangeEntryDTO` (Task 1) → 2/4/5/6. `rank_hosts` (Task 2), `period_bounds_utc`/`limit_exceeded` (Task 3), `IBookingReadAdapter`/`BookingBusyTimesSource.get_busy(...,exclude_booking_id)` (Task 4) → Task 5/6. `IBookingWriteAdapter` (Task 5) → Task 6/7. `IBookingService` (Task 5/6) → router Task 7. Reuse `SlotsReadAdapter.load`, `slots.domain.*`, `Clock`, `ActorDTO`, `BookingLimitDTO`, `BusyTimesSource`/`TimeWindow`/`BusyInterval` — сигнатуры совпадают с реализованными в срезах 1–2.
