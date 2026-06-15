# Admin Send-Client-Reminder Button Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an admin booking-detail button that emails the client the `BOOKING_REMINDER` meeting reminder on demand, addressed to the client's CURRENT email resolved from event-users.

**Architecture:** A new event-admin endpoint `POST /bookings/{booking_uid}/send-client-reminder` (orchestrated in the route handler, exactly like the existing `reassign-client`) resolves the current client email via `IUsersClient.get_user`, gates to future+active bookings with a linked account, and publishes a `notification.send_requested` CloudEvent (`source="admin"`) via the existing `IEventPublisher`. event-receiver normalizes/routes it to the notifier unchanged; the per-role `(BOOKING_REMINDER, client, *)` bindings decide channels. The frontend adds an eligibility-gated, confirm-guarded button.

**Tech Stack:** event-admin: Python 3.14, FastAPI, Dishka, pytest/anyio (`FakeProvider`, no real DB/network). event-admin-frontend: React + TypeScript + Vite, Vitest + Testing Library.

**Spec:** `docs/superpowers/specs/2026-06-15-admin-send-client-reminder-design.md`

**Conventions (every task):** No `elif`, avoid `else` (early returns / guard clauses). Ruff line length 120. `pre-commit` is NOT installed — commit with `--no-verify`. Each service is its OWN git repo — commit from inside that service dir. Commit trailer:
```
Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>
```

---

## File Structure

| File | Change | Responsibility |
|---|---|---|
| `event-admin/event_admin/routes.py` | Modify | New `send_client_reminder` route + 3 module-level helpers |
| `event-admin/tests/conftest.py` | Modify | Extend `make_booking_details` with overrides; add `make_meeting_link` |
| `event-admin/tests/test_send_client_reminder.py` | Create | Endpoint tests (gating, resolution, block, publish payload, 502) |
| `event-admin/docs/API_CONTRACTS.md` | Modify | Document the new endpoint + error codes |
| `event-admin/CLAUDE.md` | Modify | Mention the new booking action |
| `event-admin-frontend/src/modules/bookings/bookingsApi.ts` | Modify | `sendClientReminder(bookingUid)` |
| `event-admin-frontend/src/modules/bookings/BookingDetailsPage.tsx` | Modify | Button + eligibility + confirm + result |
| `event-admin-frontend/src/modules/bookings/BookingDetailsPage.test.tsx` | Create | Button states + confirm + POST |

---

## Task 1: event-admin endpoint

**Files:**
- Modify: `event-admin/tests/conftest.py`
- Create: `event-admin/tests/test_send_client_reminder.py`
- Modify: `event-admin/event_admin/routes.py`

### Step 1: Extend test helpers in `conftest.py`

The current `make_booking_details` hard-codes `start_time=NOW`, `current_status="created"`, a random client `user_id`, and `meeting_links=()`. Replace it with an override-friendly version (non-breaking: no-arg calls behave as before), and add a meeting-link factory. Locate the existing `def make_booking_details(...)` (around line 68) and replace it with:

```python
_UNSET: object = object()


def make_booking_details(
    booking_uid: str = "book-1",
    *,
    start_time: dt.datetime | None = NOW,
    end_time: dt.datetime | None = NOW,
    current_status: str | None = "created",
    has_client: bool = True,
    client_user_id: object = _UNSET,
    meeting_links: tuple[BookingMeetingLinkItemDto, ...] = (),
) -> BookingDetailsDto:
    client_participant = None
    if has_client:
        resolved = uuid.uuid4() if client_user_id is _UNSET else client_user_id
        client_participant = ParticipantDto(user_id=resolved)  # type: ignore[arg-type]
    return BookingDetailsDto(
        id=1,
        booking_uid=booking_uid,
        first_seen_at=NOW,
        last_seen_at=NOW,
        start_time=start_time,
        end_time=end_time,
        current_status=current_status,
        created_at=NOW,
        updated_at=NOW,
        current_organizer_participant=ParticipantDto(user_id=uuid.uuid4()),
        current_client_participant=client_participant,
        organizer_history=(),
        meeting_links=meeting_links,
        email_notifications=(),
        telegram_notifications=(),
        chat_events=(),
        video_events=(),
        lifecycle_events=(),
    )


def make_meeting_link(*, user_id: uuid.UUID, meeting_url: str) -> BookingMeetingLinkItemDto:
    return BookingMeetingLinkItemDto(
        id=1,
        participant=ParticipantDto(user_id=user_id),
        meeting_url=meeting_url,
        source_event_id=None,
        occurred_at=NOW,
        created_at=NOW,
        updated_at=NOW,
    )
```

