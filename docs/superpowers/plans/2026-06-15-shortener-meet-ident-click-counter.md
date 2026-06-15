# Shortener Meet-Ident + Click Counter Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give event-shortener Google-Meet-style idents (`xxx-xxx-xxx`) and a per-URL click counter incremented on each redirect, and show that count next to the meeting link on the admin booking-detail card.

**Architecture:** event-shortener changes its ident generator and adds a `click_count` column incremented atomically on the 307 redirect, plus a `GET /api/v1/urls/{ident}/stats` endpoint. event-admin gains a REST `ShortenerClient`, derives the ident from the stored `meeting_url`'s last path segment, fetches counts concurrently while assembling the booking detail (best-effort → `null` on any failure), and exposes `click_count` on meeting links. The frontend renders it.

**Tech Stack:** event-shortener & event-admin: Python 3.14, FastAPI, Dishka, raw `text()` SQL, Alembic, pytest. Frontend: React + TS + Vite, Vitest.

**Spec:** `docs/superpowers/specs/2026-06-15-shortener-meet-ident-click-counter-design.md`

**Conventions (every task):** No `elif`, avoid `else`. Ruff line length 120. `pre-commit` NOT installed → commit `--no-verify`. Each service is its OWN git repo — commit from inside it. Trailer:
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```
event-shortener tests need a local Postgres (`initdb`/`pg_ctl`) or `TEST_POSTGRES_DSN`; if neither exists the suite **skips** — that is acceptable, but prefer running where Postgres is available.

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `event-shortener/event_shortener/ident.py` | Modify | Meet-format generator |
| `event-shortener/tests/test_api.py`, `tests/test_controller.py` | Modify | ident-format assertions |
| `event-shortener/alembic/versions/0002_click_count.py` | Create | add `click_count` column |
| `event-shortener/event_shortener/dto/short_url.py` | Modify | `click_count` on DTO |
| `event-shortener/event_shortener/adapters/short_url_db.py` | Modify | select `click_count`, `increment_click` |
| `event-shortener/event_shortener/interfaces/shortener.py` | Modify | `increment_click`, `register_click` |
| `event-shortener/event_shortener/controllers/shortener.py` | Modify | `register_click` |
| `event-shortener/event_shortener/schemas/short_url.py` | Modify | `IdentStatsResponse` |
| `event-shortener/event_shortener/routes.py` | Modify | increment on 307, stats endpoint |
| `event-admin/event_admin/config.py` | Modify | `shortener_url`, `shortener_api_key` |
| `event-admin/event_admin/interfaces/shortener.py` | Create | `IShortenerClient` |
| `event-admin/event_admin/adapters/shortener_client.py` | Create | `ShortenerClient` |
| `event-admin/event_admin/ioc.py` | Modify | provide `ShortenerClient` |
| `event-admin/event_admin/dto/bookings.py` | Modify | `click_count` on meeting-link DTO |
| `event-admin/event_admin/schemas/bookings.py` | Modify | `click_count` on response |
| `event-admin/event_admin/routes.py` | Modify | enrich meeting links in `get_booking_details` |
| `event-admin/tests/conftest.py` | Modify | settings + `FakeShortenerClient` |
| `event-admin/tests/test_booking_meeting_click_count.py` | Create | enrichment tests |
| `docker-compose.yml`, `.env.example` | Modify | event-admin SHORTENER_* env |
| `event-admin-frontend/src/modules/bookings/types.ts` | Modify | `MeetingLink.click_count` |
| `event-admin-frontend/src/modules/bookings/BookingDetailsPage.tsx` | Modify | render count |
| docs | Modify | shortener + admin docs |

---

## Task 1: event-shortener — Meet-format ident

**Files:**
- Modify: `event-shortener/event_shortener/ident.py`
- Modify: `event-shortener/tests/test_api.py:18`, `event-shortener/tests/test_controller.py:58`

- [ ] **Step 1: Update the failing assertions**

In `tests/test_api.py`, add `import re` at the top (next to `import time`) and replace line 18:
```python
    assert len(ident) == 7
```
with:
```python
    assert re.fullmatch(r"[a-z]{3}-[a-z]{3}-[a-z]{3}", ident)
