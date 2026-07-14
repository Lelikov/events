# event-scheduling calendar-sync (iCal busy import) — срез 5 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** event-scheduling subtracts a host's external calendar busy-times (iCal URL subscription) from availability, so the slot engine and booking-create never offer/accept times that conflict with the host's real calendar.

**Architecture:** A new `calendar/` module adds a second `BusyTimesSource` (external calendar) behind the existing Protocol; a `CompositeBusyTimesSource` unions it with the booking-based source, wired into DI so the slot engine and booking-create pick it up unchanged. A background poller (mirroring the reminder loop) fetches each host's `.ics`, expands events in a rolling window, and caches busy intervals in a new table the external source reads. Management endpoints (under the admin key) connect/list/delete/sync calendars.

**Tech Stack:** Python 3.14, uv, FastAPI, Dishka, raw SQL via `SqlExecutor`, alembic, httpx, `icalendar` + `recurring-ical-events`, structlog, pytest (Docker Postgres).

## Global Constraints

- Work ONLY in `event-scheduling` (tracked by the ROOT repo `/Users/alexandrlelikov/PycharmProjects/events`). Commit in ROOT on branch `feat/calendar-sync` (create off `main` before Task 1).
- Additive: do NOT change the slot-engine (`slots/`) or booking-create (`booking/service.py`) code — they consume the `BusyTimesSource` Protocol unchanged; the ONLY wiring change is the `provide_busy_source` factory in `ioc.py`.
- Code style: NO `elif`; avoid `else` (early returns/guards); Ruff 120; frozen-dataclass DTOs; Pydantic only in `schemas/`; Protocol interfaces in `interfaces/`; raw SQL via `SqlExecutor` (`:param` binds).
- Reuse `TimeWindow` and `BusyInterval` from `event_scheduling/interfaces/busy_times.py` everywhere (the iCal parser output, the external source, and the cache all speak `BusyInterval`). Do NOT invent a parallel busy DTO.
- The `BusyTimesSource` Protocol is `get_busy(user_ids, window) -> list[BusyInterval]`. `BookingBusyTimesSource.get_busy` also accepts an optional `exclude_booking_id`. `CompositeBusyTimesSource.get_busy(user_ids, window, exclude_booking_id=None)` passes `exclude_booking_id` ONLY to the booking source; the external source is called with `(user_ids, window)`.
- Sync = full replace per calendar per tick (`delete` all cache rows for the calendar, then insert the freshly-expanded window) in one transaction. On fetch/parse error: `mark_error`, leave the last good cache intact.
- Fetch: `http`/`https` URL only (else `ValidationError`); timeout `CALENDAR_FETCH_TIMEOUT`; non-2xx → `UpstreamError`. SSRF private-IP blocking is DEFERRED (endpoints are admin-gated).
- All-day events (VALUE=DATE) → busy from UTC-midnight to next UTC-midnight (first-cut simplification; host-tz refinement deferred). Skip `TRANSP:TRANSPARENT` and `STATUS:CANCELLED`.
- Migration revision `0005`, down_revision `0004`. Window cap 62 days (matches the slot engine).
- DB tests: Docker Postgres — `docker run -d --rm --name sched-testpg -e POSTGRES_PASSWORD=postgres -e POSTGRES_DB=event_scheduling -p 5599:5432 postgres:16`, then `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest`. Do NOT pass SCHEDULING_API_KEY on the pytest command. Recreate the container to re-apply migration 0005 fresh.

---

## File Structure

New module `event_scheduling/calendar/`:
- `calendar/__init__.py` (empty)
- `calendar/dto.py` — `ExternalCalendarDTO` (frozen)
- `calendar/interfaces.py` — `IICalClient`, `IICalParser`, `ICalendarReadAdapter`, `ICalendarWriteAdapter`
- `calendar/ical_parser.py` — `ICalParser.expand(ics_bytes, window) -> list[BusyInterval]`
- `calendar/ical_client.py` — `ICalClient(timeout).fetch(url) -> bytes`
- `calendar/read_adapter.py` — `CalendarReadAdapter(sql)`
- `calendar/write_adapter.py` — `CalendarWriteAdapter(sql)`
- `calendar/busy_source.py` — `ExternalCalendarBusyTimesSource(sql)`
- `calendar/composite_busy.py` — `CompositeBusyTimesSource(booking, external)`
- `calendar/sync_service.py` — `sync_calendar(...)`
- `calendar/dispatcher.py` — `run_calendar_sync_loop(...)`
- `routers/calendar.py`, `schemas/calendar.py`

Modified: `alembic/versions/0005_external_calendar.py`, `pyproject.toml`, `config.py`, `ioc.py`, `main.py`, docs, `docker-compose.services.yml`.

Tests: `tests/test_calendar_schema.py`, `test_ical_parser.py`, `test_ical_client.py`, `test_calendar_adapters.py`, `test_external_busy_source.py`, `test_composite_busy.py`, `test_calendar_sync.py`, `test_calendar_api.py`, and an e2e in `test_slots_api.py` (or a new `test_calendar_e2e.py`).

---

## Task 1: Migration 0005 + iCal dependencies

**Files:**
- Create: `alembic/versions/0005_external_calendar.py`
- Modify: `pyproject.toml` (add `icalendar`, `recurring-ical-events`)
- Test: `tests/test_calendar_schema.py`

**Interfaces:**
- Produces: tables `external_calendar` + `external_calendar_event`; deps `icalendar`, `recurring-ical-events`.

- [ ] **Step 1: Add deps** to `pyproject.toml` `dependencies`: `"icalendar>=6.0.0"`, `"recurring-ical-events>=3.3.0"`. Then `cd event-scheduling && uv sync` (updates `uv.lock`).

- [ ] **Step 2: Write the failing test** `tests/test_calendar_schema.py`:

```python
"""Migration 0005: external_calendar + external_calendar_event."""

import pytest
from sqlalchemy import text


@pytest.mark.asyncio
async def test_external_calendar_tables_exist(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        cols = await s.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='external_calendar' ORDER BY column_name"
            )
        )
        names = {r[0] for r in cols}
        assert {"id", "host_user_id", "kind", "url", "enabled", "last_synced_at", "last_error"} <= names

        kind_ck = await s.execute(
            text("SELECT 1 FROM pg_constraint WHERE conname='ck_external_calendar_kind'")
        )
        assert kind_ck.first() is not None

        ev = await s.execute(
            text(
                "SELECT column_name FROM information_schema.columns "
                "WHERE table_name='external_calendar_event'"
            )
        )
        assert {"calendar_id", "busy_start", "busy_end"} <= {r[0] for r in ev}


@pytest.mark.asyncio
async def test_event_cache_cascades_on_calendar_delete(sessionmaker_fixture) -> None:
    from uuid import uuid4

    cal_id, host = uuid4(), uuid4()
    async with sessionmaker_fixture() as s:
        await s.execute(
            text("INSERT INTO external_calendar (id, host_user_id, url) VALUES (:id,:h,'https://x/c.ics')"),
            {"id": cal_id, "h": host},
        )
        await s.execute(
            text(
                "INSERT INTO external_calendar_event (calendar_id, busy_start, busy_end) "
                "VALUES (:c, '2026-10-01T09:00+00','2026-10-01T10:00+00')"
            ),
            {"c": cal_id},
        )
        await s.commit()
    async with sessionmaker_fixture() as s:
        await s.execute(text("DELETE FROM external_calendar WHERE id=:id"), {"id": cal_id})
        await s.commit()
    async with sessionmaker_fixture() as s:
        left = await s.execute(
            text("SELECT count(*) FROM external_calendar_event WHERE calendar_id=:c"), {"c": cal_id}
        )
        assert left.scalar_one() == 0
```

- [ ] **Step 3: Run — verify FAIL** (`relation "external_calendar" does not exist`).

- [ ] **Step 4: Write the migration** `alembic/versions/0005_external_calendar.py`:

