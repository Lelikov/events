# Audit Report v2

**Dates:** 2026-06-10 → 2026-06-11
**Scope:** all 9 services + cross-cutting flows of the events monorepo
**Status:** COMPLETE — 210 findings raised, fixes applied and integration-verified on a real broker
**Supersedes:** `docs/audit/AUDIT_REPORT.md` (April 2026 audit; kept as historical baseline)

---

## 1. Methodology

Audit-v2 ran as a strictly **sequential pipeline with disk checkpoints** (one agent at a time;
every result committed to `docs/audit/v2/` before the next stage started):

1. **13 auditors** — one per service (9) plus four cross-cutting auditors
   (`rabbitmq-topology`, `delivery-reliability`, `flow-e2e`, `security`). Each wrote a findings
   checkpoint to `docs/audit/v2/findings/<name>.json` — **210 raw findings** total.
2. **Contracts wave** — a single coordinator fixed every *cross-service* contract defect first
   (queue topology, envelope, attribute naming, payload models) and froze the canonical decisions
   in [`CONTRACT_DECISIONS.md`](CONTRACT_DECISIONS.md). Per-service fixers were forbidden from
   re-litigating these (D8).
3. **9 service fixers** — one per service, each on its repo's `audit-fixes` branch
   (`event-booking` lives in the root repo on `feat/event-booking-service`). Each wrote a fix
   manifest (`docs/audit/v2/fixes/<service>.json`) with `fixed[]` / `skipped[]` (with reasons).
4. **Integration verification** — all test/lint/build gates re-run per service, cross-repo
   consistency greps, and a live end-to-end run: isolated `rabbitmq:3-management` +
   `postgres:16`, event-receiver + event-saver started from source, and a **real cal.com
   `BOOKING_CREATED` payload** (from `event-booking/requests.jsonl`) POSTed to the new
   `/event/calcom` endpoint with a valid `X-Cal-Signature-256` HMAC. Webhook → envelope →
   topic-exchange fan-out (both lifecycle queues) → saver consumption → Postgres projection
   all verified. Full detail: [`INTEGRATION_REPORT.md`](INTEGRATION_REPORT.md).

**Fix policy:** fix-all. Items not fixed are explicitly listed in §5 with the recorded reason,
classified as *follow-up (code work pending)* or *accepted decision/risk*.

---

## 2. Results Summary

### 2.1 Findings by auditor (210 total: 20 CRITICAL / 39 HIGH / 66 MEDIUM / 85 LOW)

| Auditor | C | H | M | L | Total |
|---|---|---|---|---|---|
| event-booking | 4 | 8 | 11 | 7 | 30 |
| event-notifier | 7 | 4 | 6 | 5 | 22 |
| event-users | 1 | 4 | 8 | 8 | 21 |
| event-admin-frontend | 0 | 3 | 3 | 14 | 20 |
| event-receiver | 0 | 3 | 6 | 10 | 19 |
| event-schemas | 3 | 4 | 7 | 5 | 19 |
| event-admin | 1 | 1 | 6 | 10 | 18 |
| jitsi-chat | 0 | 1 | 4 | 12 | 17 |
| event-saver | 0 | 2 | 4 | 9 | 15 |
| rabbitmq-topology (cross) | 3 | 4 | 3 | 1 | 11 |
| delivery-reliability (cross) | 1 | 2 | 4 | 0 | 7 |
| flow-e2e (cross) | 0 | 3 | 2 | 1 | 6 |
| security (cross) | 0 | 0 | 2 | 3 | 5 |
| **Total** | **20** | **39** | **66** | **85** | **210** |

Cross-cutting findings were dispatched to the contracts wave and the owning service fixers;
the fixed/skipped accounting below therefore overlaps the cross-cutting rows.

### 2.2 Fix outcome per fixer (verified green at integration)