```
In `tests/test_controller.py`, add `import re` at the top and replace line 58:
```python
    assert len(ident) == 7
```
with:
```python
    assert re.fullmatch(r"[a-z]{3}-[a-z]{3}-[a-z]{3}", ident)
```

- [ ] **Step 2: Run them to confirm they FAIL**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-shortener && uv run pytest tests/test_controller.py -k generates -v`
Expected: FAIL (current ident is 7-char base62, no hyphens). (If the suite skips for lack of Postgres, run at least `test_controller.py` which uses fakes, not the DB.)

- [ ] **Step 3: Replace the generator** — replace the whole file `event_shortener/ident.py` with:

```python
import secrets

_ALPHABET = "abcdefghijklmnopqrstuvwxyz"
_GROUP_LENGTH = 3
_GROUP_COUNT = 3


def generate_ident() -> str:
    """Return a Google-Meet-style ident: three groups of three lowercase letters
    joined by '-' (e.g. 'qmk-rba-htz'), keyspace 26^9 ≈ 5.4e12.

    Uniqueness is enforced by the DB; the controller regenerates on a unique
    violation, so this only needs to be uniformly random, not collision-proof.
    """
    groups = (
        "".join(secrets.choice(_ALPHABET) for _ in range(_GROUP_LENGTH)) for _ in range(_GROUP_COUNT)
    )
    return "-".join(groups)
```