Ensure `BookingMeetingLinkItemDto` is imported at the top of `conftest.py` (add it to the existing `from event_admin.dto.bookings import (...)` block if missing). Confirm `dt` is the datetime alias already used by `NOW` (it is — `NOW` is defined with it); if the file imports `datetime` differently, match that.

### Step 2: Write the failing endpoint tests — create `tests/test_send_client_reminder.py`

```python
"""POST /bookings/{uid}/send-client-reminder: resolve current email, gate, publish."""

import uuid
from datetime import UTC, datetime, timedelta

from event_admin.errors import EventPublishError
from tests.conftest import make_booking_details, make_meeting_link

FUTURE = datetime.now(UTC) + timedelta(days=1)
PAST = datetime.now(UTC) - timedelta(days=1)


def _client_user(email: str = "current@example.com") -> dict:
    return {"id": str(uuid.uuid4()), "email": email, "name": "Иван", "role": "client", "time_zone": "Europe/Moscow"}


async def test_unknown_booking_returns_404_and_publishes_nothing(client, admin_headers, fakes) -> None:
    response = await client.post("/bookings/ghost/send-client-reminder", headers=admin_headers)
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "booking_not_found"
    assert fakes.publisher.published == []


async def test_no_client_participant_returns_409(client, admin_headers, fakes) -> None:
    fakes.bookings_controller.bookings["b1"] = make_booking_details("b1", has_client=False, start_time=FUTURE)
    response = await client.post("/bookings/b1/send-client-reminder", headers=admin_headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "no_client_on_booking"
    assert fakes.publisher.published == []


async def test_past_booking_not_eligible(client, admin_headers, fakes) -> None:
    fakes.bookings_controller.bookings["b1"] = make_booking_details("b1", start_time=PAST)
    response = await client.post("/bookings/b1/send-client-reminder", headers=admin_headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "booking_not_eligible"
    assert fakes.publisher.published == []


async def test_cancelled_booking_not_eligible(client, admin_headers, fakes) -> None:
    fakes.bookings_controller.bookings["b1"] = make_booking_details("b1", start_time=FUTURE, current_status="cancelled")
    response = await client.post("/bookings/b1/send-client-reminder", headers=admin_headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "booking_not_eligible"
    assert fakes.publisher.published == []


async def test_client_without_account_is_blocked(client, admin_headers, fakes) -> None:
    fakes.bookings_controller.bookings["b1"] = make_booking_details(
        "b1", start_time=FUTURE, client_user_id=None
    )
    response = await client.post("/bookings/b1/send-client-reminder", headers=admin_headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "client_has_no_account"
    assert fakes.publisher.published == []


async def test_client_not_found_in_users_returns_409(client, admin_headers, fakes) -> None:
    cid = uuid.uuid4()
    fakes.bookings_controller.bookings["b1"] = make_booking_details("b1", start_time=FUTURE, client_user_id=cid)
    # users_by_id intentionally empty → FakeUsersClient.get_user raises 404
    response = await client.post("/bookings/b1/send-client-reminder", headers=admin_headers)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "client_not_found"
    assert fakes.publisher.published == []


async def test_success_publishes_reminder_with_current_email(client, admin_headers, fakes) -> None:
    cid = uuid.uuid4()
    link = make_meeting_link(user_id=cid, meeting_url="https://meet/abc")
    fakes.bookings_controller.bookings["b1"] = make_booking_details(
        "b1", start_time=FUTURE, end_time=FUTURE, client_user_id=cid, meeting_links=(link,)
    )
    fakes.users_client.users_by_id[cid] = _client_user("current@example.com")

    response = await client.post("/bookings/b1/send-client-reminder", headers=admin_headers)

    assert response.status_code == 202
    assert response.json() == {"status": "accepted", "email": "current@example.com"}
    assert len(fakes.publisher.published) == 1
    event = fakes.publisher.published[0]
    assert event["source"] == "admin"
    assert event["event_type"] == "notification.send_requested"
    data = event["data"]
    assert data["booking_id"] == "b1"
    assert data["trigger_event"] == "BOOKING_REMINDER"
    assert data["recipients"] == [{"email": "current@example.com", "role": "client", "locale": None}]
    td = data["template_data"]
    assert td["client_email"] == "current@example.com"
    assert td["client_name"] == "Иван"
    assert td["meeting_url"] == "https://meet/abc"
    assert "requested_at" in td and td["booking_uid"] == "b1"


async def test_publish_failure_maps_to_502(client, admin_headers, fakes) -> None:
    cid = uuid.uuid4()
    fakes.bookings_controller.bookings["b1"] = make_booking_details("b1", start_time=FUTURE, client_user_id=cid)
    fakes.users_client.users_by_id[cid] = _client_user()
    fakes.publisher.error = EventPublishError(
        event_type="notification.send_requested", source="admin", upstream_status=None, detail="unreachable"
    )
    response = await client.post("/bookings/b1/send-client-reminder", headers=admin_headers)
    assert response.status_code == 502
```