```python
"""external_calendar + external_calendar_event (slice 5, calendar-sync)

Revision ID: 0005
Revises: 0004
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_UUID = postgresql.UUID(as_uuid=True)


def upgrade() -> None:
    op.create_table(
        "external_calendar",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("host_user_id", _UUID, nullable=False),
        sa.Column("kind", sa.Text(), server_default=sa.text("'ical_url'"), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("enabled", sa.Boolean(), server_default=sa.text("true"), nullable=False),
        sa.Column("last_synced_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint("kind IN ('ical_url')", name="ck_external_calendar_kind"),
        sa.UniqueConstraint("host_user_id", "url", name="uq_external_calendar_host_url"),
    )
    op.execute("CREATE INDEX ix_external_calendar_enabled ON external_calendar (host_user_id) WHERE enabled")
    op.create_table(
        "external_calendar_event",
        sa.Column("id", _UUID, server_default=sa.text("gen_random_uuid()"), nullable=False),
        sa.Column("calendar_id", _UUID, nullable=False),
        sa.Column("busy_start", sa.DateTime(timezone=True), nullable=False),
        sa.Column("busy_end", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["calendar_id"], ["external_calendar.id"], ondelete="CASCADE"),
        sa.CheckConstraint("busy_end > busy_start", name="ck_ext_cal_event_range"),
    )
    op.create_index(
        "ix_ext_cal_event_window", "external_calendar_event", ["calendar_id", "busy_start", "busy_end"]
    )


def downgrade() -> None:
    op.drop_table("external_calendar_event")
    op.drop_index("ix_external_calendar_enabled", table_name="external_calendar")
    op.drop_table("external_calendar")
```

- [ ] **Step 5: Run — verify PASS.** `TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest tests/test_calendar_schema.py -v` (recreate `sched-testpg` if it predates 0005). 2 pass.

- [ ] **Step 6: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/alembic/versions/0005_external_calendar.py event-scheduling/pyproject.toml \
        event-scheduling/uv.lock event-scheduling/tests/test_calendar_schema.py
git commit -m "feat(calendar): migration 0005 external_calendar + cache + iCal deps (slice 5)"
```

---

## Task 2: iCal parser (`.ics` bytes → busy intervals)

**Files:**
- Create: `event_scheduling/calendar/__init__.py` (empty), `event_scheduling/calendar/ical_parser.py`
- Test: `tests/test_ical_parser.py`

**Interfaces:**
- Consumes: `TimeWindow`, `BusyInterval` (`event_scheduling/interfaces/busy_times.py`).
- Produces: `ICalParser.expand(self, ics_bytes: bytes, window: TimeWindow) -> list[BusyInterval]`.

- [ ] **Step 1: Write the failing test** `tests/test_ical_parser.py`:

```python
import datetime as dt

from event_scheduling.calendar.ical_parser import ICalParser
from event_scheduling.interfaces.busy_times import TimeWindow

WIN = TimeWindow(dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 31, tzinfo=dt.UTC))


def _ics(body: str) -> bytes:
    return f"BEGIN:VCALENDAR\r\nVERSION:2.0\r\nPRODID:-//test//EN\r\n{body}\r\nEND:VCALENDAR\r\n".encode()


def _event(uid: str, extra: str) -> str:
    return f"BEGIN:VEVENT\r\nUID:{uid}\r\n{extra}\r\nEND:VEVENT"


def test_single_timed_event() -> None:
    ics = _ics(_event("e1", "DTSTART:20261005T090000Z\r\nDTEND:20261005T100000Z"))
    out = ICalParser().expand(ics, WIN)
    assert len(out) == 1
    assert out[0].start == dt.datetime(2026, 10, 5, 9, tzinfo=dt.UTC)
    assert out[0].end == dt.datetime(2026, 10, 5, 10, tzinfo=dt.UTC)


def test_weekly_recurrence_expanded_in_window() -> None:
    ics = _ics(_event("e2", "DTSTART:20261001T090000Z\r\nDTEND:20261001T093000Z\r\nRRULE:FREQ=WEEKLY;COUNT=3"))
    out = ICalParser().expand(ics, WIN)
    starts = sorted(b.start for b in out)
    assert starts == [
        dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC),
        dt.datetime(2026, 10, 8, 9, tzinfo=dt.UTC),
        dt.datetime(2026, 10, 15, 9, tzinfo=dt.UTC),
    ]


def test_all_day_event_is_full_utc_day() -> None:
    ics = _ics(_event("e3", "DTSTART;VALUE=DATE:20261010\r\nDTEND;VALUE=DATE:20261011"))
    out = ICalParser().expand(ics, WIN)
    assert out[0].start == dt.datetime(2026, 10, 10, tzinfo=dt.UTC)
    assert out[0].end == dt.datetime(2026, 10, 11, tzinfo=dt.UTC)


def test_transparent_and_cancelled_are_skipped() -> None:
    ics = _ics(
        _event("e4", "DTSTART:20261005T090000Z\r\nDTEND:20261005T100000Z\r\nTRANSP:TRANSPARENT")
        + "\r\n"
        + _event("e5", "DTSTART:20261006T090000Z\r\nDTEND:20261006T100000Z\r\nSTATUS:CANCELLED")
    )
    assert ICalParser().expand(ics, WIN) == []


def test_out_of_window_event_excluded() -> None:
    ics = _ics(_event("e6", "DTSTART:20261115T090000Z\r\nDTEND:20261115T100000Z"))
    assert ICalParser().expand(ics, WIN) == []
```

- [ ] **Step 2: Run — verify FAIL** (`ModuleNotFoundError`).

- [ ] **Step 3: `event_scheduling/calendar/__init__.py`** empty. **`event_scheduling/calendar/ical_parser.py`**:

```python
from datetime import UTC, date, datetime, time, timedelta

import icalendar
import recurring_ical_events

from event_scheduling.interfaces.busy_times import BusyInterval, TimeWindow


def _to_utc(value: date | datetime) -> datetime:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
    return datetime.combine(value, time.min, tzinfo=UTC)


class ICalParser:
    def expand(self, ics_bytes: bytes, window: TimeWindow) -> list[BusyInterval]:
        calendar = icalendar.Calendar.from_ical(ics_bytes)
        events = recurring_ical_events.of(calendar).between(window.start, window.end)
        win_lo = _to_utc(window.start)
        win_hi = _to_utc(window.end)
        out: list[BusyInterval] = []
        for event in events:
            interval = self._to_interval(event, win_lo, win_hi)
            if interval is not None:
                out.append(interval)
        return out

    @staticmethod
    def _to_interval(event: object, win_lo: datetime, win_hi: datetime) -> BusyInterval | None:
        if str(event.get("TRANSP", "")).upper() == "TRANSPARENT":
            return None
        if str(event.get("STATUS", "")).upper() == "CANCELLED":
            return None
        dtstart = event.get("DTSTART")
        if dtstart is None:
            return None
        start = _to_utc(dtstart.dt)
        end = ICalParser._end_of(event, dtstart)
        if end is None or end <= start:
            return None
        clipped_start = max(start, win_lo)
        clipped_end = min(end, win_hi)
        if clipped_end <= clipped_start:
            return None
        return BusyInterval(clipped_start, clipped_end)

    @staticmethod
    def _end_of(event: object, dtstart: object) -> datetime | None:
        dtend = event.get("DTEND")
        if dtend is not None:
            return _to_utc(dtend.dt)
        if isinstance(dtstart.dt, datetime):
            return None  # timed event without DTEND → zero-length, skip
        return _to_utc(dtstart.dt) + timedelta(days=1)  # all-day single date → one UTC day