- [ ] **Step 4: Run the controller tests to confirm PASS**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-shortener && uv run pytest tests/test_controller.py -v && ruff check .`
Expected: pass, no lint errors. (Also run `tests/test_api.py` if Postgres is available.)

- [ ] **Step 5: Commit (event-shortener repo)**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-shortener
git add event_shortener/ident.py tests/test_api.py tests/test_controller.py
git commit --no-verify -m "feat(shortener): Google-Meet-style idents (xxx-xxx-xxx)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: event-shortener — click counter + stats endpoint

**Files:**
- Create: `event-shortener/alembic/versions/0002_click_count.py`
- Modify: `dto/short_url.py`, `adapters/short_url_db.py`, `interfaces/shortener.py`, `controllers/shortener.py`, `schemas/short_url.py`, `routes.py`
- Modify: `event-shortener/event_shortener/db/models.py` (ORM drift)
- Test: `event-shortener/tests/test_api.py`

- [ ] **Step 1: Write the migration** — create `alembic/versions/0002_click_count.py`:

```python
"""add click_count to short_urls.

Revision ID: 0002
Revises: 0001
Create Date: 2026-06-15 00:00:00.000000

"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "short_urls",
        sa.Column("click_count", sa.BigInteger(), nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("short_urls", "click_count")
```

- [ ] **Step 2: Add the failing redirect-increment + stats tests** — append to `tests/test_api.py`:

```python
def test_redirect_increments_click_count(client) -> None:
    now = time.time()
    client.post(
        SHORTEN_URL,
        json=_payload("ext-clicks", long_url="https://live.example", not_before=now - 60, expires_at=now + 3600),
    )
    ident = client.get("/api/v1/urls/external/ext-clicks").json()["ident"]

    assert client.get(f"/api/v1/urls/{ident}/stats").json()["click_count"] == 0
    for _ in range(3):
        assert client.get(f"/{ident}", follow_redirects=False).status_code == 307
    assert client.get(f"/api/v1/urls/{ident}/stats").json()["click_count"] == 3


def test_expired_redirect_does_not_increment(client) -> None:
    now = time.time()
    client.post(
        SHORTEN_URL,
        json=_payload("ext-exp", long_url="https://gone.example", not_before=now - 120, expires_at=now - 60),
    )
    ident = client.get("/api/v1/urls/external/ext-exp").json()["ident"]
    assert client.get(f"/{ident}", follow_redirects=False).status_code == 410
    assert client.get(f"/api/v1/urls/{ident}/stats").json()["click_count"] == 0


def test_stats_unknown_ident_404(client) -> None:
    assert client.get("/api/v1/urls/abc-def-ghi/stats").status_code == 404
```

- [ ] **Step 3: Run them to confirm FAIL**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-shortener && uv run pytest tests/test_api.py -k "click_count or stats or increment" -v`
Expected: FAIL (no `click_count` column / no stats route). (Skips if no Postgres — then this task can only be verified where Postgres is available; note it in the report.)

- [ ] **Step 4: Add `click_count` to the DTO** — in `dto/short_url.py`, add a trailing defaulted field to `ShortUrlDTO`:
```python
@dataclass(frozen=True)
class ShortUrlDTO:
    id: int
    ident: str
    external_id: str
    long_url: str
    not_before: datetime | None
    expires_at: datetime | None
    created_at: datetime
    updated_at: datetime
    click_count: int = 0
```

- [ ] **Step 5: DB adapter — select the column + increment** — in `adapters/short_url_db.py`:

Replace `_COLUMNS`:
```python
_COLUMNS = "id, ident, external_id, long_url, not_before, expires_at, created_at, updated_at"
```
with:
```python
_COLUMNS = "id, ident, external_id, long_url, not_before, expires_at, created_at, updated_at, click_count"
```
In `_from_row`, add the field to the returned `ShortUrlDTO(...)`:
```python
        updated_at=row["updated_at"],
        click_count=row["click_count"],
    )
```
Add an `increment_click` method (after `get_by_ident`):
```python
    async def increment_click(self, ident: str) -> None:
        await self._sql.execute(
            "UPDATE short_urls SET click_count = click_count + 1 WHERE ident = :ident",
            {"ident": ident},
        )
```
NOTE: confirm `ISqlExecutor` has an `execute` method; if the executor only exposes `fetch_one`/`fetch_all`, use `fetch_one` with a `RETURNING id` and ignore the result. Check `event_shortener/interfaces/sql.py` and `adapters/sql.py` and match the available method.

- [ ] **Step 6: Interfaces** — in `interfaces/shortener.py`, add to `IShortUrlDBAdapter`:
```python
    async def increment_click(self, ident: str) -> None: ...
```
and to `IShortenerController`:
```python
    async def register_click(self, ident: str) -> None: ...
```

- [ ] **Step 7: Controller** — in `controllers/shortener.py`, add to `ShortenerController`:
```python
    async def register_click(self, ident: str) -> None:
        await self._db.increment_click(ident)
```

- [ ] **Step 8: Stats response schema** — in `schemas/short_url.py`, append:
```python
class IdentStatsResponse(BaseModel):
    ident: str
    click_count: int
```

- [ ] **Step 9: Routes — increment on 307 + stats endpoint** — in `routes.py`:

Import the new schema (extend the existing import):
```python
from event_shortener.schemas.short_url import IdentResponse, IdentStatsResponse, ShortenRequest
```
Add a stats route to `api_router` (after `delete_by_external_id`):
```python
@api_router.get("/{ident}/stats", response_model=IdentStatsResponse)
async def ident_stats(ident: str, controller: FromDishka[IShortenerController]) -> IdentStatsResponse:
    record = await controller.resolve(ident)
    if record is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"ident {ident!r} not found")
    return IdentStatsResponse(ident=record.ident, click_count=record.click_count)
```
In the `redirect` handler, increment on the in-window path. Replace:
```python
    metrics.REDIRECTS_TOTAL.labels(result="ok").inc()
    return RedirectResponse(url=record.long_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
```
with:
```python
    try:
        await controller.register_click(ident)
    except Exception:
        logger.exception("Failed to record click; serving redirect anyway", ident=ident)
    metrics.REDIRECTS_TOTAL.labels(result="ok").inc()
    return RedirectResponse(url=record.long_url, status_code=status.HTTP_307_TEMPORARY_REDIRECT)
```
NOTE: the `/{ident}/stats` route is on `api_router` (prefix `/api/v1/urls`, registered before the catch-all `/{ident}` redirect), so it is not swallowed by the redirect. Verify route registration order in `routes.py` is unchanged (api → ops → redirect).

- [ ] **Step 10: ORM drift** — in `db/models.py`, add to `ShortUrl` (keeps Alembic autogenerate honest):
```python
    click_count: Mapped[int] = mapped_column(BigInteger, nullable=False, server_default=text("0"))
```
(after `updated_at`; `BigInteger` and `text` are already imported).

- [ ] **Step 11: Run the shortener suite + lint**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-shortener && uv run pytest -q && ruff check .`
Expected: pass (or skip if no Postgres), no lint errors. The new tests must pass where Postgres is available.

- [ ] **Step 12: Commit (event-shortener repo)**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-shortener
git add alembic/versions/0002_click_count.py event_shortener/ tests/test_api.py
git commit --no-verify -m "feat(shortener): click_count column, 307 increment, /stats endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: event-admin — ShortenerClient + meeting-link click_count

**Files:**
- Modify: `config.py`, `ioc.py`, `dto/bookings.py`, `schemas/bookings.py`, `routes.py`
- Create: `interfaces/shortener.py`, `adapters/shortener_client.py`
- Modify: `tests/conftest.py`; Create `tests/test_booking_meeting_click_count.py`
- Modify: `docker-compose.yml`, `.env.example` (repo root)

- [ ] **Step 1: Config** — in `event_admin/config.py`, add (next to `notifier_service_url`):
```python
    shortener_url: AnyHttpUrl = Field(strict=True)
    shortener_api_key: str = Field(strict=True)
```

- [ ] **Step 2: Interface** — create `event_admin/interfaces/shortener.py`:
```python
"""Interface for the URL-shortener stats client."""

from typing import Protocol


class IShortenerClient(Protocol):
    async def get_click_count(self, ident: str) -> int | None: ...
```

- [ ] **Step 3: Client adapter** — create `event_admin/adapters/shortener_client.py`:
```python
"""HTTP client for event-shortener's stats endpoint (best-effort)."""

