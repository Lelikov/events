# Integration Report (audit v2)

Date: 2026-06-11. Verified on each nested repo's `audit-fixes` branch; `event-booking` on
root branch `feat/event-booking-service`. Canonical contracts: `docs/audit/v2/CONTRACT_DECISIONS.md`.

## 1. Per-service gates

| Service | uv sync / deps | Tests | Lint / Build |
|---|---|---|---|
| event-receiver | OK | `pytest`: **103 passed** | `ruff check`: clean |
| event-saver | OK | `pytest`: **100 passed** | `ruff check`: clean |
| event-booking | OK | `pytest`: **88 passed** | `ruff check`: clean (warning: deprecated ANN101/ANN102 in config — cosmetic) |
| event-notifier | OK | `pytest`: **80 passed** | `ruff check`: clean |
| event-users | OK | `pytest`: **55 passed** | `ruff check`: clean |
| event-admin | OK | `pytest`: **75 passed** | `ruff check`: clean |
| event-schemas | OK | `pytest`: **73 passed** | `ruff check`: clean (same ANN101/ANN102 warning) |
| event-admin-frontend | npm ci OK | `vitest`: **27 passed (5 files)** | `tsc -b && vite build`: OK; `eslint .`: clean |
| jitsi-chat | npm ci OK | `vitest`: **21 passed (3 files)** | `tsc -b && vite build`: OK (chunk-size warning >500kB for ChatOverlay — advisory); `eslint .`: clean |

**Zero test/lint/build failures. No fixes were needed.**

## 2. Cross-repo consistency

### Queue specs and envelope imported from event_schemas — PASS (with one gap)

- **event-receiver**: imports `ALL_QUEUES`, `EVENTS_DLX`, `QueueSpec`, `ROUTING_RULES`,
  `event_schemas.attributes`; declares the FULL topology (D2).
- **event-saver**: imports `SAVER_QUEUES`, `EVENTS_DLX`, `QueueSpec`, `BOOKING_ID_ATTRIBUTE`;
  consumers use `unwrap_payload()` (booking_extractor, lifecycle_projection).
- **event-booking**: imports `BOOKING_LIFECYCLE_BOOKING_QUEUE`, `EVENTS_DLX`, `QueueSpec`,
  `unwrap_payload`, `BOOKING_ID_ATTRIBUTE`.
- **event-notifier**: imports `NOTIFICATION_COMMANDS_QUEUE`, `EVENTS_DLX`, `QueueSpec`,
  `EventEnvelope`/`EnvelopeParticipant` and validates `NotificationCommandPayload`
  via `envelope.parse_payload(...)` (D6).
- **No `ce-booking_id` / `booking_id` attribute readers anywhere** (D3/D8) — grep clean.
- Queue-argument literals outside event-schemas exist only in **tests** (event-saver,
  event-booking — asserting the canonical values; acceptable) and in
  **event-users/event_users/consumer.py:127-132**, which hardcodes the canonical args for
  `events.user.email` instead of importing `USER_EMAIL_QUEUE`. The literals are byte-identical
  to `QueueSpec.arguments` (verified) so the broker accepts them, but this is a remaining
  single-source-of-truth gap: event-users has **no event-schemas dependency** in its pyproject.
  Not a one-liner (needs dependency + consumer rewrite + tests) — left as a follow-up.
- event-users consumer reads `data.get("original", data)` — envelope-first with a legacy
  top-level fallback. Reads the new envelope correctly; fallback is tolerated-legacy, not a
  contract violation.

### Lifecycle queue bindings — PASS

`event_schemas.queues`: `events.booking.lifecycle.saver` (consumer event-saver) and
`events.booking.lifecycle.booking` (consumer event-booking) both bind routing key
`events.booking.lifecycle`, exactly per CONTRACT_DECISIONS D2. Confirmed live on the broker
during e2e (one published message landed in both queues).

### pyproject references to local event-schemas + uv sync — PASS (with notes)

| Service | Reference |
|---|---|
| event-receiver | `event-schemas = { path = "../event-schemas", editable = true }` (relative — portable) |
| event-saver | `event-schemas @ file:///Users/.../events/event-schemas` (absolute path) |
| event-booking | `event-schemas @ file:///Users/.../events/event-schemas` (absolute path) |
| event-notifier | `event-schemas @ file:///Users/.../events/event-schemas` (absolute path) |
| event-admin | no dependency — by design (read-only DB API, no messaging) |
| event-users | **no dependency** — gap, see above |

`uv sync` resolved successfully in all 7 Python services. Note: the three absolute
`file:///Users/alexandrlelikov/...` URLs work locally but are not portable to CI/other machines;
recommend switching to relative `path = "../event-schemas"` like event-receiver.

### event-notifier ↔ event-users HTTP contract — PASS