NOTE: confirm the fixture names match this repo's conftest — the reassign tests use `client`, `admin_headers`, `fakes` with `fakes.bookings_controller`, `fakes.users_client`, `fakes.publisher`. If `FakeEventPublisher` does not yet honor a `.error` attribute, check `test_reassign_client.py::test_reassign_publish_failure_maps_to_502` — it already sets `fakes.publisher.error`, so the support exists.

### Step 3: Run the tests to confirm they FAIL

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin && uv run pytest tests/test_send_client_reminder.py -v`
Expected: failures — the route does not exist yet (404 for all, or collection errors if `make_meeting_link` import missing — fix the import in Step 1 if so).

### Step 4: Implement the route + helpers in `event_admin/routes.py`

(a) Extend imports. The file already imports `from typing import Annotated`; change it to also bring `Any`:
```python
from typing import Annotated, Any
```
Add a datetime import near the top (after the stdlib imports `import hmac` / `import uuid`):
```python
from datetime import UTC, datetime
```

(b) Add module-level helpers just above the `notifications_router` definition (anywhere at module scope after `_notifier_proxy_error`):
```python
_REMINDER_INELIGIBLE_STATUSES = frozenset({"cancelled", "completed", "no_show"})


def _reminder_eligible(details: Any) -> bool:
    """A reminder is sendable only for a future booking that is not cancelled/finished."""
    if details.start_time is None or details.start_time <= datetime.now(UTC):
        return False
    return details.current_status not in _REMINDER_INELIGIBLE_STATUSES


def _client_meeting_url(details: Any, client_user_id: uuid.UUID) -> str:
    """The client's meeting link if present, else the most recent link, else empty."""
    links = details.meeting_links
    if not links:
        return ""
    for link in links:
        if link.participant.user_id == client_user_id:
            return link.meeting_url
    return max(links, key=lambda link: link.created_at).meeting_url


def _build_reminder_payload(details: Any, client_user: dict[str, Any]) -> dict[str, Any]:
    email = client_user["email"]
    client_user_id = details.current_client_participant.user_id
    return {
        "booking_id": details.booking_uid,
        "trigger_event": "BOOKING_REMINDER",
        "recipients": [{"email": email, "role": "client", "locale": None}],
        "template_data": {
            "booking_uid": details.booking_uid,
            "start_time": details.start_time.isoformat() if details.start_time else None,
            "end_time": details.end_time.isoformat() if details.end_time else None,
            "client_name": client_user.get("name") or "",
            "client_email": email,
            "meeting_url": _client_meeting_url(details, client_user_id),
            "requested_at": datetime.now(UTC).isoformat(),
        },
    }
```

(c) Add the route on the existing `bookings_router` (place it after `reassign_booking_client`):
```python
@bookings_router.post(
    "/{booking_uid}/send-client-reminder",
    status_code=status.HTTP_202_ACCEPTED,
    summary="Send the client a meeting reminder",
    description="Resolve the client's CURRENT email from event-users and publish a "
    "BOOKING_REMINDER notification command for the client.",
)
async def send_client_reminder(
    booking_uid: str,
    controller: FromDishka[IBookingsController],
    client: FromDishka[IUsersClient],
    publisher: FromDishka[IEventPublisher],
    user: Annotated[TokenPayload, Depends(require_admin)],
) -> dict[str, str]:
    details = await controller.get_booking_details(booking_uid)
    if details is None:
        raise http_error(
            status.HTTP_404_NOT_FOUND, "booking_not_found", f"Booking with uid={booking_uid!r} not found"
        )

    client_participant = details.current_client_participant
    if client_participant is None:
        raise http_error(status.HTTP_409_CONFLICT, "no_client_on_booking", "Booking has no client participant")

    if not _reminder_eligible(details):
        raise http_error(
            status.HTTP_409_CONFLICT,
            "booking_not_eligible",
            "Reminder can only be sent for a future, active booking",
        )

    if client_participant.user_id is None:
        raise http_error(
            status.HTTP_409_CONFLICT,
            "client_has_no_account",
            "Client has no linked account; current email cannot be resolved",
        )

    try:
        client_user = await client.get_user(client_participant.user_id)
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 404:
            raise http_error(status.HTTP_409_CONFLICT, "client_not_found", "Client account not found") from exc
        raise _users_proxy_error(exc) from exc

    payload = _build_reminder_payload(details, client_user)
    await publisher.publish(source="admin", event_type="notification.send_requested", data=payload)
    email = client_user["email"]
    logger.info("client_reminder_sent", booking_uid=booking_uid, email=email, requested_by=user.sub)
    return {"status": "accepted", "email": email}