import httpx
import structlog
from httpx import AsyncClient

logger = structlog.get_logger(__name__)


class ShortenerClient:
    def __init__(self, *, http_client: AsyncClient, api_key: str) -> None:
        self._client = http_client
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def get_click_count(self, ident: str) -> int | None:
        """Return the click count for an ident, or None on any failure.

        The shortener is never on the booking-detail critical path, so a missing
        ident, an unreachable shortener, or a 5xx all degrade to None.
        """
        try:
            response = await self._client.get(f"/api/v1/urls/{ident}/stats", headers=self._headers)
        except httpx.HTTPError as exc:
            logger.warning("Shortener unreachable for click count", ident=ident, error=str(exc))
            return None
        if response.status_code != 200:
            return None
        return response.json().get("click_count")
```

- [ ] **Step 4: DI** — in `event_admin/ioc.py`, mirror `provide_notifier_client`. Add imports near the other adapter/interface imports:
```python
from event_admin.adapters.shortener_client import ShortenerClient
from event_admin.interfaces.shortener import IShortenerClient
```
Add a provider (APP scope, like `provide_notifier_client`):
```python
    @provide(scope=Scope.APP)
    async def provide_shortener_client(self, settings: Settings) -> AsyncGenerator[IShortenerClient]:
        async with AsyncClient(base_url=str(settings.shortener_url)) as http_client:
            yield ShortenerClient(http_client=http_client, api_key=settings.shortener_api_key)
```
(match the exact decorator/typing style of the existing `provide_notifier_client`; it is an `AsyncGenerator`.)

- [ ] **Step 5: DTO field** — in `event_admin/dto/bookings.py`, add a trailing defaulted field to `BookingMeetingLinkItemDto`:
```python
@dataclass(slots=True, frozen=True)
class BookingMeetingLinkItemDto:
    id: int
    participant: ParticipantDto
    meeting_url: str
    source_event_id: str | None
    occurred_at: datetime
    created_at: datetime
    updated_at: datetime
    click_count: int | None = None
```

- [ ] **Step 6: Response field** — in `event_admin/schemas/bookings.py`, add `click_count` to `BookingMeetingLinkItemResponse` and its `from_dto`:
```python
class BookingMeetingLinkItemResponse(BaseModel):
    id: int
    participant: ParticipantResponse
    meeting_url: str
    created_at: datetime
    click_count: int | None = None

    @classmethod
    def from_dto(cls, dto: BookingMeetingLinkItemDto) -> BookingMeetingLinkItemResponse:
        return cls(
            id=dto.id,
            participant=ParticipantResponse.from_dto(dto.participant),
            meeting_url=dto.meeting_url,
            created_at=dto.created_at,
            click_count=dto.click_count,
        )
```

- [ ] **Step 7: Write the failing enrichment test** — create `tests/test_booking_meeting_click_count.py`:
```python
"""GET /bookings/{uid} enriches meeting links with shortener click counts."""

