# event-scheduling — движок расчёта слотов (срез 2) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Добавить в сервис `event-scheduling` read-side движок расчёта доступных слотов: `GET /api/v1/slots` считает round-robin-доступность хостов события над окном дат и отдаёт UTC-слоты, сгруппированные по локальной дате визитёра.

**Architecture:** Изолированный модуль `event_scheduling/slots/` с чистым ядром (интервальная математика + tz/DST, нулевой IO) и тонкой IO-оболочкой (batch read-adapter + сервис-оркестратор + роутер). Пайплайн: batch-load event_type+hosts+расписания → per-host UTC-диапазоны доступности (weekly/override/travel-tz, DST) → вычитание занятости из `BusyTimesSource` (в срезе 2 пусто) → round-robin UNION по хостам → нарезка на слоты (min_notice) → группировка по локальной дате.

**Tech Stack:** Python 3.14, FastAPI, Dishka, SQLAlchemy async (raw SQL via `SqlExecutor`), `zoneinfo`, pytest.

**Spec:** `docs/superpowers/specs/2026-07-05-event-scheduling-slot-engine-design.md`
**Target service (уже в main):** `event-scheduling/` — модель расписаний среза 1 (8 таблиц). Существующие DTO переиспользуем из `event_scheduling/dto/schedule.py`.

## Global Constraints

- **Python `>=3.14`**; deps через `uv`; работать из `event-scheduling/`.
- **Ruff** line-length 120, target py314; **NO `elif`; avoid `else`** — guard clauses / early returns / mapping dicts.
- **Raw SQL только** через `SqlExecutor` (`:param` плейсхолдеры). DTO — frozen dataclasses; Pydantic только в `schemas/`.
- Движок **read-only**; транзакции не мутируют.
- **Контракт в UTC**: слоты — UTC ISO (`...Z`); `time_zone` визитёра — только для группировки ответа.
- **`now` инжектируется** (Clock), не `datetime.now()` внутри доменных функций — детерминизм тестов.
- **Идентификатор события — `event_type_id` (uuid)**. Окно `start`/`end` — UTC ISO instants. Cap окна **62 дня** → `422`.
- Занятость через `BusyTimesSource` (в срезе 2 — `StubBusyTimesSource → []`); буферы и `booking_limit` **проплюблены, но инертны** до среза 3.
- Все внутренние вычисления в доменном слое — в **epoch-минутах** (`int`), datetime только на границах.
- Ветка реализации: `feat/event-scheduling-slot-engine` (спек уже закоммичен там).

---

## File Structure

Всё под `event-scheduling/`:

```
event_scheduling/slots/
├── __init__.py
├── dto.py            # EventTypeConfig, HostSchedule, Interval, SlotBundle (frozen)
├── timezones.py      # effective_time_zone, local_interval_to_utc, group_slots_by_local_date (pure, zoneinfo)
├── domain.py         # epoch helpers + merge/subtract/slice + host_availability_intervals (pure)
├── read_adapter.py   # SlotsReadAdapter — batch SQL → SlotBundle
├── service.py        # Clock/SystemClock + SlotService (orchestration)
└── interfaces.py     # ISlotsReadAdapter, ISlotService, Clock
event_scheduling/routers/slots.py    # GET /api/v1/slots
event_scheduling/schemas/slots.py    # SlotsResponse
event_scheduling/ioc.py              # + providers (modify)
event_scheduling/main.py             # + include slots_router (modify)
tests/conftest.py                    # + include slots_router in app fixture (modify)
tests/test_slots_timezones.py
tests/test_slots_domain.py
tests/test_slots_service.py
tests/test_slots_api.py
```

Reuse from slice 1: `event_scheduling/dto/schedule.py` — `WeeklyHourDTO(day_of_week, start_time, end_time)`, `DateOverrideDTO(date, start_time, end_time)`, `TravelDTO(time_zone, start_date, end_date, prev_time_zone)`. `event_scheduling/errors.py` — `ValidationError`(422), `NotFoundError`(404). `event_scheduling/validation.py` — `validate_time_zone`. `event_scheduling/interfaces/busy_times.py` — `BusyTimesSource`, `TimeWindow`, `BusyInterval`, `StubBusyTimesSource`.

---

## Task 1: slots DTOs + timezone helpers (pure)

**Files:**
- Create: `event_scheduling/slots/__init__.py`, `event_scheduling/slots/dto.py`, `event_scheduling/slots/timezones.py`
- Test: `tests/test_slots_timezones.py`