```

### Step 5: Run the tests to confirm they PASS

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin && uv run pytest tests/test_send_client_reminder.py -v`
Expected: 8 passed.

### Step 6: Run the full event-admin suite + lint

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin && uv run pytest -q && ruff check .`
Expected: all pass (the extended `make_booking_details` is backward-compatible), no lint errors.

### Step 7: Commit (event-admin repo)

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-admin
git add event_admin/routes.py tests/conftest.py tests/test_send_client_reminder.py
git commit --no-verify -m "feat(admin): send-client-reminder endpoint (current email + BOOKING_REMINDER)

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: event-admin-frontend button

**Files:**
- Modify: `event-admin-frontend/src/modules/bookings/bookingsApi.ts`
- Modify: `event-admin-frontend/src/modules/bookings/BookingDetailsPage.tsx`
- Create: `event-admin-frontend/src/modules/bookings/BookingDetailsPage.test.tsx`

### Step 1: Add the API function in `bookingsApi.ts`

Append:
```typescript
export type SendClientReminderResult = {
  status: string
  email: string
}

export function sendClientReminder(bookingUid: string): Promise<SendClientReminderResult> {
  return apiRequest<SendClientReminderResult>(
    `/bookings/${encodeURIComponent(bookingUid)}/send-client-reminder`,
    { method: 'POST' },
  )
}
```
(Confirm `apiRequest` accepts `{ method: 'POST' }` with no body — it does; `putBinding` in the notifications module calls it with `{ method: 'PUT', body }`, and other callers omit `body`.)

### Step 2: Write the failing component test — create `BookingDetailsPage.test.tsx`

Model the mock/render style on `src/modules/notifications/NotificationsPage.test.tsx` (same repo). Mock `./bookingsApi.ts`, render `<BookingDetailsPage />`, and cover: button disabled when ineligible, enabled+confirm+POST when eligible. Use `window.confirm` spy.

```tsx
import { describe, expect, it, vi, beforeEach } from 'vitest'
import { render, screen, waitFor } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { BookingDetailsPage } from './BookingDetailsPage.tsx'
import * as api from './bookingsApi.ts'
import type { BookingDetails } from './types.ts'

vi.mock('./bookingsApi.ts')

const FUTURE = new Date(Date.now() + 86_400_000).toISOString()
const PAST = new Date(Date.now() - 86_400_000).toISOString()

function details(overrides: Partial<BookingDetails> = {}): BookingDetails {
  return {
    id: 1,
    booking_uid: 'b1',
    start_time: FUTURE,
    end_time: FUTURE,
    current_status: 'created',
    created_at: FUTURE,
    current_organizer_participant: { user_id: 'org-1' },
    current_client_participant: { user_id: 'cli-1' },
    organizer_history: [],
    meeting_links: [],
    email_notifications: [],
    telegram_notifications: [],
    chat_events: [],
    video_events: [],
    lifecycle_events: [],
    ...overrides,
  } as BookingDetails
}

beforeEach(() => {
  vi.resetAllMocks()
  vi.mocked(api.getBookingDetails).mockResolvedValue(details())
})

const reminderButton = () => screen.getByRole('button', { name: /Отправить напоминание клиенту/i })