```

- [ ] **Step 4: Run — verify PASS.** `... uv run pytest tests/test_ical_parser.py -v` — 5 pass. Then `uv run ruff check . && uv run ruff format --check .`.
> If `recurring_ical_events.of(...).between(...)` returns already-transparent/cancelled events or differs slightly across versions, adjust the filter — but keep the return type `list[BusyInterval]` and all 5 assertions green.

- [ ] **Step 5: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/calendar/__init__.py \
        event-scheduling/event_scheduling/calendar/ical_parser.py event-scheduling/tests/test_ical_parser.py
git commit -m "feat(calendar): iCal parser (RRULE/all-day/skip) -> busy intervals (slice 5)"
```

---

## Task 3: iCal HTTP client

**Files:**
- Create: `event_scheduling/calendar/interfaces.py`, `event_scheduling/calendar/ical_client.py`
- Test: `tests/test_ical_client.py`

**Interfaces:**
- Consumes: `errors.ValidationError`; needs an `UpstreamError` (add to `errors.py` if absent — check first: `event_scheduling/errors.py` currently has `ValidationError/NotFoundError/ConflictError`; ADD `class UpstreamError(DomainError)` if not present).
- Produces:
  - `interfaces.py`: `IICalClient.fetch(url: str) -> bytes`; `IICalParser.expand(ics_bytes: bytes, window: TimeWindow) -> list[BusyInterval]` (Protocol matching Task 2's `ICalParser`).
  - `ical_client.py`: `ICalClient(timeout_seconds: float, *, transport=None).fetch(url) -> bytes`.

- [ ] **Step 1: Ensure `UpstreamError` exists** in `event_scheduling/errors.py`. If missing, append:
```python
class UpstreamError(DomainError):
    """External fetch failed / returned an unexpected status."""
```

- [ ] **Step 2: `event_scheduling/calendar/interfaces.py`**:

```python
from typing import Protocol

from event_scheduling.interfaces.busy_times import BusyInterval, TimeWindow


class IICalClient(Protocol):
    async def fetch(self, url: str) -> bytes: ...


class IICalParser(Protocol):
    def expand(self, ics_bytes: bytes, window: TimeWindow) -> list[BusyInterval]: ...
```
> The calendar read/write adapter Protocols (`ICalendarReadAdapter`, `ICalendarWriteAdapter`) are added in Task 4 to this same file.

- [ ] **Step 3: Write the failing test** `tests/test_ical_client.py`:

```python
import httpx
import pytest

from event_scheduling.calendar.ical_client import ICalClient
from event_scheduling.errors import UpstreamError, ValidationError


def _client(handler) -> ICalClient:
    return ICalClient(10.0, transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_fetch_returns_bytes() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.host == "cal.example"
        return httpx.Response(200, content=b"BEGIN:VCALENDAR")

    out = await _client(handler).fetch("https://cal.example/c.ics")
    assert out == b"BEGIN:VCALENDAR"


@pytest.mark.asyncio
async def test_non_2xx_raises_upstream() -> None:
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    with pytest.raises(UpstreamError):
        await _client(handler).fetch("https://cal.example/c.ics")


@pytest.mark.asyncio
async def test_non_http_scheme_rejected() -> None:
    with pytest.raises(ValidationError):
        await ICalClient(10.0).fetch("file:///etc/passwd")
```

- [ ] **Step 4: Run — verify FAIL.**

- [ ] **Step 5: `event_scheduling/calendar/ical_client.py`**:

```python
from urllib.parse import urlparse

import httpx

from event_scheduling.errors import UpstreamError, ValidationError


class ICalClient:
    def __init__(self, timeout_seconds: float, *, transport: httpx.BaseTransport | None = None) -> None:
        self._timeout = timeout_seconds
        self._transport = transport

    async def fetch(self, url: str) -> bytes:
        scheme = urlparse(url).scheme.lower()
        if scheme not in ("http", "https"):
            raise ValidationError(f"unsupported url scheme: {scheme!r}")
        async with httpx.AsyncClient(transport=self._transport, timeout=self._timeout, follow_redirects=True) as client:
            try:
                resp = await client.get(url)
            except httpx.HTTPError as exc:
                raise UpstreamError(f"iCal fetch failed: {exc}") from exc
        if not resp.is_success:
            raise UpstreamError(f"iCal fetch returned {resp.status_code}")
        return resp.content
```

- [ ] **Step 6: Run — verify PASS.** `... uv run pytest tests/test_ical_client.py -v` — 3 pass; ruff clean.

- [ ] **Step 7: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/errors.py event-scheduling/event_scheduling/calendar/interfaces.py \
        event-scheduling/event_scheduling/calendar/ical_client.py event-scheduling/tests/test_ical_client.py
git commit -m "feat(calendar): iCal HTTP client (http(s) only, timeout, non-2xx->Upstream) (slice 5)"
```

---

## Task 4: Calendar read/write adapters + DTO

**Files:**
- Create: `event_scheduling/calendar/dto.py`, `event_scheduling/calendar/read_adapter.py`, `event_scheduling/calendar/write_adapter.py`
- Modify: `event_scheduling/calendar/interfaces.py` (add adapter Protocols)
- Test: `tests/test_calendar_adapters.py`

**Interfaces:**
- Consumes: `ISqlExecutor`, `BusyInterval` (Task 2), `errors.ConflictError`.
- Produces:
  - `dto.py`: `ExternalCalendarDTO(id: UUID, host_user_id: UUID, kind: str, url: str, enabled: bool, last_synced_at: datetime | None, last_error: str | None)` (frozen).
  - `interfaces.py` (append): `ICalendarReadAdapter` (`list_enabled()`, `list_by_host(host_user_id)`, `get(calendar_id)`), `ICalendarWriteAdapter` (`create(host_user_id, url)`, `delete(calendar_id)`, `replace_cache(calendar_id, events)`, `mark_synced(calendar_id, now)`, `mark_error(calendar_id, now, err)`).
  - `read_adapter.py`: `CalendarReadAdapter(sql)`; `write_adapter.py`: `CalendarWriteAdapter(sql)`.

- [ ] **Step 1: `event_scheduling/calendar/dto.py`**:

```python
from dataclasses import dataclass
from datetime import datetime
from uuid import UUID


@dataclass(frozen=True)
class ExternalCalendarDTO:
    id: UUID
    host_user_id: UUID
    kind: str
    url: str
    enabled: bool
    last_synced_at: datetime | None
    last_error: str | None
```

- [ ] **Step 2: Append adapter Protocols** to `event_scheduling/calendar/interfaces.py`:

```python
from collections.abc import Sequence  # add to existing imports
from datetime import datetime
from uuid import UUID

from event_scheduling.calendar.dto import ExternalCalendarDTO


class ICalendarReadAdapter(Protocol):
    async def list_enabled(self) -> list[ExternalCalendarDTO]: ...
    async def list_by_host(self, host_user_id: UUID) -> list[ExternalCalendarDTO]: ...
    async def get(self, calendar_id: UUID) -> ExternalCalendarDTO | None: ...


class ICalendarWriteAdapter(Protocol):
    async def create(self, host_user_id: UUID, url: str) -> ExternalCalendarDTO: ...
    async def delete(self, calendar_id: UUID) -> None: ...
    async def replace_cache(self, calendar_id: UUID, events: Sequence[BusyInterval]) -> None: ...
    async def mark_synced(self, calendar_id: UUID, now: datetime) -> None: ...
    async def mark_error(self, calendar_id: UUID, now: datetime, err: str) -> None: ...
```

- [ ] **Step 3: Write the failing test** `tests/test_calendar_adapters.py`:

```python
import datetime as dt
from uuid import uuid4

import pytest

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.calendar.read_adapter import CalendarReadAdapter
from event_scheduling.calendar.write_adapter import CalendarWriteAdapter
from event_scheduling.errors import ConflictError
from event_scheduling.interfaces.busy_times import BusyInterval

NOW = dt.datetime(2026, 9, 1, 12, tzinfo=dt.UTC)


@pytest.mark.asyncio
async def test_create_list_get_and_duplicate(sessionmaker_fixture) -> None:
    host = uuid4()
    async with sessionmaker_fixture() as s:
        w = CalendarWriteAdapter(SqlExecutor(s))
        created = await w.create(host, "https://cal/x.ics")
        await s.commit()
        assert created.host_user_id == host and created.enabled is True and created.kind == "ical_url"

    async with sessionmaker_fixture() as s:
        r = CalendarReadAdapter(SqlExecutor(s))
        by_host = await r.list_by_host(host)
        assert [c.id for c in by_host] == [created.id]
        assert (await r.get(created.id)).url == "https://cal/x.ics"
        assert await r.list_enabled()  # non-empty

    async with sessionmaker_fixture() as s:
        w = CalendarWriteAdapter(SqlExecutor(s))
        with pytest.raises(ConflictError):
            await w.create(host, "https://cal/x.ics")  # (host,url) unique


@pytest.mark.asyncio
async def test_replace_cache_and_mark(sessionmaker_fixture) -> None:
    host = uuid4()
    async with sessionmaker_fixture() as s:
        w = CalendarWriteAdapter(SqlExecutor(s))
        cal = await w.create(host, "https://cal/y.ics")
        await w.replace_cache(cal.id, [BusyInterval(NOW, NOW + dt.timedelta(hours=1))])
        await w.mark_synced(cal.id, NOW)
        await s.commit()

    async with sessionmaker_fixture() as s:
        from sqlalchemy import text
        n = (await s.execute(text("SELECT count(*) FROM external_calendar_event WHERE calendar_id=:c"), {"c": cal.id})).scalar_one()
        assert n == 1
        r = CalendarReadAdapter(SqlExecutor(s))
        assert (await r.get(cal.id)).last_synced_at is not None

    # replace overwrites (delete+insert)
    async with sessionmaker_fixture() as s:
        w = CalendarWriteAdapter(SqlExecutor(s))
        await w.replace_cache(cal.id, [])
        await w.mark_error(cal.id, NOW, "boom")
        await s.commit()
    async with sessionmaker_fixture() as s:
        from sqlalchemy import text
        n = (await s.execute(text("SELECT count(*) FROM external_calendar_event WHERE calendar_id=:c"), {"c": cal.id})).scalar_one()
        assert n == 0
        r = CalendarReadAdapter(SqlExecutor(s))
        assert (await r.get(cal.id)).last_error == "boom"
```

- [ ] **Step 4: Run — verify FAIL.**

- [ ] **Step 5: `event_scheduling/calendar/read_adapter.py`**:

```python
from uuid import UUID

from event_scheduling.calendar.dto import ExternalCalendarDTO
from event_scheduling.interfaces.sql import ISqlExecutor

_COLS = "id, host_user_id, kind, url, enabled, last_synced_at, last_error"


def _to_dto(r: dict) -> ExternalCalendarDTO:
    return ExternalCalendarDTO(
        id=r["id"],
        host_user_id=r["host_user_id"],
        kind=r["kind"],
        url=r["url"],
        enabled=r["enabled"],
        last_synced_at=r["last_synced_at"],
        last_error=r["last_error"],
    )


class CalendarReadAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def list_enabled(self) -> list[ExternalCalendarDTO]:
        rows = await self._sql.fetch_all(
            f"SELECT {_COLS} FROM external_calendar WHERE enabled ORDER BY created_at", {}  # noqa: S608
        )
        return [_to_dto(r) for r in rows]

    async def list_by_host(self, host_user_id: UUID) -> list[ExternalCalendarDTO]:
        rows = await self._sql.fetch_all(
            f"SELECT {_COLS} FROM external_calendar WHERE host_user_id=:h ORDER BY created_at",  # noqa: S608
            {"h": host_user_id},
        )
        return [_to_dto(r) for r in rows]

    async def get(self, calendar_id: UUID) -> ExternalCalendarDTO | None:
        row = await self._sql.fetch_one(
            f"SELECT {_COLS} FROM external_calendar WHERE id=:id", {"id": calendar_id}  # noqa: S608
        )
        if row is None:
            return None
        return _to_dto(row)
```

- [ ] **Step 6: `event_scheduling/calendar/write_adapter.py`**:

```python
from collections.abc import Sequence
from datetime import datetime
from uuid import UUID

from sqlalchemy.exc import IntegrityError

from event_scheduling.calendar.dto import ExternalCalendarDTO
from event_scheduling.calendar.read_adapter import _to_dto
from event_scheduling.errors import ConflictError
from event_scheduling.interfaces.busy_times import BusyInterval
from event_scheduling.interfaces.sql import ISqlExecutor

_COLS = "id, host_user_id, kind, url, enabled, last_synced_at, last_error"


class CalendarWriteAdapter:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def create(self, host_user_id: UUID, url: str) -> ExternalCalendarDTO:
        try:
            async with self._sql.begin_nested():
                row = await self._sql.fetch_one(
                    f"INSERT INTO external_calendar (host_user_id, url) VALUES (:h, :u) RETURNING {_COLS}",  # noqa: S608
                    {"h": host_user_id, "u": url},
                )
        except IntegrityError as exc:
            raise ConflictError("calendar already connected for this url") from exc
        return _to_dto(row)

    async def delete(self, calendar_id: UUID) -> None:
        await self._sql.execute("DELETE FROM external_calendar WHERE id=:id", {"id": calendar_id})

    async def replace_cache(self, calendar_id: UUID, events: Sequence[BusyInterval]) -> None:
        await self._sql.execute(
            "DELETE FROM external_calendar_event WHERE calendar_id=:c", {"c": calendar_id}
        )
        for ev in events:
            await self._sql.execute(
                "INSERT INTO external_calendar_event (calendar_id, busy_start, busy_end) VALUES (:c, :s, :e)",
                {"c": calendar_id, "s": ev.start, "e": ev.end},
            )

    async def mark_synced(self, calendar_id: UUID, now: datetime) -> None:
        await self._sql.execute(
            "UPDATE external_calendar SET last_synced_at=:n, last_error=NULL, updated_at=now() WHERE id=:id",
            {"n": now, "id": calendar_id},
        )

    async def mark_error(self, calendar_id: UUID, now: datetime, err: str) -> None:
        await self._sql.execute(
            "UPDATE external_calendar SET last_synced_at=:n, last_error=:e, updated_at=now() WHERE id=:id",
            {"n": now, "e": err, "id": calendar_id},
        )
```
> `create` wraps the INSERT in `begin_nested()` so the `uq_external_calendar_host_url` violation surfaces as `ConflictError` without poisoning the outer transaction — same SAVEPOINT pattern as `booking/write_adapter.py`.

- [ ] **Step 7: Run — verify PASS.** `... uv run pytest tests/test_calendar_adapters.py -v` — 2 pass; ruff clean.

- [ ] **Step 8: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/calendar/dto.py event-scheduling/event_scheduling/calendar/interfaces.py \
        event-scheduling/event_scheduling/calendar/read_adapter.py event-scheduling/event_scheduling/calendar/write_adapter.py \
        event-scheduling/tests/test_calendar_adapters.py
git commit -m "feat(calendar): ExternalCalendarDTO + read/write adapters (replace-cache, mark) (slice 5)"
```

---

## Task 5: External + Composite busy sources + DI swap (core value)

**Files:**
- Create: `event_scheduling/calendar/busy_source.py`, `event_scheduling/calendar/composite_busy.py`
- Modify: `event_scheduling/ioc.py` (`provide_busy_source` → composite)
- Test: `tests/test_external_busy_source.py`, `tests/test_composite_busy.py`, plus an e2e assertion in `tests/test_calendar_e2e.py`

**Interfaces:**
- Consumes: `ISqlExecutor`, `BusyTimesSource`/`BusyInterval`/`TimeWindow`, `BookingBusyTimesSource`.
- Produces:
  - `ExternalCalendarBusyTimesSource(sql).get_busy(user_ids, window) -> list[BusyInterval]`.
  - `CompositeBusyTimesSource(booking, external).get_busy(user_ids, window, exclude_booking_id=None) -> list[BusyInterval]`.

- [ ] **Step 1: `event_scheduling/calendar/busy_source.py`**:

```python
from collections.abc import Sequence
from uuid import UUID

from event_scheduling.interfaces.busy_times import BusyInterval, TimeWindow
from event_scheduling.interfaces.sql import ISqlExecutor

_BUSY_SQL = """
    SELECT e.busy_start, e.busy_end
    FROM external_calendar_event e
    JOIN external_calendar c ON c.id = e.calendar_id
    WHERE c.enabled
      AND c.host_user_id = ANY(:users)
      AND tstzrange(e.busy_start, e.busy_end) && tstzrange(:lo, :hi)
"""


class ExternalCalendarBusyTimesSource:
    def __init__(self, sql: ISqlExecutor) -> None:
        self._sql = sql

    async def get_busy(self, user_ids: Sequence[UUID], window: TimeWindow) -> list[BusyInterval]:
        if not user_ids:
            return []
        rows = await self._sql.fetch_all(
            _BUSY_SQL, {"users": list(user_ids), "lo": window.start, "hi": window.end}
        )
        return [BusyInterval(r["busy_start"], r["busy_end"]) for r in rows]
```

- [ ] **Step 2: `event_scheduling/calendar/composite_busy.py`**:

```python
from collections.abc import Sequence
from uuid import UUID

from event_scheduling.interfaces.busy_times import BusyInterval, BusyTimesSource, TimeWindow


class CompositeBusyTimesSource:
    """Unions the booking-based busy source with the external-calendar one.

    Satisfies BusyTimesSource. The optional exclude_booking_id is forwarded ONLY to the
    booking source (booking-create passes it; the slot engine does not).
    """

    def __init__(self, booking: BusyTimesSource, external: BusyTimesSource) -> None:
        self._booking = booking
        self._external = external

    async def get_busy(
        self, user_ids: Sequence[UUID], window: TimeWindow, exclude_booking_id: UUID | None = None
    ) -> list[BusyInterval]:
        booking_busy = await self._booking.get_busy(user_ids, window, exclude_booking_id)
        external_busy = await self._external.get_busy(user_ids, window)
        return [*booking_busy, *external_busy]
```

- [ ] **Step 3: Write the failing tests.** `tests/test_composite_busy.py` (unit, fakes):

```python
import datetime as dt
from uuid import uuid4

import pytest

from event_scheduling.calendar.composite_busy import CompositeBusyTimesSource
from event_scheduling.interfaces.busy_times import BusyInterval, TimeWindow

WIN = TimeWindow(dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC))
A = BusyInterval(dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC), dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC))
B = BusyInterval(dt.datetime(2026, 10, 1, 11, tzinfo=dt.UTC), dt.datetime(2026, 10, 1, 12, tzinfo=dt.UTC))


class _Booking:
    def __init__(self) -> None:
        self.exclude_seen = "unset"

    async def get_busy(self, user_ids, window, exclude_booking_id=None):
        self.exclude_seen = exclude_booking_id
        return [A]


class _External:
    async def get_busy(self, user_ids, window):
        return [B]


@pytest.mark.asyncio
async def test_unions_both_sources_and_forwards_exclude() -> None:
    booking = _Booking()
    comp = CompositeBusyTimesSource(booking, _External())
    excl = uuid4()
    out = await comp.get_busy([uuid4()], WIN, excl)
    assert out == [A, B]
    assert booking.exclude_seen == excl


@pytest.mark.asyncio
async def test_default_exclude_is_none() -> None:
    booking = _Booking()
    await CompositeBusyTimesSource(booking, _External()).get_busy([uuid4()], WIN)
    assert booking.exclude_seen is None
```

`tests/test_external_busy_source.py` (integration):

```python
import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.calendar.busy_source import ExternalCalendarBusyTimesSource
from event_scheduling.interfaces.busy_times import TimeWindow

WIN = TimeWindow(dt.datetime(2026, 10, 1, tzinfo=dt.UTC), dt.datetime(2026, 10, 2, tzinfo=dt.UTC))


async def _seed(s, host, *, enabled=True, busy=(dt.datetime(2026, 10, 1, 9, tzinfo=dt.UTC), dt.datetime(2026, 10, 1, 10, tzinfo=dt.UTC))):
    cal = uuid4()
    await s.execute(
        text("INSERT INTO external_calendar (id, host_user_id, url, enabled) VALUES (:id,:h,:u,:en)"),
        {"id": cal, "h": host, "u": f"https://c/{cal}.ics", "en": enabled},
    )
    await s.execute(
        text("INSERT INTO external_calendar_event (calendar_id, busy_start, busy_end) VALUES (:c,:s,:e)"),
        {"c": cal, "s": busy[0], "e": busy[1]},
    )
    return cal


@pytest.mark.asyncio
async def test_returns_busy_for_host_in_window(sessionmaker_fixture) -> None:
    host = uuid4()
    async with sessionmaker_fixture() as s:
        await _seed(s, host)
        await s.commit()
    async with sessionmaker_fixture() as s:
        out = await ExternalCalendarBusyTimesSource(SqlExecutor(s)).get_busy([host], WIN)
    assert len(out) == 1


@pytest.mark.asyncio
async def test_excludes_disabled_and_other_host_and_out_of_window(sessionmaker_fixture) -> None:
    host, other = uuid4(), uuid4()
    async with sessionmaker_fixture() as s:
        await _seed(s, host, enabled=False)
        await _seed(s, other)
        await _seed(s, host, busy=(dt.datetime(2026, 11, 1, 9, tzinfo=dt.UTC), dt.datetime(2026, 11, 1, 10, tzinfo=dt.UTC)))
        await s.commit()
    async with sessionmaker_fixture() as s:
        out = await ExternalCalendarBusyTimesSource(SqlExecutor(s)).get_busy([host], WIN)
    assert out == []
```

- [ ] **Step 4: Run — verify FAIL** (composite import + external busy).

- [ ] **Step 5: Implement** `busy_source.py` + `composite_busy.py` (code in Steps 1-2). Run — 4 tests pass.

- [ ] **Step 6: DI swap** in `event_scheduling/ioc.py`. Add imports:
```python
from event_scheduling.calendar.busy_source import ExternalCalendarBusyTimesSource
from event_scheduling.calendar.composite_busy import CompositeBusyTimesSource
```
Replace the body of `provide_busy_source`:
```python
    @provide(scope=Scope.REQUEST)
    def provide_busy_source(self, sql: ISqlExecutor) -> BusyTimesSource:
        return CompositeBusyTimesSource(BookingBusyTimesSource(sql), ExternalCalendarBusyTimesSource(sql))
```
> `BookingBusyTimesSource` is already imported. The slot engine + booking-create providers are unchanged — they receive the composite via the `BusyTimesSource` type.

- [ ] **Step 7: e2e test** `tests/test_calendar_e2e.py` — an external busy event excludes the overlapping slot from `GET /api/v1/slots`. Mirror the existing slots-API integration test setup (`tests/test_slots_api.py`): seed a schedule + event_type + host, then seed an `external_calendar` + `external_calendar_event` overlapping one slot, and assert that time is absent from the response. Reuse the `client` fixture (sends the API key). Concretely:

```python
import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text

# Reuse the same seeding helpers the existing slots test uses. If test_slots_api.py exposes
# a helper to seed (schedule, event_type, host), import and use it; otherwise replicate its
# minimal insert block here (event_type + schedule + weekly_hours + host) so a known slot exists.


@pytest.mark.asyncio
async def test_external_busy_excludes_slot(client, sessionmaker_fixture) -> None:
    # 1) seed event_type + host + schedule so a slot exists at a known UTC time T (copy the
    #    pattern from tests/test_slots_api.py's happy-path setup).
    # 2) seed an external_calendar for the host + an external_calendar_event covering T.
    # 3) GET /api/v1/slots for the window; assert T is NOT in any date bucket.
    ...
```
> This step's implementer MUST read `tests/test_slots_api.py` first and reuse its exact seeding approach so the test is real (not a stub). The assertion: a slot that WOULD appear without the external event is absent WITH it. Replace the `...` with the concrete test built on the existing slots-test scaffolding. Do not leave `...` in the committed test.

- [ ] **Step 8: Run — full slots + calendar suites green.** `... uv run pytest tests/test_composite_busy.py tests/test_external_busy_source.py tests/test_calendar_e2e.py tests/test_slots_api.py -v`; ruff clean.

- [ ] **Step 9: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/calendar/busy_source.py event-scheduling/event_scheduling/calendar/composite_busy.py \
        event-scheduling/event_scheduling/ioc.py event-scheduling/tests/test_composite_busy.py \
        event-scheduling/tests/test_external_busy_source.py event-scheduling/tests/test_calendar_e2e.py
git commit -m "feat(calendar): external + composite busy sources; slot engine subtracts external busy (slice 5)"
```

---

## Task 6: Sync service + background poller + config + lifespan

**Files:**
- Create: `event_scheduling/calendar/sync_service.py`, `event_scheduling/calendar/dispatcher.py`
- Modify: `event_scheduling/config.py` (settings), `event_scheduling/main.py` (3rd background task)
- Test: `tests/test_calendar_sync.py`

**Interfaces:**
- Consumes: `IICalClient` (Task 3), `IICalParser`/`ICalParser` (Task 2), `CalendarReadAdapter`/`CalendarWriteAdapter` (Task 4), `Clock`, `TimeWindow`, `SqlExecutor as _SqlExec`.
- Produces:
  - `sync_calendar(sql, client, parser, clock, calendar: ExternalCalendarDTO, window_days: int) -> None`.
  - `run_calendar_sync_loop(sessionmaker, client, parser, clock, *, interval_s: float, window_days: int, stop: asyncio.Event) -> None`.
  - `Settings.calendar_sync_enabled/calendar_sync_interval_seconds/calendar_sync_window_days/calendar_fetch_timeout_seconds`.

- [ ] **Step 1: Write the failing test** `tests/test_calendar_sync.py`:

```python
import datetime as dt
from uuid import uuid4

import pytest
from sqlalchemy import text

from event_scheduling.adapters.sql import SqlExecutor
from event_scheduling.calendar.sync_service import sync_calendar
from event_scheduling.calendar.write_adapter import CalendarWriteAdapter
from event_scheduling.interfaces.busy_times import BusyInterval

NOW = dt.datetime(2026, 9, 1, 12, tzinfo=dt.UTC)


class _Clock:
    def now(self):
        return NOW


class _OkClient:
    async def fetch(self, url):
        return b"ICS-BYTES"


class _BoomClient:
    async def fetch(self, url):
        raise RuntimeError("network down")


class _Parser:
    def expand(self, ics_bytes, window):
        return [BusyInterval(NOW + dt.timedelta(hours=1), NOW + dt.timedelta(hours=2))]


async def _mk_cal(s, host="h"):
    from event_scheduling.calendar.read_adapter import CalendarReadAdapter
    cal = await CalendarWriteAdapter(SqlExecutor(s)).create(uuid4(), f"https://c/{uuid4()}.ics")
    return cal


@pytest.mark.asyncio
async def test_sync_success_replaces_cache_and_marks(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        cal = await _mk_cal(s)
        await s.commit()
    async with sessionmaker_fixture() as s:
        await sync_calendar(SqlExecutor(s), _OkClient(), _Parser(), _Clock(), cal, window_days=62)
        await s.commit()
    async with sessionmaker_fixture() as s:
        n = (await s.execute(text("SELECT count(*) FROM external_calendar_event WHERE calendar_id=:c"), {"c": cal.id})).scalar_one()
        assert n == 1
        row = (await s.execute(text("SELECT last_synced_at, last_error FROM external_calendar WHERE id=:c"), {"c": cal.id})).mappings().one()
        assert row["last_synced_at"] is not None and row["last_error"] is None


@pytest.mark.asyncio
async def test_sync_fetch_failure_marks_error_and_keeps_cache(sessionmaker_fixture) -> None:
    async with sessionmaker_fixture() as s:
        cal = await _mk_cal(s)
        await CalendarWriteAdapter(SqlExecutor(s)).replace_cache(cal.id, [BusyInterval(NOW, NOW + dt.timedelta(hours=1))])
        await s.commit()
    async with sessionmaker_fixture() as s:
        await sync_calendar(SqlExecutor(s), _BoomClient(), _Parser(), _Clock(), cal, window_days=62)
        await s.commit()
    async with sessionmaker_fixture() as s:
        n = (await s.execute(text("SELECT count(*) FROM external_calendar_event WHERE calendar_id=:c"), {"c": cal.id})).scalar_one()
        assert n == 1  # old cache preserved
        err = (await s.execute(text("SELECT last_error FROM external_calendar WHERE id=:c"), {"c": cal.id})).scalar_one()
        assert err is not None
```

- [ ] **Step 2: Run — verify FAIL.**

- [ ] **Step 3: `event_scheduling/calendar/sync_service.py`**:

```python
from datetime import timedelta

import structlog

from event_scheduling.calendar.dto import ExternalCalendarDTO
from event_scheduling.calendar.interfaces import IICalClient, IICalParser
from event_scheduling.calendar.write_adapter import CalendarWriteAdapter
from event_scheduling.interfaces.busy_times import TimeWindow
from event_scheduling.interfaces.sql import ISqlExecutor
from event_scheduling.slots.interfaces import Clock

logger = structlog.get_logger(__name__)


async def sync_calendar(
    sql: ISqlExecutor,
    client: IICalClient,
    parser: IICalParser,
    clock: Clock,
    calendar: ExternalCalendarDTO,
    window_days: int,
) -> None:
    write = CalendarWriteAdapter(sql)
    now = clock.now()
    try:
        ics_bytes = await client.fetch(calendar.url)
        events = parser.expand(ics_bytes, TimeWindow(now, now + timedelta(days=window_days)))
    except Exception as exc:  # noqa: BLE001 - any fetch/parse failure keeps the last good cache
        logger.warning("calendar sync failed", calendar_id=str(calendar.id), error=str(exc))
        await write.mark_error(calendar.id, now, str(exc))
        return
    await write.replace_cache(calendar.id, events)
    await write.mark_synced(calendar.id, now)
```

- [ ] **Step 4: `event_scheduling/calendar/dispatcher.py`** (mirror `reminders/dispatcher.py::run_reminder_loop`):

```python
import asyncio
import contextlib
from typing import TYPE_CHECKING

import structlog

from event_scheduling.adapters.sql import SqlExecutor as _SqlExec
from event_scheduling.calendar.read_adapter import CalendarReadAdapter
from event_scheduling.calendar.sync_service import sync_calendar

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import async_sessionmaker

    from event_scheduling.calendar.interfaces import IICalClient, IICalParser
    from event_scheduling.slots.interfaces import Clock

logger = structlog.get_logger(__name__)


async def run_calendar_sync_loop(
    sessionmaker: "async_sessionmaker",
    client: "IICalClient",
    parser: "IICalParser",
    clock: "Clock",
    *,
    interval_s: float,
    window_days: int,
    stop: asyncio.Event,
) -> None:
    """Background poller: own session per tick, sync each enabled calendar in isolation, commit."""
    while not stop.is_set():
        try:
            async with sessionmaker() as session:
                sql = _SqlExec(session)
                calendars = await CalendarReadAdapter(sql).list_enabled()
                for calendar in calendars:
                    await sync_calendar(sql, client, parser, clock, calendar, window_days)
                await session.commit()
        except Exception:
            logger.exception("calendar sync tick failed")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(stop.wait(), timeout=interval_s)
```
> Each `sync_calendar` already swallows its own fetch/parse errors (mark_error), so one bad calendar can't abort the tick's other calendars. The outer try/except guards DB/commit failures.

- [ ] **Step 5: Add settings** to `event_scheduling/config.py` (after the reminder block, before the validator):

```python
    # Calendar-sync (slice 5): background poller imports external iCal busy-times.
    calendar_sync_enabled: bool = True
    calendar_sync_interval_seconds: float = 300.0
    calendar_sync_window_days: int = 62
    calendar_fetch_timeout_seconds: float = 15.0
```

- [ ] **Step 6: Wire the lifespan** in `event_scheduling/main.py`. Add imports:
```python
from event_scheduling.calendar.dispatcher import run_calendar_sync_loop
from event_scheduling.calendar.ical_client import ICalClient
from event_scheduling.calendar.ical_parser import ICalParser
```
In `lifespan`, after the reminder-task block and before `try:`, append a third task guarded by the toggle (reuse the existing `sessionmaker`, `clock`, `stop` locals):
```python
    if settings.calendar_sync_enabled:
        tasks.append(
            asyncio.create_task(
                run_calendar_sync_loop(
                    sessionmaker,
                    ICalClient(settings.calendar_fetch_timeout_seconds),
                    ICalParser(),
                    clock,
                    interval_s=settings.calendar_sync_interval_seconds,
                    window_days=settings.calendar_sync_window_days,
                    stop=stop,
                )
            )
        )
```
> The existing `finally` already cancels+awaits every task in `tasks` — no shutdown change needed.

- [ ] **Step 7: Run — verify PASS + boot.** `... uv run pytest tests/test_calendar_sync.py -v` (2 pass); then the full suite `... uv run pytest -q` to confirm the lifespan/config changes didn't break app startup tests; ruff clean.

- [ ] **Step 8: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/calendar/sync_service.py event-scheduling/event_scheduling/calendar/dispatcher.py \
        event-scheduling/event_scheduling/config.py event-scheduling/event_scheduling/main.py \
        event-scheduling/tests/test_calendar_sync.py
git commit -m "feat(calendar): sync service + background poller + config + lifespan task (slice 5)"
```

---

## Task 7: Management API (connect/list/delete/sync) + schemas + DI

**Files:**
- Create: `event_scheduling/schemas/calendar.py`, `event_scheduling/routers/calendar.py`
- Modify: `event_scheduling/ioc.py` (calendar adapter + client/parser providers), `event_scheduling/main.py` (include router)
- Test: `tests/test_calendar_api.py`

**Interfaces:**
- Consumes: `CalendarReadAdapter`/`CalendarWriteAdapter`, `ICalendarReadAdapter`/`ICalendarWriteAdapter`, `IICalClient`/`IICalParser`, `sync_calendar`, `Clock`, `Settings`, `require_api_key`.
- Produces: routes `POST/GET/DELETE /api/v1/calendars` + `POST /api/v1/calendars/{id}/sync`; ioc providers for the calendar adapters (REQUEST) and `IICalClient`/`IICalParser` (APP).

- [ ] **Step 1: `event_scheduling/schemas/calendar.py`**:

```python
from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from event_scheduling.calendar.dto import ExternalCalendarDTO


class CreateCalendarRequest(BaseModel):
    host_user_id: UUID
    url: str


class CalendarResponse(BaseModel):
    id: UUID
    host_user_id: UUID
    kind: str
    url: str
    enabled: bool
    last_synced_at: datetime | None
    last_error: str | None

    @classmethod
    def from_dto(cls, d: ExternalCalendarDTO) -> CalendarResponse:
        return cls(
            id=d.id,
            host_user_id=d.host_user_id,
            kind=d.kind,
            url=d.url,
            enabled=d.enabled,
            last_synced_at=d.last_synced_at,
            last_error=d.last_error,
        )


class CalendarListResponse(BaseModel):
    items: list[CalendarResponse]
```

- [ ] **Step 2: Add ioc providers** to `event_scheduling/ioc.py`. Imports:
```python
from event_scheduling.calendar.ical_client import ICalClient
from event_scheduling.calendar.ical_parser import ICalParser
from event_scheduling.calendar.interfaces import ICalendarReadAdapter, ICalendarWriteAdapter, IICalClient, IICalParser
from event_scheduling.calendar.read_adapter import CalendarReadAdapter
from event_scheduling.calendar.write_adapter import CalendarWriteAdapter
```
Providers (REQUEST for adapters, APP for the stateless client/parser):
```python
    @provide(scope=Scope.REQUEST)
    def provide_calendar_read(self, sql: ISqlExecutor) -> ICalendarReadAdapter:
        return CalendarReadAdapter(sql)

    @provide(scope=Scope.REQUEST)
    def provide_calendar_write(self, sql: ISqlExecutor) -> ICalendarWriteAdapter:
        return CalendarWriteAdapter(sql)

    @provide(scope=Scope.APP)
    def provide_ical_client(self, settings: Settings) -> IICalClient:
        return ICalClient(settings.calendar_fetch_timeout_seconds)

    @provide(scope=Scope.APP)
    def provide_ical_parser(self) -> IICalParser:
        return ICalParser()
```

- [ ] **Step 3: Write the failing test** `tests/test_calendar_api.py` (integration, `client` fixture sends the API key; stub the iCal client via a Dishka override so `sync` doesn't hit the network — mirror `tests/conftest.py`'s `client_fake_users` override pattern):

```python
from uuid import uuid4

import pytest


@pytest.mark.asyncio
async def test_create_list_delete(client) -> None:
    host = str(uuid4())
    r = client.post("/api/v1/calendars", json={"host_user_id": host, "url": "https://cal/x.ics"})
    assert r.status_code == 201
    cal_id = r.json()["id"]
    assert r.json()["kind"] == "ical_url" and r.json()["enabled"] is True

    dup = client.post("/api/v1/calendars", json={"host_user_id": host, "url": "https://cal/x.ics"})
    assert dup.status_code == 409

    bad = client.post("/api/v1/calendars", json={"host_user_id": host, "url": "file:///etc/passwd"})
    assert bad.status_code == 422

    lst = client.get(f"/api/v1/calendars?host_user_id={host}")
    assert [c["id"] for c in lst.json()["items"]] == [cal_id]

    assert client.delete(f"/api/v1/calendars/{cal_id}").status_code == 204
    assert client.get(f"/api/v1/calendars?host_user_id={host}").json()["items"] == []
```
> If exercising `POST /{id}/sync` in a test, add a Dishka `IICalClient` override returning fixture `.ics` bytes (so no network). The create/list/delete test above needs no override. The implementer MAY add a sync test using the override pattern from `client_fake_users` if straightforward; otherwise the sync endpoint is covered by Task 6's `sync_calendar` unit tests + this endpoint's wiring.

- [ ] **Step 4: Run — verify FAIL** (route 404 / not wired).

- [ ] **Step 5: `event_scheduling/routers/calendar.py`**:

```python
from uuid import UUID

from dishka.integrations.fastapi import DishkaRoute, FromDishka
from fastapi import APIRouter, Depends, status

from event_scheduling.auth import require_api_key
from event_scheduling.calendar.interfaces import (
    ICalendarReadAdapter,
    ICalendarWriteAdapter,
    IICalClient,
    IICalParser,
)
from event_scheduling.calendar.sync_service import sync_calendar
from event_scheduling.config import Settings
from event_scheduling.errors import NotFoundError, ValidationError
from event_scheduling.schemas.calendar import CalendarListResponse, CalendarResponse, CreateCalendarRequest
from event_scheduling.slots.interfaces import Clock

calendar_router = APIRouter(
    prefix="/api/v1/calendars", tags=["calendars"], route_class=DishkaRoute, dependencies=[Depends(require_api_key)]
)

_ALLOWED_SCHEMES = ("http://", "https://")


@calendar_router.post("", response_model=CalendarResponse, status_code=status.HTTP_201_CREATED)
async def create_calendar(
    body: CreateCalendarRequest, write: FromDishka[ICalendarWriteAdapter]
) -> CalendarResponse:
    if not body.url.startswith(_ALLOWED_SCHEMES):
        raise ValidationError("url must be http(s)")
    return CalendarResponse.from_dto(await write.create(body.host_user_id, body.url))


@calendar_router.get("", response_model=CalendarListResponse)
async def list_calendars(host_user_id: UUID, read: FromDishka[ICalendarReadAdapter]) -> CalendarListResponse:
    rows = await read.list_by_host(host_user_id)
    return CalendarListResponse(items=[CalendarResponse.from_dto(c) for c in rows])


@calendar_router.delete("/{calendar_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_calendar(calendar_id: UUID, write: FromDishka[ICalendarWriteAdapter]) -> None:
    await write.delete(calendar_id)


@calendar_router.post("/{calendar_id}/sync", response_model=CalendarResponse)
async def sync_now(
    calendar_id: UUID,
    read: FromDishka[ICalendarReadAdapter],
    sql: FromDishka["ISqlExecutor"],  # noqa: F821 - resolved below
    client: FromDishka[IICalClient],
    parser: FromDishka[IICalParser],
    clock: FromDishka[Clock],
    settings: FromDishka[Settings],
) -> CalendarResponse:
    calendar = await read.get(calendar_id)
    if calendar is None:
        raise NotFoundError("calendar not found")
    await sync_calendar(sql, client, parser, clock, calendar, settings.calendar_sync_window_days)
    refreshed = await read.get(calendar_id)
    return CalendarResponse.from_dto(refreshed)
```
> The `sync_now` handler needs an `ISqlExecutor` injected — import it: `from event_scheduling.interfaces.sql import ISqlExecutor` and replace the quoted annotation with the real type. `sync_calendar` marks synced/error and never raises for fetch failures, so this returns 200 with `last_error` populated on a bad feed.

- [ ] **Step 6: Include the router** in `event_scheduling/main.py`: `from event_scheduling.routers.calendar import calendar_router` and `app.include_router(calendar_router)` (next to the other `include_router` calls). Also add `calendar_router` to the test app builders in `tests/conftest.py` (the `app` and `client_fake_users` fixtures list the routers explicitly — add `calendar_router` import + `application.include_router(calendar_router)` to BOTH, mirroring how the other routers are registered).

- [ ] **Step 7: Run — verify PASS + full suite.** `... uv run pytest -q` — calendar-api tests pass; full suite green; ruff clean.

- [ ] **Step 8: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/event_scheduling/schemas/calendar.py event-scheduling/event_scheduling/routers/calendar.py \
        event-scheduling/event_scheduling/ioc.py event-scheduling/event_scheduling/main.py \
        event-scheduling/tests/conftest.py event-scheduling/tests/test_calendar_api.py
git commit -m "feat(calendar): management API (connect/list/delete/sync) + DI wiring (slice 5)"
```

---

## Task 8: docs + compose env + final gate

**Files:**
- Modify: `event-scheduling/CLAUDE.md`, `event-scheduling/docs/DATA_MODEL.md`, `event-scheduling/docs/SERVICE_OVERVIEW.md`, root `docs/architecture/ARCHITECTURE.md`, `docker-compose.services.yml`

- [ ] **Step 1: Full suite + lint gate.** `cd /Users/alexandrlelikov/PycharmProjects/events/event-scheduling && TEST_POSTGRES_DSN='postgresql+asyncpg://postgres:postgres@localhost:5599/event_scheduling' uv run pytest -q && uv run ruff check . && uv run ruff format --check .` — all green. Recreate `sched-testpg` if it predates 0005.

- [ ] **Step 2: docker-compose env.** In `docker-compose.services.yml`, add to the `event-scheduling` service `environment` block (mirror the reminder-vars style):
```yaml
      # Calendar-sync (slice 5): background iCal busy-import poller.
      CALENDAR_SYNC_ENABLED: ${CALENDAR_SYNC_ENABLED:-true}
      CALENDAR_SYNC_INTERVAL_SECONDS: ${CALENDAR_SYNC_INTERVAL_SECONDS:-300}
      CALENDAR_SYNC_WINDOW_DAYS: ${CALENDAR_SYNC_WINDOW_DAYS:-62}
      CALENDAR_FETCH_TIMEOUT_SECONDS: ${CALENDAR_FETCH_TIMEOUT_SECONDS:-15}
```

- [ ] **Step 3: Docs.**
  - `event-scheduling/CLAUDE.md`: add the `calendar/` module (iCal-URL busy import), the background poller (3rd lifespan task, `CALENDAR_SYNC_ENABLED` toggle), the `CompositeBusyTimesSource` (slot engine + booking-create now subtract external busy), and the management endpoints `/api/v1/calendars`. Note it's additive; slot/booking code unchanged.
  - `event-scheduling/docs/DATA_MODEL.md`: document `external_calendar` + `external_calendar_event` (cache; delete+insert per sync; cascade).
  - `event-scheduling/docs/SERVICE_OVERVIEW.md`: mark slice 5 (Calendar sync — external busy-times via iCal URL) **Delivered**; note OAuth/export deferred.
  - Root `docs/architecture/ARCHITECTURE.md`: note event-scheduling now imports external calendar busy-times (iCal URL) into availability.
  - Every doc claim must be TRUE against the code (iCal URL only; import-only; poller-based; no OAuth/export).

- [ ] **Step 4: Re-run the gate** to confirm still green.

- [ ] **Step 5: Commit**
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
git add event-scheduling/CLAUDE.md event-scheduling/docs/DATA_MODEL.md event-scheduling/docs/SERVICE_OVERVIEW.md \
        docs/architecture/ARCHITECTURE.md docker-compose.services.yml
git commit -m "docs(calendar): document calendar-sync (iCal busy import) + compose env (slice 5)"
```

---

## Self-Review (completed during plan authoring)

**1. Spec coverage:** §1 architecture (calendar/ module + composite + poller) → Tasks 2–7. §2 migration (2 tables) → Task 1. §3 fetch+parse → Tasks 2 (parser) + 3 (client). §4 sync (poller+cache, mark_error keeps cache) → Task 6. §5 composite + DI swap → Task 5. §6 management API → Task 7. §7 error handling (fetch fail → mark_error keep cache; empty cache → []; sync endpoint 200 w/ last_error) → Tasks 6 (sync) + 7 (router). §8 deferred (OAuth/export/CalDAV/SSRF/webhooks/dedup/UI) → noted, not built. §9 tests → distributed across tasks. §10 config → Task 6. §11 DoR + docs/compose → Task 8.

**2. Placeholders:** All production code is complete. The Task 5 e2e test body is intentionally a "read test_slots_api.py and reuse its seeding" instruction with an explicit "do not leave `...` committed" gate — the seeding is repo-specific and safer to mirror than to guess; every other test + all module code is given in full. Boilerplate (dispatcher) mirrors the named reminder file with full code.

**3. Type consistency:** `BusyInterval`/`TimeWindow` reused everywhere (parser Task 2, external source Task 5, cache Task 4, composite Task 5). `ExternalCalendarDTO` fields (Task 4) consumed by read/write adapters (Task 4), sync (Task 6), schemas/router (Task 7). `ICalParser.expand(ics_bytes, window)` (Task 2) matches `IICalParser` Protocol (Task 3) + sync call (Task 6). `ICalClient.fetch(url)` (Task 3) matches `IICalClient` (Task 3) + sync (Task 6) + ioc (Task 7). `CalendarReadAdapter`/`CalendarWriteAdapter` methods (Task 4) consumed by sync (Task 6), dispatcher (Task 6), router (Task 7). `CompositeBusyTimesSource.get_busy(user_ids, window, exclude_booking_id=None)` (Task 5) matches the `BusyTimesSource` Protocol + booking-create call site. `sync_calendar(sql, client, parser, clock, calendar, window_days)` (Task 6) called by dispatcher (Task 6) + router (Task 7) with matching args.