**Interfaces:**
- Produces:
  - `dto.EventTypeConfig(duration_minutes: int, slot_interval_minutes: int | None, min_booking_notice_minutes: int, buffer_before_minutes: int, buffer_after_minutes: int)` (frozen)
  - `dto.HostSchedule(user_id: UUID, time_zone: str, weekly_hours: list[WeeklyHourDTO], date_overrides: list[DateOverrideDTO], travels: list[TravelDTO])` (frozen)
  - `dto.Interval(start: int, end: int)` (frozen; epoch-minute bounds, `end` exclusive)
  - `dto.SlotBundle(event_type: EventTypeConfig, hosts: list[HostSchedule])` (frozen)
  - `timezones.effective_time_zone(day: date, base_tz: str, travels: Sequence[TravelDTO]) -> str`
  - `timezones.local_interval_to_utc(day: date, start: time, end: time, tz: str) -> tuple[datetime, datetime]` (UTC-aware)
  - `timezones.group_slots_by_local_date(slots_utc: Sequence[datetime], tz: str) -> dict[str, list[str]]` (local-date ISO → sorted UTC-ISO `...Z`)

- [ ] **Step 1: Failing test `tests/test_slots_timezones.py`**

```python
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
    assert before[0] == dt.datetime(2026, 3, 28, 8, tzinfo=dt.UTC)   # 09:00 CET → 08:00Z
    assert after[0] == dt.datetime(2026, 3, 30, 7, tzinfo=dt.UTC)    # 09:00 CEST → 07:00Z


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
```

- [ ] **Step 2: Run — FAIL.** `cd event-scheduling && uv run pytest tests/test_slots_timezones.py -v` → module not found.

- [ ] **Step 3: `event_scheduling/slots/__init__.py`** — empty file.

- [ ] **Step 4: `event_scheduling/slots/dto.py`**

```python
from dataclasses import dataclass
from uuid import UUID

from event_scheduling.dto.schedule import DateOverrideDTO, TravelDTO, WeeklyHourDTO


@dataclass(frozen=True)
class EventTypeConfig:
    duration_minutes: int
    slot_interval_minutes: int | None
    min_booking_notice_minutes: int
    buffer_before_minutes: int
    buffer_after_minutes: int


@dataclass(frozen=True)
class HostSchedule:
    user_id: UUID
    time_zone: str
    weekly_hours: list[WeeklyHourDTO]
    date_overrides: list[DateOverrideDTO]
    travels: list[TravelDTO]


@dataclass(frozen=True)
class Interval:
    """Half-open [start, end) in epoch minutes (UTC)."""

    start: int
    end: int


@dataclass(frozen=True)
class SlotBundle:
    event_type: EventTypeConfig
    hosts: list[HostSchedule]
```

- [ ] **Step 5: `event_scheduling/slots/timezones.py`**

```python
from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

from event_scheduling.dto.schedule import TravelDTO


def effective_time_zone(day: date, base_tz: str, travels: Sequence[TravelDTO]) -> str:
    for t in travels:
        if t.start_date <= day and (t.end_date is None or day <= t.end_date):
            return t.time_zone
    return base_tz


def local_interval_to_utc(day: date, start: time, end: time, tz: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(tz)
    start_utc = datetime.combine(day, start, tzinfo=zone).astimezone(UTC)
    end_utc = datetime.combine(day, end, tzinfo=zone).astimezone(UTC)
    return start_utc, end_utc


def group_slots_by_local_date(slots_utc: Sequence[datetime], tz: str) -> dict[str, list[str]]:
    zone = ZoneInfo(tz)
    grouped: dict[str, list[str]] = {}
    for slot in sorted(slots_utc):
        local_date = slot.astimezone(zone).date().isoformat()
        iso_z = slot.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        grouped.setdefault(local_date, []).append(iso_z)
    return grouped
```
> `effective_time_zone` iterates travels in order and returns the first match — no `elif`/`else`. `group_slots_by_local_date` sorts once; buckets preserve chronological order.

- [ ] **Step 6: Run — PASS.** `uv run pytest tests/test_slots_timezones.py -v` (no DB needed).

- [ ] **Step 7: Commit**
```bash
git add event_scheduling/slots/__init__.py event_scheduling/slots/dto.py event_scheduling/slots/timezones.py tests/test_slots_timezones.py
git commit -m "feat(slots): DTOs + timezone/DST helpers (pure)"
```

---

## Task 2: interval primitives — merge / subtract / slice (pure)

**Files:**
- Create: `event_scheduling/slots/domain.py`
- Test: `tests/test_slots_domain.py`