describe('send client reminder', () => {
  it('enables and sends for an eligible booking', async () => {
    vi.spyOn(window, 'confirm').mockReturnValue(true)
    vi.mocked(api.sendClientReminder).mockResolvedValue({ status: 'accepted', email: 'cur@x.com' })
    render(<BookingDetailsPage bookingUid="b1" />)
    const btn = await waitFor(() => reminderButton())
    expect(btn).toBeEnabled()
    await userEvent.click(btn)
    await waitFor(() => expect(api.sendClientReminder).toHaveBeenCalledWith('b1'))
    expect(await screen.findByText(/cur@x\.com/)).toBeInTheDocument()
  })

  it('disables for a past booking', async () => {
    vi.mocked(api.getBookingDetails).mockResolvedValue(details({ start_time: PAST }))
    render(<BookingDetailsPage bookingUid="b1" />)
    expect(await waitFor(() => reminderButton())).toBeDisabled()
  })

  it('disables when the client has no account', async () => {
    vi.mocked(api.getBookingDetails).mockResolvedValue(details({ current_client_participant: { user_id: null } }))
    render(<BookingDetailsPage bookingUid="b1" />)
    expect(await waitFor(() => reminderButton())).toBeDisabled()
  })
})
```

NOTE: match the actual `BookingDetailsPage` props — open the file and check whether it takes `bookingUid` as a prop or reads it from a route/global. Adapt the `render(<BookingDetailsPage .../>)` calls and the import of `Participant` shape accordingly. If the page reads the uid from a router, set that up the way other page tests in the repo do.

### Step 3: Run, confirm FAIL

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend && npx vitest run src/modules/bookings/BookingDetailsPage.test.tsx`
Expected: failure — no reminder button yet.

### Step 4: Implement the button in `BookingDetailsPage.tsx`

Open the file. Add to the existing imports from `./bookingsApi.ts` the `sendClientReminder` function. Add component state near the other `useState` hooks (around line 278-283):
```tsx
  const [reminderState, setReminderState] = useState<{ sending: boolean; ok: string | null; error: string | null }>({
    sending: false,
    ok: null,
    error: null,
  })
```

Add an eligibility helper at module scope (top of file, after imports):
```tsx
const REMINDER_INELIGIBLE_STATUSES = new Set(['cancelled', 'completed', 'no_show'])

function canSendReminder(item: BookingDetails): boolean {
  const clientUserId = item.current_client_participant?.user_id ?? null
  if (!clientUserId) return false
  if (REMINDER_INELIGIBLE_STATUSES.has(item.current_status ?? '')) return false
  if (!item.start_time) return false
  return new Date(item.start_time).getTime() > Date.now()
}
```

Add a handler inside the component (near the other handlers):
```tsx
  async function handleSendReminder(item: BookingDetails) {
    if (!window.confirm('Отправить клиенту письмо-напоминание о встрече?')) return
    setReminderState({ sending: true, ok: null, error: null })
    try {
      const res = await sendClientReminder(item.booking_uid)
      setReminderState({ sending: false, ok: res.email, error: null })
    } catch (err) {
      setReminderState({
        sending: false,
        ok: null,
        error: err instanceof ApiError ? err.message : 'Не удалось отправить напоминание',
      })
    }
  }
```

Render the button in the client participant block (near the existing client `UserInfo` / change-email button, around line 360). Insert after that block:
```tsx
                <div style={{ marginTop: '8px', display: 'grid', gap: '4px' }}>
                  <button
                    type="button"
                    className="secondary small"
                    disabled={!canSendReminder(item) || reminderState.sending}
                    title={canSendReminder(item) ? '' : 'Доступно только для будущей активной встречи с привязанным аккаунтом клиента'}
                    onClick={() => void handleSendReminder(item)}
                  >
                    {reminderState.sending ? 'Отправка…' : 'Отправить напоминание клиенту'}
                  </button>
                  {reminderState.ok && (
                    <span style={{ fontSize: '12px', color: 'var(--success)' }}>
                      Отправлено на {reminderState.ok}
                    </span>
                  )}
                  {reminderState.error && (
                    <span className="error-text" style={{ fontSize: '12px' }}>{reminderState.error}</span>
                  )}
                </div>
```
Match the surrounding JSX structure (the exact wrapping element/indentation around line 360); place the block inside the same container as the client info so it renders within the client section. `item` is the loaded `BookingDetails` in scope there.