import uuid

from tests.conftest import make_booking_details, make_meeting_link


async def test_meeting_links_get_click_count(client, admin_headers, fakes) -> None:
    cid = uuid.uuid4()
    link = make_meeting_link(user_id=cid, meeting_url="http://event-shortener:8888/qmk-rba-htz")
    fakes.bookings_controller.bookings["b1"] = make_booking_details("b1", meeting_links=(link,))
    fakes.shortener.counts["qmk-rba-htz"] = 5

    resp = await client.get("/bookings/b1", headers=admin_headers)
    assert resp.status_code == 200
    links = resp.json()["meeting_links"]
    assert len(links) == 1
    assert links[0]["click_count"] == 5


async def test_meeting_link_click_count_null_when_shortener_unknown(client, admin_headers, fakes) -> None:
    cid = uuid.uuid4()
    link = make_meeting_link(user_id=cid, meeting_url="http://event-shortener:8888/zzz-zzz-zzz")
    fakes.bookings_controller.bookings["b1"] = make_booking_details("b1", meeting_links=(link,))
    # no count registered → graceful null

    resp = await client.get("/bookings/b1", headers=admin_headers)
    assert resp.status_code == 200
    assert resp.json()["meeting_links"][0]["click_count"] is None
```

- [ ] **Step 8: Wire the fake + settings into conftest** — in `tests/conftest.py`:

Add to the settings `defaults` dict (Step from §config; near the other URLs):
```python
        "shortener_url": "http://shortener.test",
        "shortener_api_key": "shortener-key-0123456789abcdef",
```
Add a `FakeShortenerClient` class (near `FakeNotifierClient`):
```python
class FakeShortenerClient:
    def __init__(self) -> None:
        self.counts: dict[str, int] = {}

    async def get_click_count(self, ident: str) -> int | None:
        return self.counts.get(ident)
```
In the `Fakes` class `__init__`, add `self.shortener = FakeShortenerClient()`. In `FakeProvider`, add a provider mirroring `provide_users_client`:
```python
    @provide(scope=Scope.APP)
    def provide_shortener_client(self) -> IShortenerClient:
        return self._fakes.shortener