**Interfaces:**
- Consumes: `dto.Interval`.
- Produces:
  - `domain.to_epoch_min(d: datetime) -> int`, `domain.from_epoch_min(m: int) -> datetime` (UTC-aware)
  - `domain.merge_intervals(intervals: list[Interval]) -> list[Interval]` (sorted, overlapping/adjacent unioned)
  - `domain.subtract_intervals(base: list[Interval], busy: list[Interval]) -> list[Interval]`
  - `domain.slice_into_slots(avail: list[Interval], duration_min: int, step_min: int, not_before_min: int) -> list[int]` (sorted epoch-min slot starts; `[t, t+duration)` fits inside an interval; `t >= not_before_min`; stepping aligned to each interval's start)

- [ ] **Step 1: Failing test `tests/test_slots_domain.py`**

```python
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
```

- [ ] **Step 2: Run — FAIL.** `uv run pytest tests/test_slots_domain.py -v`.

- [ ] **Step 3: `event_scheduling/slots/domain.py`** (primitives)

```python
from datetime import UTC, datetime

from event_scheduling.slots.dto import Interval


def to_epoch_min(d: datetime) -> int:
    return int(d.timestamp()) // 60


def from_epoch_min(m: int) -> datetime:
    return datetime.fromtimestamp(m * 60, tz=UTC)


def merge_intervals(intervals: list[Interval]) -> list[Interval]:
    if not intervals:
        return []
    ordered = sorted(intervals, key=lambda i: i.start)
    merged = [ordered[0]]
    for cur in ordered[1:]:
        last = merged[-1]
        if cur.start <= last.end:
            merged[-1] = Interval(last.start, max(last.end, cur.end))
            continue
        merged.append(cur)
    return merged


def subtract_intervals(base: list[Interval], busy: list[Interval]) -> list[Interval]:
    if not busy:
        return list(base)
    busy_sorted = merge_intervals(busy)
    out: list[Interval] = []
    for b in base:
        cursor = b.start
        for x in busy_sorted:
            if x.end <= cursor or x.start >= b.end:
                continue
            if x.start > cursor:
                out.append(Interval(cursor, x.start))
            cursor = max(cursor, x.end)
        if cursor < b.end:
            out.append(Interval(cursor, b.end))
    return out


def slice_into_slots(avail: list[Interval], duration_min: int, step_min: int, not_before_min: int) -> list[int]:
    out: list[int] = []
    for iv in avail:
        t = iv.start
        while t + duration_min <= iv.end:
            if t >= not_before_min:
                out.append(t)
            t += step_min
    return out
```
> Guard clauses only, no `elif`/`else`. `subtract_intervals` sweeps merged busy per base interval.

- [ ] **Step 4: Run — PASS.** `uv run pytest tests/test_slots_domain.py -v`.

- [ ] **Step 5: Commit**
```bash
git add event_scheduling/slots/domain.py tests/test_slots_domain.py
git commit -m "feat(slots): interval primitives — merge/subtract/slice (pure)"
```

---

## Task 3: per-host availability builder (pure)

**Files:**
- Modify: `event_scheduling/slots/domain.py`
- Test: `tests/test_slots_domain.py` (extend)

**Interfaces:**
- Consumes: `dto.HostSchedule`, `dto.Interval`, `timezones.*`, `to_epoch_min`.
- Produces: `domain.host_availability_intervals(host: HostSchedule, window_start: datetime, window_end: datetime) -> list[Interval]` — this host's raw UTC availability intervals over the window (weekly hours or date-override per local day in effective tz, DST-correct, clipped to the window; a NULL/NULL override day contributes nothing).

- [ ] **Step 1: Failing test (extend `tests/test_slots_domain.py`)**

```python
from uuid import uuid4

from event_scheduling.dto.schedule import DateOverrideDTO, TravelDTO, WeeklyHourDTO
from event_scheduling.slots.domain import host_availability_intervals, to_epoch_min
from event_scheduling.slots.dto import HostSchedule


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
    assert ivs == [__import__("event_scheduling.slots.dto", fromlist=["Interval"]).Interval(start, end)]


def test_host_date_override_replaces_weekly() -> None:
    host = _host(
        weekly_hours=[WeeklyHourDTO(4, dt.time(9), dt.time(17))],
        date_overrides=[DateOverrideDTO(dt.date(2026, 10, 1), dt.time(10), dt.time(12))],
    )
    ws = dt.datetime(2026, 10, 1, 0, tzinfo=dt.UTC)
    we = dt.datetime(2026, 10, 2, 0, tzinfo=dt.UTC)
    ivs = host_availability_intervals(host, ws, we)
    from event_scheduling.slots.dto import Interval
    start = to_epoch_min(dt.datetime(2026, 10, 1, 8, tzinfo=dt.UTC))   # 10:00 CEST → 08:00Z
    end = to_epoch_min(dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC))    # 12:00 CEST → 10:00Z
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
    from event_scheduling.slots.dto import Interval
    # Almaty UTC+5 (no DST): 09:00 → 04:00Z, 17:00 → 12:00Z
    start = to_epoch_min(dt.datetime(2026, 10, 1, 4, tzinfo=dt.UTC))
    end = to_epoch_min(dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC))
    assert ivs == [Interval(start, end)]
```

- [ ] **Step 2: Run — FAIL.** `uv run pytest tests/test_slots_domain.py -k host -v`.

- [ ] **Step 3: Add `host_availability_intervals` to `event_scheduling/slots/domain.py`**

```python
from datetime import date, time, timedelta

from event_scheduling.slots.dto import HostSchedule
from event_scheduling.slots.timezones import effective_time_zone, local_interval_to_utc


def _clip(start_utc: datetime, end_utc: datetime, window_start: datetime, window_end: datetime) -> Interval | None:
    s = max(start_utc, window_start)
    e = min(end_utc, window_end)
    if s >= e:
        return None
    return Interval(to_epoch_min(s), to_epoch_min(e))


def _day_local_intervals(host: HostSchedule, day: date) -> list[tuple[time, time]]:
    overrides = [o for o in host.date_overrides if o.date == day]
    if overrides:
        return [(o.start_time, o.end_time) for o in overrides if o.start_time is not None and o.end_time is not None]
    return [(w.start_time, w.end_time) for w in host.weekly_hours if w.day_of_week == day.isoweekday()]


def host_availability_intervals(host: HostSchedule, window_start: datetime, window_end: datetime) -> list[Interval]:
    out: list[Interval] = []
    day = window_start.date() - timedelta(days=1)
    last = window_end.date() + timedelta(days=1)
    while day <= last:
        tz = effective_time_zone(day, host.time_zone, host.travels)
        for start, end in _day_local_intervals(host, day):
            start_utc, end_utc = local_interval_to_utc(day, start, end, tz)
            clipped = _clip(start_utc, end_utc, window_start, window_end)
            if clipped is not None:
                out.append(clipped)
        day += timedelta(days=1)
    return sorted(out, key=lambda i: i.start)
```
> `_day_local_intervals`: date-overrides fully replace weekly hours for that day; a NULL/NULL override row is filtered out (blocked day → no intervals). ±1 day iteration padding catches tz-offset spillover; `_clip` trims to the UTC window.

- [ ] **Step 4: Run — PASS.** `uv run pytest tests/test_slots_domain.py -v`.

- [ ] **Step 5: Commit**
```bash
git add event_scheduling/slots/domain.py tests/test_slots_domain.py
git commit -m "feat(slots): per-host availability builder (weekly/override/travel/DST)"
```

---

## Task 4: batch read adapter (DB)

**Files:**
- Create: `event_scheduling/slots/read_adapter.py`, `event_scheduling/slots/interfaces.py`
- Test: `tests/test_slots_api.py` (adapter-focused integration test; file shared with Task 6)

**Interfaces:**
- Consumes: `ISqlExecutor`, `dto.{EventTypeConfig,HostSchedule,SlotBundle}`, schedule DTOs.
- Produces:
  - `interfaces.ISlotsReadAdapter.load(event_type_id: UUID) -> SlotBundle | None`
  - `read_adapter.SlotsReadAdapter(sql: ISqlExecutor)` implementing it.

- [ ] **Step 1: Failing integration test (start `tests/test_slots_api.py`)**

```python
from uuid import UUID, uuid4

import pytest

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.slots.read_adapter import SlotsReadAdapter


HDRS = {"actor-source": "admin"}


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
```
> Add a `sessionmaker_fixture` to `tests/conftest.py` if not present: a fixture returning the `async_sessionmaker` bound to the migrated test DB (mirror how `ioc.provide_sessionmaker` builds it from the test DSN). If an equivalent already exists, use it and adjust the name.

- [ ] **Step 2: Run — FAIL.** `uv run pytest tests/test_slots_api.py -k read_adapter -v`.

- [ ] **Step 3: `event_scheduling/slots/interfaces.py`**

```python
from typing import Protocol
from uuid import UUID

from event_scheduling.slots.dto import SlotBundle


class ISlotsReadAdapter(Protocol):
    async def load(self, event_type_id: UUID) -> SlotBundle | None: ...
```

- [ ] **Step 4: `event_scheduling/slots/read_adapter.py`**

```python
from uuid import UUID

from event_scheduling.dto.schedule import DateOverrideDTO, TravelDTO, WeeklyHourDTO
from event_scheduling.interfaces.sql import ISqlExecutor
from event_scheduling.slots.dto import EventTypeConfig, HostSchedule, SlotBundle


class SlotsReadAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def load(self, event_type_id: UUID) -> SlotBundle | None:
        et = await self._sql.fetch_one(
            """
            SELECT duration_minutes, slot_interval_minutes, min_booking_notice_minutes,
                   buffer_before_minutes, buffer_after_minutes
            FROM event_type WHERE id = :id
            """,
            {"id": event_type_id},
        )
        if et is None:
            return None
        config = EventTypeConfig(
            duration_minutes=et["duration_minutes"],
            slot_interval_minutes=et["slot_interval_minutes"],
            min_booking_notice_minutes=et["min_booking_notice_minutes"],
            buffer_before_minutes=et["buffer_before_minutes"],
            buffer_after_minutes=et["buffer_after_minutes"],
        )
        host_rows = await self._sql.fetch_all(
            "SELECT user_id, schedule_id FROM host WHERE event_type_id = :id",
            {"id": event_type_id},
        )
        if not host_rows:
            return SlotBundle(event_type=config, hosts=[])
        schedule_ids = [r["schedule_id"] for r in host_rows]
        schedules = {
            r["id"]: r["time_zone"]
            for r in await self._sql.fetch_all(
                "SELECT id, time_zone FROM schedule WHERE id = ANY(:ids)", {"ids": schedule_ids}
            )
        }
        weekly = self._group(
            await self._sql.fetch_all(
                "SELECT schedule_id, day_of_week, start_time, end_time "
                "FROM weekly_hours WHERE schedule_id = ANY(:ids)",
                {"ids": schedule_ids},
            ),
            lambda r: WeeklyHourDTO(r["day_of_week"], r["start_time"], r["end_time"]),
        )
        overrides = self._group(
            await self._sql.fetch_all(
                "SELECT schedule_id, date, start_time, end_time "
                "FROM date_override WHERE schedule_id = ANY(:ids)",
                {"ids": schedule_ids},
            ),
            lambda r: DateOverrideDTO(r["date"], r["start_time"], r["end_time"]),
        )
        travels = self._group(
            await self._sql.fetch_all(
                "SELECT schedule_id, time_zone, start_date, end_date, prev_time_zone "
                "FROM travel_schedule WHERE schedule_id = ANY(:ids)",
                {"ids": schedule_ids},
            ),
            lambda r: TravelDTO(r["time_zone"], r["start_date"], r["end_date"], r["prev_time_zone"]),
        )
        hosts = [
            HostSchedule(
                user_id=r["user_id"],
                time_zone=schedules[r["schedule_id"]],
                weekly_hours=weekly.get(r["schedule_id"], []),
                date_overrides=overrides.get(r["schedule_id"], []),
                travels=travels.get(r["schedule_id"], []),
            )
            for r in host_rows
        ]
        return SlotBundle(event_type=config, hosts=hosts)

    @staticmethod
    def _group(rows, make):  # noqa: ANN001, ANN205
        grouped: dict = {}
        for r in rows:
            grouped.setdefault(r["schedule_id"], []).append(make(r))
        return grouped
```
> `= ANY(:ids)` binds a Python list → Postgres array via asyncpg. One query per child table (5 queries total) regardless of host count — no N+1.

- [ ] **Step 5: Run — PASS.** `uv run pytest tests/test_slots_api.py -k read_adapter -v`.

- [ ] **Step 6: Commit**
```bash
git add event_scheduling/slots/read_adapter.py event_scheduling/slots/interfaces.py tests/test_slots_api.py tests/conftest.py
git commit -m "feat(slots): batch read adapter (event_type + hosts + schedules)"
```

---

## Task 5: SlotService orchestration + Clock (unit)

**Files:**
- Create: `event_scheduling/slots/service.py`
- Modify: `event_scheduling/slots/interfaces.py` (add `ISlotService`, `Clock`)
- Test: `tests/test_slots_service.py`

**Interfaces:**
- Consumes: `ISlotsReadAdapter`, `BusyTimesSource`, `TimeWindow`, `BusyInterval`, domain + timezones, `NotFoundError`.
- Produces:
  - `interfaces.Clock` Protocol (`now() -> datetime`); `service.SystemClock` (returns `datetime.now(UTC)`).
  - `interfaces.ISlotService.available_slots(event_type_id: UUID, window_start: datetime, window_end: datetime, time_zone: str) -> dict[str, list[str]]`
  - `service.SlotService(read_adapter, busy_source, clock)` implementing it. Raises `NotFoundError` when the event type is missing.

- [ ] **Step 1: Failing test `tests/test_slots_service.py`**

```python
import datetime as dt
from uuid import UUID, uuid4

import pytest

from event_scheduling.dto.schedule import WeeklyHourDTO
from event_scheduling.errors import NotFoundError
from event_scheduling.interfaces.busy_times import StubBusyTimesSource
from event_scheduling.slots.dto import EventTypeConfig, HostSchedule, SlotBundle
from event_scheduling.slots.service import SlotService


class _FakeAdapter:
    def __init__(self, bundle: SlotBundle | None) -> None:
        self._bundle = bundle

    async def load(self, event_type_id: UUID) -> SlotBundle | None:
        return self._bundle


class _FixedClock:
    def __init__(self, now: dt.datetime) -> None:
        self._now = now

    def now(self) -> dt.datetime:
        return self._now


def _bundle(hosts: list[HostSchedule], *, step: int | None = 30, notice: int = 0) -> SlotBundle:
    return SlotBundle(
        event_type=EventTypeConfig(60, step, notice, 0, 0),
        hosts=hosts,
    )


def _host(tz: str = "Europe/Berlin") -> HostSchedule:
    return HostSchedule(uuid4(), tz, [WeeklyHourDTO(4, dt.time(9), dt.time(17))], [], [])


@pytest.mark.asyncio
async def test_two_hosts_union_produces_slots() -> None:
    # Two hosts, identical Thu 09:00-17:00 Berlin → union is the same window; 60-min/30-step.
    svc = SlotService(_FakeAdapter(_bundle([_host(), _host()])), StubBusyTimesSource(),
                      _FixedClock(dt.datetime(2026, 9, 1, tzinfo=dt.UTC)))
    grouped = await svc.available_slots(
        uuid4(), dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC), "Europe/Berlin"
    )
    # 09:00 CEST → 07:00Z; last fitting slot 16:00 CEST → 14:00Z (14:00+60 = 15:00 = 17:00 CEST end)
    assert grouped["2026-10-01"][0] == "2026-10-01T07:00:00Z"
    assert grouped["2026-10-01"][-1] == "2026-10-01T14:00:00Z"


@pytest.mark.asyncio
async def test_min_notice_drops_early_slots() -> None:
    # now = 2026-10-01 10:00Z, notice 120 min → earliest slot >= 12:00Z
    svc = SlotService(_FakeAdapter(_bundle([_host()], notice=120)), StubBusyTimesSource(),
                      _FixedClock(dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC)))
    grouped = await svc.available_slots(
        uuid4(), dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC), "Europe/Berlin"
    )
    assert grouped["2026-10-01"][0] == "2026-10-01T12:00:00Z"


@pytest.mark.asyncio
async def test_missing_event_type_raises_not_found() -> None:
    svc = SlotService(_FakeAdapter(None), StubBusyTimesSource(), _FixedClock(dt.datetime(2026, 1, 1, tzinfo=dt.UTC)))
    with pytest.raises(NotFoundError):
        await svc.available_slots(uuid4(), dt.datetime(2026, 10, 1, tzinfo=dt.UTC),
                                  dt.datetime(2026, 10, 2, tzinfo=dt.UTC), "Europe/Berlin")
```

- [ ] **Step 2: Run — FAIL.** `uv run pytest tests/test_slots_service.py -v`.

- [ ] **Step 3: Extend `event_scheduling/slots/interfaces.py`**

```python
from datetime import datetime


class Clock(Protocol):
    def now(self) -> datetime: ...


class ISlotService(Protocol):
    async def available_slots(
        self, event_type_id: UUID, window_start: datetime, window_end: datetime, time_zone: str
    ) -> dict[str, list[str]]: ...
```

- [ ] **Step 4: `event_scheduling/slots/service.py`**

```python
from datetime import UTC, datetime
from uuid import UUID

from event_scheduling.errors import NotFoundError
from event_scheduling.interfaces.busy_times import BusyTimesSource, TimeWindow
from event_scheduling.slots.domain import (
    from_epoch_min,
    host_availability_intervals,
    merge_intervals,
    slice_into_slots,
    subtract_intervals,
    to_epoch_min,
)
from event_scheduling.slots.dto import Interval
from event_scheduling.slots.interfaces import Clock, ISlotsReadAdapter
from event_scheduling.slots.timezones import group_slots_by_local_date


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class SlotService:
    def __init__(self, read_adapter: ISlotsReadAdapter, busy_source: BusyTimesSource, clock: Clock) -> None:
        self._read = read_adapter
        self._busy = busy_source
        self._clock = clock

    async def available_slots(
        self, event_type_id: UUID, window_start: datetime, window_end: datetime, time_zone: str
    ) -> dict[str, list[str]]:
        bundle = await self._read.load(event_type_id)
        if bundle is None:
            raise NotFoundError(f"event_type {event_type_id} not found")

        window = TimeWindow(window_start, window_end)
        free: list[Interval] = []
        for host in bundle.hosts:
            intervals = host_availability_intervals(host, window_start, window_end)
            busy = await self._busy.get_busy([host.user_id], window)
            busy_iv = [Interval(to_epoch_min(b.start), to_epoch_min(b.end)) for b in busy]
            free.extend(subtract_intervals(intervals, busy_iv))

        union = merge_intervals(free)
        cfg = bundle.event_type
        step = cfg.slot_interval_minutes or cfg.duration_minutes
        not_before = to_epoch_min(self._clock.now()) + cfg.min_booking_notice_minutes
        slot_mins = slice_into_slots(union, cfg.duration_minutes, step, not_before)
        return group_slots_by_local_date([from_epoch_min(m) for m in slot_mins], time_zone)
```
> Round-robin union = merge across all hosts' free intervals. Busy fetched per-host with a single-element `user_ids` list (stub returns `[]` in slice 2; slice 3 backs it per user).

- [ ] **Step 5: Run — PASS.** `uv run pytest tests/test_slots_service.py -v` (no DB).

- [ ] **Step 6: Commit**
```bash
git add event_scheduling/slots/service.py event_scheduling/slots/interfaces.py tests/test_slots_service.py
git commit -m "feat(slots): SlotService orchestration + injectable Clock"
```

---

## Task 6: router + schema + DI wiring + end-to-end integration

**Files:**
- Create: `event_scheduling/routers/slots.py`, `event_scheduling/schemas/slots.py`
- Modify: `event_scheduling/ioc.py`, `event_scheduling/main.py`, `tests/conftest.py`
- Test: `tests/test_slots_api.py` (extend)

**Interfaces:**
- Consumes: `ISlotService`, `validate_time_zone`, `ValidationError`.
- Produces: `GET /api/v1/slots?event_type_id&start&end&time_zone` → `200` `SlotsResponse`; `404` (unknown event type via `NotFoundError`); `422` (bad tz / `end<=start` / window > 62 days).

- [ ] **Step 1: Failing end-to-end tests (extend `tests/test_slots_api.py`)**

```python
def _slots(client, et_id: str, start: str, end: str, tz: str = "Europe/Berlin"):
    return client.get("/api/v1/slots", params={"event_type_id": et_id, "start": start, "end": end, "time_zone": tz})


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
```

- [ ] **Step 2: Run — FAIL.** `uv run pytest tests/test_slots_api.py -k slots_endpoint -v`.

- [ ] **Step 3: `event_scheduling/schemas/slots.py`**

```python
from uuid import UUID

from pydantic import BaseModel


class SlotsResponse(BaseModel):
    event_type_id: UUID
    time_zone: str
    slots: dict[str, list[str]]
```

- [ ] **Step 4: `event_scheduling/routers/slots.py`**

```python
from datetime import UTC, datetime
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends

from event_scheduling.auth import require_api_key
from event_scheduling.errors import ValidationError
from event_scheduling.interfaces.slots import ISlotService  # NOTE: see Step 6 import path
from event_scheduling.schemas.slots import SlotsResponse
from event_scheduling.validation import validate_time_zone

slots_router = APIRouter(
    prefix="/api/v1/slots", tags=["slots"], route_class=DishkaRoute, dependencies=[Depends(require_api_key)]
)

_MAX_WINDOW_DAYS = 62


def _as_utc(d: datetime) -> datetime:
    if d.tzinfo is None:
        return d.replace(tzinfo=UTC)
    return d.astimezone(UTC)


@slots_router.get("", response_model=SlotsResponse)
async def get_slots(
    event_type_id: UUID,
    start: datetime,
    end: datetime,
    time_zone: str,
    service: FromDishka[ISlotService],
) -> SlotsResponse:
    validate_time_zone(time_zone)
    ws, we = _as_utc(start), _as_utc(end)
    if we <= ws:
        raise ValidationError("end must be after start")
    if (we - ws).days > _MAX_WINDOW_DAYS:
        raise ValidationError(f"window exceeds {_MAX_WINDOW_DAYS} days")
    slots = await service.available_slots(event_type_id, ws, we, time_zone)
    return SlotsResponse(event_type_id=event_type_id, time_zone=time_zone, slots=slots)
```
> `ISlotService` lives in `event_scheduling/slots/interfaces.py`. Import it as `from event_scheduling.slots.interfaces import ISlotService` (the inline note above is a reminder — use the real path).

- [ ] **Step 5: Wire DI in `event_scheduling/ioc.py`** — add imports and REQUEST/APP providers:

```python
from event_scheduling.interfaces.busy_times import BusyTimesSource, StubBusyTimesSource
from event_scheduling.slots.interfaces import Clock, ISlotService, ISlotsReadAdapter
from event_scheduling.slots.read_adapter import SlotsReadAdapter
from event_scheduling.slots.service import SlotService, SystemClock

    @provide(scope=Scope.APP)
    def provide_clock(self) -> Clock:
        return SystemClock()

    @provide(scope=Scope.APP)
    def provide_busy_source(self) -> BusyTimesSource:
        return StubBusyTimesSource()

    @provide(scope=Scope.REQUEST)
    def provide_slots_read_adapter(self, sql: ISqlExecutor) -> ISlotsReadAdapter:
        return SlotsReadAdapter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_slot_service(
        self, read_adapter: ISlotsReadAdapter, busy_source: BusyTimesSource, clock: Clock
    ) -> ISlotService:
        return SlotService(read_adapter, busy_source, clock)
```

- [ ] **Step 6: Include the router in `event_scheduling/main.py` AND the test `app` fixture in `tests/conftest.py`.**

`main.py`: `from event_scheduling.routers.slots import slots_router` and `app.include_router(slots_router)`.
`tests/conftest.py`: in the `app` fixture that builds `FastAPI()` + includes `root_router`/`schedule_router`/`event_type_router`, ADD `application.include_router(slots_router)` (import it at the top of the fixture like the others). Without this the slots routes 404 in tests.

- [ ] **Step 7: Run — PASS.** `uv run pytest tests/test_slots_api.py -v` (needs Postgres — start a scratch DB if `initdb`/`pg_ctl` unavailable, as in slice-1 tasks).

- [ ] **Step 8: Commit**
```bash
git add event_scheduling/routers/slots.py event_scheduling/schemas/slots.py event_scheduling/ioc.py event_scheduling/main.py tests/conftest.py tests/test_slots_api.py
git commit -m "feat(slots): GET /api/v1/slots endpoint + DI wiring + integration tests"
```

---

## Task 7: docs + final checks

**Files:**
- Modify: `event-scheduling/CLAUDE.md`, `event-scheduling/docs/{API_CONTRACTS,SERVICE_OVERVIEW,DEPENDENCIES}.md`, root `docs/architecture/ARCHITECTURE.md`

**Interfaces:** docs only + full verification.

- [ ] **Step 1: Update `event-scheduling/CLAUDE.md`** — add the `slots/` module to the Architecture/Layers list; add `GET /api/v1/slots` to the Endpoints table (query params, UTC window, local-date buckets, `404`/`422`); note the engine reads the domain tables (S1), `BusyTimesSource` is still the stub, buffers/limits inert until slice 3.

- [ ] **Step 2: Update `docs/`** — `API_CONTRACTS.md`: the `/slots` request/response contract (from spec §2). `SERVICE_OVERVIEW.md`: the service now also computes availability (read-side); slice-2 maturity note. `DEPENDENCIES.md`: still no external deps; busy via `BusyTimesSource` stub. Root `docs/architecture/ARCHITECTURE.md`: add the slot engine to the event-scheduling entry + replacement roadmap (slice 2 done).

- [ ] **Step 3: Full test + lint.** Run: `cd event-scheduling && uv run pytest && ruff check . && ruff format --check .` — all green. (Scratch Postgres via docker if `initdb`/`pg_ctl` not on PATH, `TEST_POSTGRES_DSN=postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling`.)

- [ ] **Step 4: Smoke (best-effort).** `docker compose up -d --build postgres event-scheduling`, seed a schedule + event type via the API, then `curl` `GET /api/v1/slots` with a Bearer token and a 1-day window; expect a `slots` bucket. If docker build is impractical, note as unverified — pytest is the hard gate.

- [ ] **Step 5: Commit**
```bash
git add event-scheduling/CLAUDE.md event-scheduling/docs docs/architecture/ARCHITECTURE.md
git commit -m "docs(slots): document slot engine endpoint + module (slice 2)"
```

---

## Self-Review (проведён при написании плана)

**1. Покрытие спека:**
- §2 контракт API → Task 6 (роутер/схема, 404/422, cap 62д, UTC-окно + tz-группировка).
- §3 конвейер: batch-load → Task 4; per-host UTC-диапазоны (weekly/override/travel/DST) → Task 3; union + subtract busy + нарезка → Task 2 (примитивы) + Task 5 (оркестрация); группировка по локальной дате → Task 1.
- §4 структура модуля (чистое ядро vs IO-оболочка) → Task 1–6 по файлам; Clock-инъекция → Task 5.
- §5 тесты (DST, travel, override, union, min_notice, группировка, 404/422) → Tasks 1/3/5/6.
- §7 DoR (stub busy, буферы/лимиты инертны, pytest+ruff, доки) → Tasks 5/6/7.

**2. Плейсхолдеры:** код в шагах полный, без `...`. Единственная пометка «см. реальный путь импорта `ISlotService`» в Task 6 Step 4 — уточнение пути, а не заглушка; сам импорт указан в Step 5.

**3. Согласованность типов:** `Interval(start:int,end:int)` (epoch-min) определён в Task 1, используется в 2/3/5. `HostSchedule`/`EventTypeConfig`/`SlotBundle` (Task 1) — в 3/4/5. `to_epoch_min`/`from_epoch_min`/`merge_intervals`/`subtract_intervals`/`slice_into_slots` (Task 2) + `host_availability_intervals` (Task 3) — в Task 5. `ISlotsReadAdapter.load` (Task 4) и `ISlotService.available_slots` / `Clock.now` (Task 5) — потребляются роутером/DI в Task 6. `BusyTimesSource.get_busy(user_ids, window)` вызывается per-host со списком из одного `user_id` — совместимо с существующим seam'ом.