### Step 5: Run the component test, confirm PASS

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend && npx vitest run src/modules/bookings/BookingDetailsPage.test.tsx`
Expected: 3 passed.

### Step 6: Type-check + full frontend suite

Run: `cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend && npx tsc --noEmit && npx vitest run`
Expected: no type errors; all tests green.

### Step 7: Commit (frontend repo)

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend
git add src/modules/bookings/bookingsApi.ts src/modules/bookings/BookingDetailsPage.tsx src/modules/bookings/BookingDetailsPage.test.tsx
git commit --no-verify -m "feat(frontend): send-client-reminder button on booking detail

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Docs

**Files:**
- Modify: `event-admin/docs/API_CONTRACTS.md`
- Modify: `event-admin/CLAUDE.md`

### Step 1: `event-admin/docs/API_CONTRACTS.md`

In the bookings section, document the new endpoint:
- `POST /bookings/{booking_uid}/send-client-reminder` (admin JWT). Resolves the client's current email from event-users and publishes a `BOOKING_REMINDER` `notification.send_requested` (source `admin`).
- Responses: `202 {status, email}`; errors `404 booking_not_found`, `409 no_client_on_booking | booking_not_eligible | client_has_no_account | client_not_found`, `502` on publish failure.
- Note channels follow the `(BOOKING_REMINDER, client, *)` bindings (config-driven), and the `requested_at` nonce defeats the 10-minute identical-payload suppression so resends work.

### Step 2: `event-admin/CLAUDE.md`

Under the service role / booking-actions description (where `reassign-client` and the publish pattern are mentioned), add one line: event-admin also publishes `notification.send_requested` (BOOKING_REMINDER, client) from `POST /bookings/{uid}/send-client-reminder`, resolving the client's current email from event-users.

### Step 3: Commit (event-admin repo)

```bash
cd /Users/alexandrlelikov/PycharmProjects/events/event-admin
git add docs/API_CONTRACTS.md CLAUDE.md
git commit --no-verify -m "docs(admin): document send-client-reminder endpoint

Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Live e2e verification (no code)

- [ ] **Step 1: Rebuild + send through the real chain**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
docker compose up -d --build event-admin event-admin-frontend
docker compose restart event-admin-frontend   # refresh nginx upstream IP (known gotcha)
```

- [ ] **Step 2: Drive the endpoint with an admin JWT (TOTP inline)** against a real future booking uid (use one from `scripts/calcom_sim.py create --starts-in 2h` if needed), POST `/bookings/<uid>/send-client-reminder`, expect `202 {status, email}` with the CURRENT email. Then confirm an email outbox row appears in the notifier (`docker compose exec -T pg-notifier psql -U postgres -d event_notifier -c "select recipient_email, trigger_event, status from notification_outbox where trigger_event='BOOKING_REMINDER' order by created_at desc limit 3;"`). Record the outcome.

- [ ] **Step 3: Negative check** — change the client's email via the admin UI, then resend, and confirm the outbox `recipient_email` is the NEW address (proves current-email resolution + that `requested_at` allowed the resend).

---

## Self-Review (against the spec)

**Spec coverage:**
- Endpoint + route-orchestration → Task 1. ✅
- Resolve current email from event-users by client user_id → Task 1 (`client.get_user`). ✅
- Gate future + active + has-account; block on no account → Task 1 (`_reminder_eligible`, `client_has_no_account`). ✅
- Build BOOKING_REMINDER `notification.send_requested` with `requested_at` nonce; publish via EventPublisher source=admin → Task 1 (`_build_reminder_payload`, publish call). ✅
- Structured error codes (404/409×4/502) → Task 1 tests + route. ✅
- Frontend eligibility-disabled button + confirm + API + success/error → Task 2. ✅
- Config-driven channels (no email-only hardcode) → honored (publishes the command; no channel filtering). ✅
- Docs → Task 3. ✅
- Live verification → Task 4. ✅

**Type/signature consistency:**
- `IEventPublisher.publish(*, source, event_type, data)` — matches the call in Task 1 and `FakeEventPublisher`.
- `IUsersClient.get_user(user_id) -> dict` — Task 1 reads `["email"]`, `.get("name")`; `FakeUsersClient.get_user` raises httpx 404 when absent (drives `client_not_found`).
- `ParticipantDto.user_id: uuid.UUID | None`, `BookingDetailsDto.current_client_participant/meeting_links/start_time/current_status` — used exactly as defined.
- Front `BookingDetails.current_client_participant.user_id`, `start_time`, `current_status` — used in `canSendReminder`.
- Endpoint path `/bookings/{uid}/send-client-reminder` identical in route, API client, and both test suites.

**Placeholder scan:** none — every code step is complete. Two NOTEs ask the implementer to match existing fixture/page wiring (conftest fixture names; `BookingDetailsPage` prop/route shape) — these are verification instructions, not placeholders.