- Notifier `event_notifier/infrastructure/users_client.py` calls `GET /api/users/id/{user_id}`;
  event-users exposes `@users_router.get("/id/{user_id}")` under prefix `/api/users`. Match.
- Notifier resolves `user_id` from `normalized.participants` (D6) and does not call
  `/by-identity` — consistent with the contract (no email-path lookups).
- Note: **event-receiver** (`adapters/users_client.py:46`) still uses the deprecated
  `GET /api/users/roles/{role}/emails/{email}` instead of `GET /api/users/by-identity`.
  The deprecated route still exists in event-users (kept explicitly for migration), so
  nothing is broken; migrating the receiver is a documented follow-up.

## 3. End-to-end verification (real broker, real payload)

Methodology — Docker available, so the real-infra path was used:

1. Started isolated `rabbitmq:3-management` (`audit-v2-rabbit`, ports 5680/15680 — host ports
   5672/15672 were occupied by pre-existing user containers, which were not touched).
2. Started event-receiver from source (`uv run uvicorn event_receiver.main:app --port 8899`)
   with minimal env incl. `CALCOM_WEBHOOK_SECRET`; `EVENT_USERS_API_URL` pointed at a dead
   port to exercise the documented resolver fallback (publish without `user_id`).
3. Took the real `BOOKING_CREATED` record from `event-booking/requests.jsonl`
   (python-repr → `ast.literal_eval` → JSON; uid `n3FHda8Cpy48QW4JZX9th7`) and POSTed it to
   `/event/calcom` with a correct `X-Cal-Signature-256` HMAC-SHA256. Response: **202**.
4. Management-API verification (`/api/queues`):
   - Message present in **both** `events.booking.lifecycle.saver` and
     `events.booking.lifecycle.booking` (fan-out per D2).
   - Both queues had canonical args verbatim: `x-max-priority: 10`,
     `x-dead-letter-exchange: events.dlx`, `x-dead-letter-routing-key: <queue>.dlq`.
   - Full topology declared by the receiver: all 12 queues + 12 `.dlq` companions
     (args `x-message-ttl: 86400000`), exchanges `events` and `events.dlx` both topic/durable.
   - Peeked message: routing key `events.booking.lifecycle`, priority 10, headers
     `ce-bookingid` (no underscore, D3), `ce-type: booking.created`, `ce-traceid`/`ce-spanid`/
     `ce-idempotencykey` present; body is the canonical envelope
     `{"original": {...}, "normalized": {"participants": [organizer, client]}}` (D1/D7).
5. Started isolated `postgres:16` (`audit-v2-postgres`, port 5448), ran event-saver alembic
   migrations (`upgrade head` — clean), started event-saver from source against the same broker.
   Result: saver consumed its queue (`.saver` → 0 msgs), wrote
   `bookings` row (`booking_uid=n3FHda8Cpy48QW4JZX9th7`, `current_status=created`,
   correct start/end times), 1 row in `events`, 1 row in `booking_lifecycle_events`.
   `organizer_user_id`/`client_user_id` NULL — expected, event-users was intentionally offline.
   The `.booking` queue still held its own copy (1 msg, 0 consumers) — consumer isolation works.
6. **Cleanup**: killed both local uvicorn processes; removed `audit-v2-rabbit` and
   `audit-v2-postgres`. Pre-existing user containers were left untouched.

**Outcome: PASS.** Webhook → HMAC auth → normalization/envelope → topic-exchange fan-out →
saver consumption → Postgres projection all verified with a real payload on a real broker.

## 4. Not verified / out of scope

- **event-booking consumer against the live broker** — its queue topology and envelope handling
  are covered by its unit tests (88 passed) and the broker-side queue/binding/args were verified
  live; the service itself was not started against the e2e broker (requires its own DB + APIs).
- **event-notifier end-to-end** — no `notification.send_requested` sample exists in
  `requests.jsonl`; the notifier consumer contract (envelope parse, user_id from
  `normalized.participants`, `/api/users/id/{id}`) is verified by its unit tests and grep.
- **event-users resolve-or-create enrichment** — deliberately exercised the *fallback* path
  (service offline → publish without user_id). The happy path is covered by receiver unit tests.
- **CRM sync, GetStream, Jitsi, Unisender external integrations** — require external
  credentials; not exercised.

## 5. Follow-ups (non-blocking)

1. event-users: add `event-schemas` dependency and replace hardcoded queue args in
   `event_users/consumer.py` with `USER_EMAIL_QUEUE` from `event_schemas.queues`.
2. event-receiver: migrate `adapters/users_client.py` from deprecated
   `/api/users/roles/{role}/emails/{email}` to `/api/users/by-identity`; then drop the
   deprecated route in event-users.
3. event-saver / event-booking / event-notifier: change absolute
   `file:///Users/...` event-schemas references to relative `path = "../event-schemas"`.