| Fixer | Branch | Fixed | Skipped | Tests (after) | Lint/Build |
|---|---|---|---|---|---|
| contracts (cross-repo) | multiple (see manifest) | 11 | 8¹ | — | — |
| event-receiver | `event-receiver:audit-fixes` | 24 | 1¹ | pytest **103** (was 37) | ruff clean |
| event-saver | `event-saver:audit-fixes` | 15 | 5¹ | pytest **100** (was 39) | ruff clean (was 27 errors) |
| event-booking | root `feat/event-booking-service` | 26 | 8¹ | pytest **88** | ruff clean |
| event-notifier | `event-notifier:audit-fixes` | 10² | 2 | pytest **80** | ruff clean |
| event-users | `event-users:audit-fixes` | 16 | 7¹ | pytest **55** (was 0) | ruff + pre-commit clean |
| event-admin | `event-admin:audit-fixes` | 15 | 3 | pytest **75** (was 0) | ruff clean |
| event-admin-frontend | `event-admin-frontend:audit-fixes` | 23 | 2 | vitest **27** (was 0) | tsc + eslint clean |
| event-schemas | `event-schemas:audit-fixes` | 4 | 1 | pytest **73** (19 from contracts wave) | ruff clean |
| jitsi-chat | `jitsi-chat:audit-fixes` | 17 | 0 | vitest **21** (was 0) | tsc + eslint clean |

¹ Most "skipped" entries are *already fixed elsewhere* (typically by the contracts wave) — only
the genuinely open ones are carried into §5.
² Notifier entries are coarse-grained redesign chunks; they cover most of the 22 raw notifier
findings (see manifest).

**Integration gate: zero test/lint/build failures across all 9 services; e2e PASS.**
Suite total: **622 tests** (Python 574, vitest 48), the vast majority new in this audit.

---

## 3. Architectural Decisions (canonical, see CONTRACT_DECISIONS.md)

1. **One queue per consumer (D2).** `events.booking.lifecycle` was consumed by both event-saver
   and event-booking — round-robin split the stream. Now: `events.booking.lifecycle.saver` and
   `events.booking.lifecycle.booking`, both bound to routing key `events.booking.lifecycle`
   (fan-out via topic bindings, verified live: one publish landed in both queues).
2. **Typed envelope (D1).** Every CloudEvent `data` is `{"original": <domain payload>,
   "normalized": {"participants": [{email, role, time_zone, user_id}]}}`. Only event-receiver
   wraps; all consumers unwrap via `event_schemas.envelope.unwrap_payload()` /
   `EventEnvelope.parse_payload()`. Top-level domain-field reads are bugs.
3. **Canonical topology in event-schemas.** `event_schemas.queues` is the single source of truth:
   `QueueSpec` (names, bindings, verbatim arguments incl. `x-max-priority=10` and DLX),
   `ALL_QUEUES`, `ROUTING_RULES`, `events.dlx` + per-queue `.dlq` companions (24h TTL).
   Every consumer declares its own queues/DLQs idempotently; the receiver declares the full
   topology. Removed: `events.booking.reminder` (no producer/consumer) and the phantom
   `events.notifications`. Attribute names are canonical in `event_schemas.attributes`
   (`bookingid`/`ce-bookingid` — never `booking_id`). event-schemas bumped to **0.2.0**.
4. **New `/event/calcom` ingress.** event-receiver gained a real cal.com webhook endpoint
   (`X-Cal-Signature-256` HMAC validation, normalization of real cal.com payloads validated
   against `requests.jsonl` samples), plus idempotency-key dedup, unknown-type policy
   (route to `events.unrouted`, never 500), publish timeouts/mandatory-return handling, and
   multi-attendee/guest participant extraction.
5. **Notifier command-path redesign.** event-notifier rebuilt around the envelope:
   `NotificationCommandPayload` validation, recipient `user_id` resolved from
   `normalized.participants`; outbox schema redesigned (migration 002: atomic
   processed_events claim + insert, processing reaper, permanent/transient retry split with
   capped backoff, processed_events TTL); channels classify 4xx-permanent vs transient;
   UniSender templates/Telegram texts moved to config/Jinja2; and **delivery-result events**
   (`notification.*.message_sent`) are now actually published back through event-receiver —
   closing the previously dead `events.notification.delivery` pipeline.

Other notable per-service outcomes: event-booking's constraint analyzer no longer rejects every
booking against itself (CRITICAL) and gained idempotent-resume orchestration; event-admin's
`DEBUG=True` auth bypass is deleted; event-users' CRM sync actually commits now (CRITICAL);
GetStream user-id crypto moved from zero-IV AES-CBC to AES-GCM on both producer and decoder
sides.

---

## 4. Deployment Notes (aggregated from fix manifests)

Apply when promoting the `audit-fixes` branches:

**New required environment variables**
- event-receiver: `CALCOM_WEBHOOK_SECRET` (cal.com HMAC). Optional: `PUBLISH_TIMEOUT` (10s),
  `CORS_ORIGINS` — **must include the jitsi-chat SPA origin** or browser telemetry fails.
- event-booking: `RABBIT_URL`, `EVENTS_ENDPOINT_URL`, `JITSI_JWT_SUB` are now **required**
  (previously silently defaulted / fire-and-forget).
- event-notifier: `UNISENDER_TEMPLATE_IDS`; optional `EVENTS_ENDPOINT_URL` (delivery-result
  events publish only when set); `.env` DSN format is asyncpg.
- event-saver: optional `RABBIT_PREFETCH_COUNT` (10), `RABBIT_GRACEFUL_TIMEOUT` (30s).
- event-users: optional `WEBHOOK_VISIBILITY_TIMEOUT_SECONDS` (120s), `CRM_SYNC_MAX_BACKOFF_SECONDS`.

**Database migrations (alembic upgrade head)**
- event-users: **0005** (`user_email_changelog.message_id` unique — consumer idempotency).
- event-notifier: **002** (outbox redesign: trigger_event/recipient_email/last_error, status
  CHECK, drops dead `routing_rules`).
- event-saver: **a9d4c1f0b7e2** (drops the legacy 4-column dedup index; single
  `ON CONFLICT DO NOTHING` dedup path).

**Behavioral / startup changes**
- event-admin: startup now **fails outside DEBUG** if `JWT_SECRET_KEY`,
  `USERS_SERVICE_API_TOKEN`, `CACHE_INVALIDATION_TOKEN` or `EVENT_RECEIVER_API_KEY` is
  <16 chars or a placeholder. **JWT default lifetime is now 60 min** (was 24h —
  `JWT_EXPIRE_MINUTES` to override). `/auth/login` can return 429 (brute-force lockout).
  event-receiver enforces the same secret-strength rule for its secrets.
- event-users: **all `/api/users` routes now require `role=admin`** — callers with `role=user`
  JWTs will get 403. `JWT_AUDIENCE`/`JWT_ISSUER` are optional and must be enabled together
  with event-admin's matching vars (both sides tolerant when unset).
- event-booking: GetStream user-id scheme changed (zero-IV CBC → AES-GCM) — ids change, so
  pre-existing GetStream users/channels are recreated on the next lifecycle event (one-time).
- event-schemas 0.2.0: `uv.lock` refreshed in all dependent services; deploy schemas together
  with its consumers.
- RabbitMQ: topology is declared idempotently by services; pre-existing queues declared with
  the OLD arguments (or the old shared `events.booking.lifecycle` queue) must be deleted before
  the new declarations succeed (`PRECONDITION_FAILED` otherwise).

---

## 5. Remaining Open Items

Aggregated from every `fixes/*.json` `skipped[]` list and the integration report. Entries that
were "skipped because already fixed elsewhere" are excluded (verified against the fixing
commits).

### 5a. Follow-ups requiring code work