```
Add the needed imports to conftest: `from event_admin.interfaces.shortener import IShortenerClient`.

- [ ] **Step 9: Run, confirm FAIL** (route not enriching yet)

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin && uv run pytest tests/test_booking_meeting_click_count.py -v`
Expected: FAIL — `click_count` is `None` even when a count is registered (route doesn't call the shortener yet), so `test_meeting_links_get_click_count` fails.

- [ ] **Step 10: Enrich in the route** — in `event_admin/routes.py`:

Add imports (top of file):
```python
import asyncio
from dataclasses import replace
from urllib.parse import urlsplit
```
and the interface:
```python
from event_admin.interfaces.shortener import IShortenerClient
```
Add two module-level helpers (near the other `_…` helpers):
```python
def _ident_from_meeting_url(meeting_url: str) -> str | None:
    """The shortener ident is the last non-empty path segment of the short URL."""
    segment = urlsplit(meeting_url).path.rstrip("/").rsplit("/", 1)[-1]
    return segment or None


async def _link_click_count(shortener: IShortenerClient, meeting_url: str) -> int | None:
    ident = _ident_from_meeting_url(meeting_url)
    if ident is None:
        return None
    return await shortener.get_click_count(ident)
```
Replace the existing `get_booking_details` route body so it injects the shortener and enriches the meeting links:
```python
@bookings_router.get(
    "/{booking_uid}",
    response_model=BookingDetailsResponse,
    summary="Get booking details",
    description="Get full booking details including notifications, meeting links, and event history.",
)
async def get_booking_details(
    booking_uid: Annotated[str, Path(min_length=1)],
    controller: FromDishka[IBookingsController],
    shortener: FromDishka[IShortenerClient],
) -> BookingDetailsResponse:
    booking_details_dto = await controller.get_booking_details(booking_uid)
    if booking_details_dto is None:
        raise http_error(
            status.HTTP_404_NOT_FOUND,
            "booking_not_found",
            f"Booking with uid={booking_uid!r} not found",
        )
    links = booking_details_dto.meeting_links
    if links:
        counts = await asyncio.gather(*(_link_click_count(shortener, link.meeting_url) for link in links))
        enriched = tuple(replace(link, click_count=count) for link, count in zip(links, counts, strict=True))
        booking_details_dto = replace(booking_details_dto, meeting_links=enriched)
    return BookingDetailsResponse.from_dto(booking_details_dto)
```

- [ ] **Step 11: Run the tests + full suite + lint**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin && uv run pytest -q && ruff check .`
Expected: the two new tests pass; the full suite stays green (existing booking-detail tests now see `click_count=null`, which is additive); no lint errors.

- [ ] **Step 12: Compose + .env.example** — add SHORTENER env to the **event-admin** service in `docker-compose.yml` (next to its `NOTIFIER_SERVICE_URL`):
```yaml
      SHORTENER_URL: ${SHORTENER_URL:-http://event-shortener:8888}
      SHORTENER_API_KEY: ${SHORTENER_API_KEY:-dev-shortify-api-key-8c4e1f7b2a93d650}
```
Add the same two keys to `.env.example` if a SHORTENER section isn't already there for event-admin (event-booking already defines `SHORTENER_URL`/`SHORTENER_API_KEY`; the `${...}` defaults are shared, so this is only documentation).

- [ ] **Step 13: Commit (event-admin repo + root for compose)**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-admin
git add event_admin/ tests/conftest.py tests/test_booking_meeting_click_count.py
git commit --no-verify -m "feat(admin): attach shortener click_count to booking meeting links

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
cd /Users/alexandrlelikov/PycharmProjects/events
git add docker-compose.yml .env.example
git commit --no-verify -m "chore: event-admin SHORTENER_URL/API_KEY env

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: event-admin-frontend — render the count

**Files:**
- Modify: `src/modules/bookings/types.ts`, `src/modules/bookings/BookingDetailsPage.tsx`

- [ ] **Step 1: Type** — in `types.ts`, add to `MeetingLink`:
```typescript
export type MeetingLink = {
  id: number
  participant: Participant
  meeting_url: string
  created_at: string
  click_count: number | null
}
```

- [ ] **Step 2: Render the count** — in `BookingDetailsPage.tsx`, in the meeting-links `<li>` (around line 588-593), add the count after the date span. Replace:
```tsx
                  <li key={link.id}>
                    <a href={link.meeting_url} target="_blank" rel="noreferrer">
                      <UserInfo userId={link.participant.user_id} fallback="Ссылка" variant="name" />
                    </a>{' '}
                    <span className="muted">· {formatDateTime(link.created_at, timeZone)}</span>
                  </li>
```
with:
```tsx
                  <li key={link.id}>
                    <a href={link.meeting_url} target="_blank" rel="noreferrer">
                      <UserInfo userId={link.participant.user_id} fallback="Ссылка" variant="name" />
                    </a>{' '}
                    <span className="muted">· {formatDateTime(link.created_at, timeZone)}</span>{' '}
                    <span className="muted">· переходов: {link.click_count ?? '—'}</span>
                  </li>
```

- [ ] **Step 3: Type-check + full suite**

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend && npx tsc --noEmit && npx vitest run`
Expected: no type errors (any test fixtures building a `MeetingLink` may need `click_count` — if `tsc` flags a test/fixture, add `click_count: null` to it); all tests green.

- [ ] **Step 4: Commit (frontend repo)**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend
git add src/modules/bookings/types.ts src/modules/bookings/BookingDetailsPage.tsx
git commit --no-verify -m "feat(frontend): show meeting-link click count on booking detail

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Docs

**Files:**
- Modify: `event-shortener/docs/API_CONTRACTS.md` (+ `CLAUDE.md` if it documents the ident format)
- Modify: `event-admin/docs/API_CONTRACTS.md`, `event-admin/CLAUDE.md`

- [ ] **Step 1: event-shortener docs** — document the new ident format (`xxx-xxx-xxx`, lowercase, new links only), the `click_count` column, that every 307 redirect increments it (410/404 do not), and the new `GET /api/v1/urls/{ident}/stats` endpoint (`{ident, click_count}`, 404 unknown).

- [ ] **Step 2: event-admin docs** — in `docs/API_CONTRACTS.md`, note that `GET /bookings/{uid}` meeting links now include `click_count` (int | null, fetched from event-shortener, null when unavailable). In `CLAUDE.md`, add `ShortenerClient`/`IShortenerClient` to the adapters list and the `SHORTENER_URL`/`SHORTENER_API_KEY` settings.

- [ ] **Step 3: Commit (each repo)**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-shortener
git add docs/API_CONTRACTS.md CLAUDE.md
git commit --no-verify -m "docs(shortener): meet ident + click_count + stats endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
cd /Users/alexandrlelikov/PycharmProjects/events/event-admin
git add docs/API_CONTRACTS.md CLAUDE.md
git commit --no-verify -m "docs(admin): meeting-link click_count from shortener

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Live e2e verification (no code)

- [ ] **Step 1: Rebuild**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
docker compose up -d --build event-shortener event-admin event-admin-frontend
docker compose restart event-admin-frontend
```

- [ ] **Step 2: New ident format + counter end-to-end.** Create a fresh shortened link via the shortener API (api-key gated), confirm the returned `ident` matches `xxx-xxx-xxx`, hit `GET /{ident}` a few times (expect 307), then `GET /api/v1/urls/{ident}/stats` and confirm `click_count` equals the number of redirects. Use:
```bash
KEY=dev-shortify-api-key-8c4e1f7b2a93d650
docker compose exec -T event-shortener sh -c "curl -s -X POST -H \"Authorization: Bearer $KEY\" -H 'Content-Type: application/json' -d '{\"long_url\":\"https://example.org/x\",\"external_id\":\"e2e-1\",\"expires_at\":null,\"not_before\":null}' http://localhost:8888/api/v1/urls/shorten"
# take the ident, then:
docker compose exec -T event-shortener sh -c "curl -s -o /dev/null -w '%{http_code}\n' http://localhost:8888/<ident>"   # ×3, expect 307
docker compose exec -T event-shortener sh -c "curl -s -H \"Authorization: Bearer $KEY\" http://localhost:8888/api/v1/urls/<ident>/stats"
```
Record the ident and the final count.

- [ ] **Step 3: Booking card shows the count.** Generate a future booking through the system (`uv run scripts/calcom_sim.py create --starts-in 3h`) so event-booking shortens a real meeting link; open `http://localhost:3000/bookings/<uid>` and confirm the meeting link shows `· переходов: N`. (Optionally click the link / hit the short URL and re-open to see the count rise.)

---

## Self-Review (against the spec)

**Spec coverage:**
- ident `xxx-xxx-xxx`, new links only → Task 1. ✅
- `click_count` column + 307-only atomic increment + `/stats` endpoint → Task 2. ✅
- event-admin REST `ShortenerClient` + config + ioc → Task 3 (Steps 1–4). ✅
- ident from last path segment, concurrent, graceful-null, DTO/schema fields → Task 3 (Steps 5–11). ✅
- frontend `click_count` + render `переходов: N`/`—` → Task 4. ✅
- compose/env → Task 3 (Step 12). ✅ Docs → Task 5. ✅ E2E → Task 6. ✅

**Type/signature consistency:**
- `ShortUrlDTO.click_count: int = 0` (Task 2.4) → selected by `_COLUMNS`/`_from_row` (2.5), read by `/stats` (2.9).
- `IShortUrlDBAdapter.increment_click` / `IShortenerController.register_click` (2.6) → implemented (2.5, 2.7) → called in redirect (2.9).
- `IShortenerClient.get_click_count(ident) -> int | None` (3.2) → impl (3.3), fake (3.8), used in `_link_click_count` (3.10).
- `BookingMeetingLinkItemDto.click_count: int | None = None` (3.5) → set via `dataclasses.replace` (3.10) → surfaced by `from_dto` (3.6) → consumed by frontend `MeetingLink.click_count` (4.1).
- Endpoint path `/api/v1/urls/{ident}/stats` identical in shortener route (2.9) and admin client (3.3).

**Placeholder scan:** none — every code step is complete. Two NOTEs (the `ISqlExecutor.execute` method name in 2.5/Step 5; matching the exact `provide_notifier_client` decorator style in 3.4) are verify-against-reality instructions, not placeholders.