| # | Item | Where | Source |
|---|---|---|---|
| 1 | **event-users hardcodes queue arguments** in `event_users/consumer.py` (byte-identical to canonical, but no `event-schemas` dependency in its pyproject — single-source-of-truth gap) | event-users | INTEGRATION_REPORT §2 / follow-up 1 |
| 2 | **event-receiver still calls the deprecated** `GET /api/users/roles/{role}/emails/{email}` (verified present in `adapters/users_client.py:46`); migrate to `GET /api/users/by-identity`, then drop the deprecated route in event-users | event-receiver, then event-users | INTEGRATION_REPORT follow-up 2; event-users manifest |
| 3 | **Absolute `file:///Users/...` event-schemas references** in event-saver / event-booking / event-notifier pyprojects (not portable to CI; event-receiver already uses relative `path = "../event-schemas"`) | event-saver, event-booking, event-notifier | INTEGRATION_REPORT follow-up 3 |
| 4 | **Locale-aware notifications**: cal.com `language.locale` is dropped at receiver ingress; envelope/`EnvelopeParticipant` carry no locale field. Time-zone half is DONE (per-recipient `start_time_local` in notifier c276ebf); the language half needs event-schemas + event-receiver changes | event-schemas, event-receiver, event-notifier | notifier manifest skipped #1 |
| 5 | **FCM push channel**: implemented and retry-classified but deliberately not wired in `ioc.py` — pending FCM credentials and an OAuth token provider | event-notifier | notifier manifest skipped #2 |
| 6 | **Machine-readable error codes from event-admin** (`detail: {code: ...}`): frontend error translation is keyed on exact backend English strings (mitigated client-side, tracked as frontend AUDIT.md #13) | event-admin + frontend | frontend manifest skipped #1 |
| 7 | **`Bearer` scheme for the admin-ingest API key**: requires a coordinated two-side change (event-admin sender + event-receiver `ingest_admin`); both sides currently use the raw header constant-time-compared | event-admin + event-receiver | admin manifest skipped #1 |
| 8 | **DLQ consumer / alerting**: nothing consumes any `.dlq`, and dead letters expire after 24h (TTL is canonical, see 5b). Redrive runbook documented in receiver QUEUES_DIGEST; platform-level alerting still needed | platform/ops | saver + receiver manifests |
| 9 | **user_id backfill/reconciliation**: when event-users is down at ingress, events publish with `user_id=None` permanently; a backfill job is event-saver/event-users scope | event-saver / event-users | receiver manifest notes |
| 10 | **Per-recipient meeting-URL / product-level notification design** for multi-attendee and guest bookings: producers now emit the needed fields (time_zone, per-recipient URL events); the product flow itself is unbuilt | event-booking / event-notifier | contracts manifest skipped #7 |
| 11 | `simulate_booking.py` still sends legacy users-list payloads for `meeting.url_*` (dev tool; receiver keeps a tolerant fallback) | event-receiver | contracts manifest skipped #2 |
| 12 | `event-receiver/QUEUES_DIGEST.md` lacks a `booking.client_reassigned` row (verified missing; root MESSAGE_CONTRACTS.md has it) | event-receiver docs | admin manifest skipped #2 |

### 5b. Accepted decisions / risks (no action planned)

| Item | Rationale |
|---|---|
| **Tokens in URL query string** (jitsi-chat `jwt_video`/`jwt_chat`) | Email-link contract with event-booking/Shortify requires it; mitigated with `Referrer-Policy: no-referrer` (meta + Caddy) and short token lifetimes. Documented in jitsi-chat AUDIT.md |
| **DLQ 24h message TTL** | Canonical per CONTRACT_DECISIONS D2; transient errors no longer reach DLQs at all (retry+requeue in consumers); residual loss window is an alerting TODO (5a #8) |
| **JWT in sessionStorage** (frontend) | No refresh/cookie session exists in event-admin; sessionStorage + 60-min expiry + startup expiry check + 401 interceptor applied; residual JS-readability accepted; CSP belongs to the hosting layer |
| **`events.notification.commands` / `events.user.email` bypass event-saver** | Commands are imperatives, not facts; the resulting `notification.*.message_sent` facts ARE persisted. Documented in saver QUEUES_DIGEST |
| **event-admin local `.env` keeps `DEBUG=True`** | Local dev only; DEBUG no longer affects authentication; `.env.example` ships `DEBUG=False` |
| **`EnvelopeParticipant` fields stay lenient `str \| None`** | Consumer-side parsing must tolerate in-flight legacy messages; strictness lives on producer-validated payload models |
| **event-users consumer keeps legacy top-level fallback** (`data.get("original", data)`) | Tolerated-legacy read; envelope-first verified at integration |
| **CRM `COALESCE` null semantics; no index on `user_contacts.channel`** | Intentional (null = not provided); no channel-wide lookups exist yet |
| **In-memory LoginGuard / UsersCache in event-admin** | Single-instance assumption, documented in its AUDIT.md |
| **event-saver `NackMessage(requeue=True)` relies on FastStream 0.6 semantics** | Documented; revisit on FastStream upgrade |

---

## 6. Artifact Index

- [`CONTRACT_DECISIONS.md`](CONTRACT_DECISIONS.md) — canonical contract decisions D1–D8
- [`INTEGRATION_REPORT.md`](INTEGRATION_REPORT.md) — gates, cross-repo greps, live e2e
- [`findings/*.json`](findings/) — 13 auditor checkpoints (210 findings)
- [`fixes/*.json`](fixes/) — 10 fix manifests (fixed/skipped/notes, commit SHAs)
- Per-service: each repo's `docs/AUDIT.md` rewritten as the audit-v2 ledger on its
  `audit-fixes` branch
