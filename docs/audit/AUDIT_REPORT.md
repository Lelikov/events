# Architecture Audit Report

> **⚠️ SUPERSEDED — historical document.** This report describes the **pre-audit-v2 state
> (April 2026)**. A fresh full audit (audit-v2, 2026-06-10/11) re-audited all 9 services,
> fixed or dispositioned every finding, and verified the result end-to-end on a real broker.
> See **[`docs/audit/v2/AUDIT_REPORT_V2.md`](v2/AUDIT_REPORT_V2.md)** for the current state.
> Most CRITICAL/HIGH items below (routing bug C-1, queue mismatches, auth bypass C-14,
> SKIP LOCKED bug C-11, missing tests L-1, …) are resolved on the per-repo `audit-fixes`
> branches.

Generated: 2026-04-20

## Executive Summary

**Total deduplicated findings: 18 CRITICAL, 31 HIGH, 33 MEDIUM, 24 LOW = 106 findings across 10 audit sources (7 services + 3 cross-cutting analyses).**

After deduplication of findings that appear in multiple services, the consolidated counts are:

| Severity | Count |
|----------|-------|
| CRITICAL | 14 |
| HIGH | 24 |
| MEDIUM | 30 |
| LOW | 18 |
| **Total** | **86** |

### Top 5 Most Critical Systemic Concerns

1. **Booking lifecycle events never reach event-saver (data loss).** First-match routing in event-receiver sends all booking lifecycle events to the phantom queue `events.notifications` instead of `events.booking.lifecycle`. event-saver never sees `booking.created`, `booking.cancelled`, `booking.rescheduled`, `booking.reassigned`, or `booking.reminder_sent`. This is the single highest-impact bug in the system.

2. **`SqlExecutor.execute()` auto-commits after every statement, breaking transactional atomicity.** Present in event-saver, event-users, and event-admin. Every projection, every multi-step write, every upsert commits individually. Partial failures leave the database in inconsistent states with no rollback.

3. **Queue name mismatch between event-receiver and event-notifier.** event-receiver routes `notification.send_requested` to `events.notification.commands`; event-notifier subscribes to `events.notifications` (default). Notification commands pile up unconsumed. event-notifier instead receives booking lifecycle events from the phantom queue.

4. **Dual EventType enums with incompatible string values.** event-schemas defines `EventType.BOOKING_CREATED = "booking.created"` while event-saver defines `EventType.BOOKING_CREATED = "booking.events.v1.booking.created.create"`. The shared schema library is not actually shared with the largest consumer.

5. **Hardcoded JWT default secret across multiple services.** event-users and event-admin both ship `jwt_secret_key` with a default value of `"dev-jwt-secret-change-in-prod"`. A missing env var in production silently enables full token forgery.

### Recommended Fix Order

1. Fix routing rules in event-receiver (C-1) -- restores booking data flow
2. Fix queue name in event-notifier config (C-3) -- restores notification pipeline
3. Remove `session.commit()` from `SqlExecutor.execute()` in all services (C-5)
4. Remove hardcoded JWT default secrets (C-6)
5. Fix Python except syntax in normalizers.py (C-8)
6. Add `time_zone` to `UserInfo` in event-schemas (C-7)
7. Unify EventType enums (C-9)
8. Make FCM env vars optional in event-notifier (C-10)

---

## CRITICAL Findings

---

### C-1: First-match routing sends booking lifecycle events to phantom queue `events.notifications` (DATA LOSS)

**Services affected:** event-receiver, event-saver, event-notifier
**Location:** `event-receiver/event_receiver/config.py:9-34`
**Source audits:** event-receiver_audit, event-notifier_audit, x1_message_topology

The first 5 routing rules in `_default_route_rules()` send `booking.created`, `booking.cancelled`, `booking.rescheduled`, `booking.reassigned`, and `booking.reminder_sent` to queue `events.notifications`. Because `EventRouter.resolve_routing_key_by_fields()` returns the first match, these rules shadow the correct `events.booking.lifecycle` and `events.booking.reminder` rules that appear later. The queue `events.notifications` is not consumed by event-saver. All booking lifecycle events are silently routed to an unconsumed queue (from event-saver's perspective). event-notifier does consume `events.notifications` by default, so it receives booking lifecycle events -- but this bypasses the intended separation of concerns.

Additionally, `events.booking.lifecycle`, `events.booking.reminder`, and `events.notification.commands` become orphaned queues with no messages or no consumer respectively.

**Recommendation:** Remove the five `events.notifications` routing rules entirely (lines 9-34 in config.py). The `events.booking.lifecycle` and `events.booking.reminder` rules that follow are the correct targets. Add a startup validation that cross-checks routing destinations against a known consumer manifest.

---

### C-2: JWT `verify()` raises `ValueError` instead of `UnauthorizedError` -- uncaught 500

**Services affected:** event-receiver
**Location:** `event-receiver/event_receiver/security.py:81,88` + `event-receiver/event_receiver/routes.py:52-53`
**Source audit:** event-receiver_audit

`AuthorizationJWTVerifier.verify()` raises `ValueError` when the JWT `source` or `type` claim does not match the incoming event. The HTTP error mapper only catches `IngestError` subclasses. A `ValueError` escapes and FastAPI returns a raw 500 with no log of the auth failure.

**Recommendation:** Replace both `raise ValueError(...)` with `raise UnauthorizedError(...)` in `security.py:verify()`.

---

### C-3: Queue name mismatch -- event-notifier subscribes to wrong queue

**Services affected:** event-notifier, event-receiver
**Location:** `event-notifier/event_notifier/config.py:18`, `event-receiver/event_receiver/config.py:85-88`
**Source audits:** event-notifier_audit, x1_message_topology

event-notifier's `notifications_queue` defaults to `"events.notifications"`. event-receiver routes `notification.send_requested` to `"events.notification.commands"`. CLAUDE.md documents `events.notification.commands` as the intended queue. The result: `notification.send_requested` events pile up unconsumed in `events.notification.commands`, while event-notifier processes booking lifecycle events from `events.notifications` instead.

**Recommendation:** Update `config.py` default to `"events.notification.commands"` and update `.env.example`. Alternatively set `NOTIFICATIONS_QUEUE=events.notification.commands` in all deployments.

---

### C-4: `RequestLoggerMiddleware` writes raw request bodies (including secrets) to unrotated local file

**Services affected:** event-receiver
**Location:** `event-receiver/event_receiver/main.py:32,35-53`
**Source audit:** event-receiver_audit

Always active, writes full request bodies (headers, decoded JSON, auth tokens) to `incoming_requests.jsonl` with no size limit, rotation, or debug gate. Credential exfiltration risk and disk-exhaustion risk.

**Recommendation:** Gate behind `settings.debug`. Replace with structlog `debug`-level entries for production.

---

### C-5: `SqlExecutor.execute()` commits inside the method -- breaks transactional atomicity

**Services affected:** event-saver, event-users, event-admin
**Location:** `event-saver/event_saver/adapters/sql.py:18-20`, `event-users/event_users/adapters/sql.py:23-25`, `event-admin/event_admin/adapters/sql.py:23-35`
**Source audits:** event-saver_audit, event-users_audit, event-admin_audit

`SqlExecutor.execute()` calls `await self.session.commit()` unconditionally after each statement. In event-saver, every projection commits individually -- if projection 3 of 7 fails, projections 1-2 are permanent with no rollback. In event-users, user insert + contact upsert commit separately -- a contact failure leaves a stranded user. In event-admin, write methods exist despite the service being read-only.

**Recommendation:** Remove `await self.session.commit()` from `execute()` in all three services. Let the session lifecycle owner (use case or context manager) call commit once at the end. In event-admin, additionally remove write methods entirely and enforce a read-only DB role.

---

### C-6: Hardcoded JWT default secret `"dev-jwt-secret-change-in-prod"`

**Services affected:** event-users, event-admin
**Location:** `event-users/event_users/config.py:15`, `event-admin/event_admin/config.py:29`
**Source audits:** event-users_audit, event-admin_audit

Both services have `jwt_secret_key` with a default value of `"dev-jwt-secret-change-in-prod"`. If the production `.env` omits `JWT_SECRET_KEY`, the app starts silently with a publicly known secret. Any actor can forge valid JWT tokens for any role.

**Recommendation:** Remove the default value: `jwt_secret_key: str = Field(strict=True)`. Add a startup validator requiring minimum 32-character length.

---

### C-7: `UserInfo` missing `time_zone` field referenced at runtime by event-receiver normalizer

**Services affected:** event-schemas, event-receiver
**Location:** `event-schemas/event_schemas/types.py:72-76`, `event-receiver/event_receiver/normalizers.py:117`
**Source audit:** event-schemas_audit

`UserInfo` only declares `email: EmailStr`. The normalizer accesses `validated.user.time_zone` for `booking.reassigned` events, which raises `AttributeError` at runtime, causing silent empty participant list fallback.

**Recommendation:** Add `time_zone: str | None = None` to `UserInfo`, or remove the reference in the normalizer.

---

### C-8: Invalid multi-exception `except` syntax in normalizers.py (Python syntax error)

**Services affected:** event-receiver
**Location:** `event-receiver/event_receiver/normalizers.py:47,151`
**Source audit:** event-schemas_audit

Lines use `except ValidationError, KeyError, ValueError:` without parentheses. In Python 3, only the first exception type is caught; `KeyError` becomes the binding variable. `KeyError` and `ValueError` propagate unhandled.

**Recommendation:** Fix to `except (ValidationError, KeyError, ValueError):` and `except (ValueError, UnicodeDecodeError, binascii.Error):`.

---

### C-9: event-saver defines its own EventType enum with incompatible string values

**Services affected:** event-schemas, event-saver
**Location:** `event-schemas/event_schemas/types.py:8-43`, `event-saver/event_saver/event_types.py:20-37`
**Source audit:** event-schemas_audit

event-schemas defines `EventType.BOOKING_CREATED = "booking.created"` while event-saver defines `EventType.BOOKING_CREATED = "booking.events.v1.booking.created.create"`. The shared schema library is not shared with the largest consumer.

**Recommendation:** Decide on a single canonical EventType enum in event-schemas. Remove the duplicate from event-saver.

---

### C-10: FCM env vars required at startup despite PushChannel being disabled

**Services affected:** event-notifier
**Location:** `event-notifier/event_notifier/config.py:31-32`
**Source audit:** event-notifier_audit

`fcm_project_id` and `fcm_service_account_json` are `Field(strict=True)` with no default. Any deployment without FCM credentials fails to start, even though Push is commented out.

**Recommendation:** Make FCM fields optional with `Field(default=None)` or guard with a feature flag.

---

### C-11: `FOR UPDATE SKIP LOCKED` runs outside a transaction -- row lock immediately released

**Services affected:** event-notifier
**Location:** `event-notifier/event_notifier/db/repository.py:75-89`
**Source audit:** event-notifier_audit

`fetch_pending_outbox` issues `SELECT ... FOR UPDATE SKIP LOCKED` in autocommit mode. The lock is acquired and immediately released. Another instance can pick up the same rows, enabling duplicate notification deliveries.

**Recommendation:** Wrap in `async with conn.transaction()` and hold through delivery, or use a two-step UPDATE to `'processing'` status atomically.

---

### C-12: Module-level `container = make_async_container(...)` executes at import time

**Services affected:** event-receiver
**Location:** `event-receiver/event_receiver/main.py:56`
**Source audit:** event-receiver_audit

Container construction at import time means broken settings or missing env vars do not fail immediately. Test imports trigger full container construction with side effects. The container is a module-level global making the app non-restartable.

**Recommendation:** Move container construction inside `lifespan` or a factory function.

---

### C-13: `db/__init__.py` imports directly from `event_saver` package

**Services affected:** event-admin
**Location:** `event-admin/event_admin/db/__init__.py:1-11`
**Source audit:** event-admin_audit

Creates a hard runtime dependency on event-saver being installed. Violates service isolation. The local `db/models.py` already duplicates the ORM definitions correctly.

**Recommendation:** Delete the cross-service import. Re-export only from local `event_admin.db.models` and `event_admin.db.base`.

---

### C-14: `debug=True` in `.env` disables all authentication

**Services affected:** event-admin
**Location:** `event-admin/.env:2`, `event-admin/event_admin/middleware.py:30-31`
**Source audit:** event-admin_audit

The committed `.env` file sets `DEBUG=True`. `JWTAuthMiddleware.dispatch()` bypasses all JWT validation when debug is true. All booking data accessible without credentials.

**Recommendation:** Remove `DEBUG=True` from `.env`. Add `.env` to `.gitignore`. Add a startup assertion refusing `debug=True` without explicit `ALLOW_DEBUG=1`.

---

## HIGH Findings

---

### H-1: AES decryption errors unhandled in CRM sync -- silent partial syncs

**Services affected:** event-users
**Location:** `event-users/event_users/crm/sync.py:30-55,64-99`
**Source audit:** event-users_audit

`decrypt_payload` does not catch `ValueError`, `InvalidUnpadding`, `binascii.Error`, or `json.JSONDecodeError`. Failures crash mid-sync with no alerting, no rollback. Already-committed pages persist as partial data.

**Recommendation:** Add specific exception handling. Log failure cause. Add `last_successful_sync_at` for staleness detection. Consider aborting full sync on decryption failure.

---

### H-2: CRM sync row-by-row non-transactional; 10-second default interval (not 5 minutes)

**Services affected:** event-users
**Location:** `event-users/event_users/crm/sync.py:117-132`, `event-users/event_users/config.py:38`
**Source audit:** event-users_audit

Each user upserted in a separate commit (see C-5). Default interval is 10 seconds, not 300 as documented. No exponential backoff on errors -- a 1-hour CRM outage generates ~360 failed requests.

**Recommendation:** Fix default to 300. Implement exponential backoff with jitter. Use batch transactions.

---

### H-3: `list_users` N+1 queries (1 + N contact fetches)

**Services affected:** event-users, event-admin-frontend
**Location:** `event-users/event_users/adapters/users_db.py:209-212`
**Source audit:** event-users_audit

One `_fetch_contacts()` query per user. With limit 500, that is 502 sequential DB round trips per request.

**Recommendation:** Replace with batch `WHERE user_id = ANY(:ids)` query, group in Python.

---

### H-4: `ingest_getstream` raises `KeyError` on missing `X-SIGNATURE` header

**Services affected:** event-receiver
**Location:** `event-receiver/event_receiver/controllers/ingest.py:201`
**Source audit:** event-receiver_audit

Direct dict access `headers["X-SIGNATURE"]` raises `KeyError` (not `IngestError`), resulting in HTTP 500 instead of 401.

**Recommendation:** Use `headers.get("X-SIGNATURE")`, check for `None`, raise `UnauthorizedError`.

---

### H-5: No retry or circuit-breaker on event-users HTTP calls in publish path

**Services affected:** event-receiver, event-users
**Location:** `event-receiver/event_receiver/adapters/users_client.py:17-48`
**Source audit:** event-receiver_audit

A transient event-users failure causes unhandled exceptions in the publish path, returning 500 to webhook callers. `tenacity` is in dependencies but unused.

**Recommendation:** Wrap with `tenacity.retry`. Catch HTTP errors and raise domain error mapping to 503.

---

### H-6: No idempotency enforcement at event-receiver layer

**Services affected:** event-receiver, event-saver
**Location:** `event-receiver/event_receiver/adapters/publisher.py:67-71`
**Source audit:** event-receiver_audit

Idempotency key generated but never stored or checked. Duplicate webhook deliveries produce duplicate RabbitMQ messages. event-saver dedup constraint may not cover all scenarios.

**Recommendation:** Document that dedup is event-saver's responsibility and verify constraint coverage. If needed, add short-lived in-memory/Redis dedup store.

---

### H-7: RabbitMQ broker connect failure at startup -- no retry

**Services affected:** event-receiver
**Location:** `event-receiver/event_receiver/main.py:73-74`
**Source audit:** event-receiver_audit

`await broker.connect()` with no retry, timeout, or error handling. If RabbitMQ is unavailable, crash loop ensues.

**Recommendation:** Wrap in retry loop with exponential backoff using `tenacity.AsyncRetrying`.

---

### H-8: `events.notifications` phantom queue declared and bound at startup

**Services affected:** event-receiver
**Location:** `event-receiver/event_receiver/adapters/publisher.py:161-196`
**Source audit:** event-receiver_audit

Because routing destinations include `events.notifications`, topology manager declares this queue, creates a DLQ for it, and binds it. No consumer (from event-saver) will ever drain it.

**Recommendation:** Fix root cause (C-1). Add startup validation of routing destinations.

---

### H-9: Application layer imports infrastructure concrete classes (Clean Architecture violation)

**Services affected:** event-saver
**Location:** `event_saver/application/use_cases/ingest_event.py:11`, `event_saver/application/services/projection_executor.py:8`
**Source audit:** event-saver_audit

`IngestEventUseCase` imports concrete `BookingRepository` and `EventRepository`. `ProjectionExecutor` imports `BaseProjection` from infrastructure.

**Recommendation:** Define repository Protocols in `interfaces/`. Wire concrete classes only in `ioc.py`.

---

### H-10: `booking.rescheduled` not in event-saver's `EventType` enum

**Services affected:** event-saver
**Location:** `event_saver/config.py:18`, `event_saver/event_types.py`
**Source audits:** event-saver_audit, event-schemas_audit

Hardcoded string `"booking.rescheduled"` in routing rules while all other types use enum members. `BOOKING_RESCHEDULED` missing from enum.

**Recommendation:** Add `BOOKING_RESCHEDULED` to the enum. Replace string literal.

---

### H-11: No DLQ configured in event-saver consumer

**Services affected:** event-saver
**Location:** `event_saver/adapters/consumer.py:51-68`
**Source audit:** event-saver_audit

No `x-dead-letter-exchange` or retry configuration. Failed messages are either discarded or requeued indefinitely. A single malformed message can block processing.

**Recommendation:** Configure DLQ exchange and routing key. Set `x-delivery-limit` arguments.

---

### H-12: `IEventProjectionStatementFactory` orphaned legacy interface

**Services affected:** event-saver
**Location:** `event_saver/interfaces/projection.py:16-29`
**Source audit:** event-saver_audit

Never implemented, never called. Different signature from current projection system. Misleads developers.

**Recommendation:** Delete from `interfaces/projection.py` and `interfaces/__init__.py`.

---

### H-13: `require_admin` dependency defined but never used -- RBAC is dead code

**Services affected:** event-admin
**Location:** `event-admin/event_admin/auth.py:60-63`, routes.py
**Source audit:** event-admin_audit

None of the bookings routes inject `require_admin`. A user with `role=user` can access all data. The admin/user role distinction is unused.

**Recommendation:** Add `Depends(require_admin)` to all bookings routes.

---

### H-14: `list_bookings` has no pagination -- unbounded query

**Services affected:** event-admin, event-admin-frontend
**Location:** `event-admin/event_admin/adapters/bookings_db.py:29-75`
**Source audits:** event-admin_audit, event-admin-frontend_audit

`GET /bookings` has no `LIMIT` or `OFFSET`. As bookings grow, unbounded memory consumption and slow responses.

**Recommendation:** Add limit/offset or cursor-based pagination. Enforce hard maximum page size.

---

### H-15: getUserById calls wrong URL -- broken endpoint

**Services affected:** event-admin-frontend, event-users
**Location:** `event-admin-frontend/src/modules/participants/participantsApi.ts:57`
**Source audit:** event-admin-frontend_audit

Frontend calls `GET /api/users/${id}` but backend expects `GET /api/users/id/{user_id}`. UserInfo component silently non-functional on every page.

**Recommendation:** Change to `/api/users/id/${encodeURIComponent(id)}`.

---

### H-16: JWT stored in localStorage -- XSS exfiltration risk

**Services affected:** event-admin-frontend
**Location:** `event-admin-frontend/src/modules/auth/storage.ts:1-26`
**Source audit:** event-admin-frontend_audit

Any XSS vulnerability can exfiltrate the JWT and role, enabling session hijacking.

**Recommendation:** Prefer `httpOnly` session cookies. If localStorage required, ensure strict CSP headers.

---

### H-17: JWT expiry not handled client-side -- silent failures mid-session

**Services affected:** event-admin-frontend
**Location:** `event-admin-frontend/src/modules/shared/api.ts:55-61`
**Source audit:** event-admin-frontend_audit

No global 401 interceptor. Expired JWT leaves user with broken UI and stale token. No redirect to login.

**Recommendation:** Add 401 interceptor in `apiRequest`: call `logout()` and redirect to `/login`.

---

### H-18: RBAC enforced only client-side for /participants route

**Services affected:** event-admin-frontend, event-users
**Location:** `event-admin-frontend/src/App.tsx:41-43`
**Source audit:** event-admin-frontend_audit

Client-side nav hiding and redirect only. Backend `GET /api/users` has no role check. Any authenticated JWT can read all user data.

**Recommendation:** Enforce role check server-side in event-users read endpoints.

---

### H-19: No delivery result events published -- architecture contract broken

**Services affected:** event-notifier, event-receiver
**Location:** `event-notifier/event_notifier/adapters/outbox_sender.py`
**Source audit:** event-notifier_audit

CLAUDE.md describes publishing `notification.*.message_sent` events back to event-receiver. No `publisher.py` exists. The delivery result pipeline is absent.

**Recommendation:** Implement `ResultEventPublisher` or remove the architecture documentation and routing rules.

---

### H-20: `processed_events` table has no TTL or cleanup -- unbounded growth

**Services affected:** event-notifier
**Location:** `event-notifier/event_notifier/db/schema.py:19-22`
**Source audit:** event-notifier_audit

Every processed `cloud_event_id` stored permanently. No expiry despite docs mentioning "TTL 7 days".

**Recommendation:** Add scheduled cleanup: `DELETE WHERE processed_at < NOW() - INTERVAL '7 days'`.

---

### H-21: `get_contacts_by_id` returns empty on event-users outage -- notification silently lost

**Services affected:** event-notifier, event-users
**Location:** `event-notifier/event_notifier/infrastructure/users_client.py:70-86`
**Source audit:** event-notifier_audit

When all contact lookups fail, `outbox_records` is empty, event is not marked processed, but the message is ACKed by FastStream. Event permanently lost.

**Recommendation:** Raise on infrastructure errors (5xx/timeout) so FastStream nacks the message. Distinguish from 404.

---

### H-22: Consumer ACK behavior on exceptions is undocumented and likely incorrect

**Services affected:** event-notifier
**Location:** `event-notifier/event_notifier/adapters/consumer.py:55-85`
**Source audit:** event-notifier_audit

No explicit `ack_policy`, no prefetch, no DLQ. Unparseable messages may requeue infinitely (poison-pill) or be silently dropped.

**Recommendation:** Set explicit `ack_policy`. Catch parse errors and ACK (discard). Nack transient errors. Configure DLQ.

---

### H-23: No HTTP timeouts explicitly configured on external API calls

**Services affected:** event-notifier
**Location:** `event-notifier/event_notifier/ioc.py:54-66`, infrastructure channels
**Source audit:** event-notifier_audit

All `httpx.AsyncClient` instances use default 5s timeout. No connect/read/write distinction. Slow APIs stall the event loop.

**Recommendation:** Explicitly pass `httpx.Timeout(connect=3.0, read=10.0, write=5.0)`.

---

### H-24: No EventType-to-Pydantic-model mapping exists

**Services affected:** event-schemas, event-receiver, event-saver
**Location:** `event-schemas/event_schemas/__init__.py`
**Source audit:** event-schemas_audit

No programmatic mapping from EventType to payload model. Normalizer uses ad-hoc `match` statements. New event types can be added without corresponding models.

**Recommendation:** Add `EVENT_TYPE_TO_MODEL: dict[EventType, type[BaseModel]]` in event-schemas.

---

## MEDIUM Findings

---

### M-1: CORS wildcard `allow_origins=["*"]` with `allow_credentials=True`

**Services affected:** event-receiver, event-admin, event-users
**Location:** `event-receiver/main.py:95-99`, `event-admin/main.py:46-52`, `event-users/main.py:59-65`
**Source audits:** event-receiver_audit, event-admin_audit, event-users_audit

This combination is forbidden by the CORS spec. Browsers refuse credentialed requests with wildcard origins. Both overly permissive and functionally broken.

**Recommendation:** Replace with explicit list of trusted origins. Remove `allow_credentials=True` if wildcard is intentional.

---

### M-2: `EVENTS_DIGEST.md` / `QUEUES_DIGEST.md` payload schemas do not match ingestion code

**Services affected:** event-receiver
**Location:** `event-receiver/EVENTS_DIGEST.md:18-29` vs `ingest.py:148-160`
**Source audit:** event-receiver_audit

Docs specify structured `user.email` / `client.email` objects. Code expects flat `users[]` list with `role` and `email`.

**Recommendation:** Update docs to reflect actual accepted format.

---

### M-3: `PROJECT_CONTEXT.md` documents non-existent `/event/cloudevents` endpoint

**Services affected:** event-receiver
**Location:** `event-receiver/PROJECT_CONTEXT.md:33-42`
**Source audit:** event-receiver_audit

Endpoint does not exist in routes.py. The service advertises a capability it does not provide.

**Recommendation:** Implement the endpoint or remove all references from docs.

---

### M-4: `QUEUES_DIGEST.md` source_pattern column incorrect

**Services affected:** event-receiver
**Location:** `event-receiver/QUEUES_DIGEST.md:9-16`
**Source audit:** event-receiver_audit

Shows `*` but config.py uses `source_pattern="booking"`.

**Recommendation:** Update QUEUES_DIGEST.md to show actual source patterns.

---

### M-5: `ingest_jitsi` double-decode and security gap

**Services affected:** event-receiver
**Location:** `event-receiver/event_receiver/controllers/ingest.py:47,64-68`
**Source audit:** event-receiver_audit

Token decoded twice: `verify_signature` then `verify` with `verify_signature=False`. Architecturally fragile.

**Recommendation:** Refactor `verify()` to accept pre-parsed claims.

---

### M-6: `ingest_booking` mutates `incoming.data` in place via `.pop()`

**Services affected:** event-receiver
**Location:** `event-receiver/event_receiver/controllers/ingest.py:111`
**Source audit:** event-receiver_audit

Mutates the CloudEvent's data dict. If referenced elsewhere, `booking_uid` will be missing.

**Recommendation:** Create a shallow copy before `.pop()`.

---

### M-7: `pyproject.toml` pins event-schemas via local absolute path

**Services affected:** event-receiver
**Location:** `event-receiver/pyproject.toml:11`
**Source audit:** event-receiver_audit

Uses `/Users/alexandrlelikov/PycharmProjects/events/event-schemas`. Breaks in CI/Docker/other machines.

**Recommendation:** Use multi-stage Docker build, private PyPI, or git+https form for non-local environments.

---

### M-8: `ingest_unisender_go` missing `event_id` and `event_time` -- CloudEvents spec violation

**Services affected:** event-receiver, event-saver
**Location:** `event-receiver/event_receiver/controllers/ingest.py:185-193`
**Source audit:** event-receiver_audit

Publishes CloudEvents without required `id` and `time` attributes. event-saver raises `KeyError` on missing `time`.

**Recommendation:** Generate `event_id = str(uuid.uuid4())` and `event_time = datetime.now(UTC).isoformat()`.

---

### M-9: `normalizers.py` silently returns empty participants on errors

**Services affected:** event-receiver, event-saver
**Location:** `event-receiver/event_receiver/normalizers.py:45-49`
**Source audit:** event-receiver_audit

Schema mismatches silently produce events with no participants. Makes regressions invisible.

**Recommendation:** Log at `warning` level. Consider hard errors for CRITICAL event types.

---

### M-10: Dedup hash computed differently in Python vs PostgreSQL

**Services affected:** event-saver
**Location:** `event_saver/domain/services/event_parser.py:82-85`
**Source audit:** event-saver_audit

Python uses `ujson.dumps` + `md5`. DB constraint uses `md5(payload::text)`. Different serializations can produce different hashes, breaking dedup for legacy events.

**Recommendation:** Align hash function. Either compute consistently in Postgres or verify all existing rows.

---

### M-11: `ProjectionExecutor` silently swallows projection failures

**Services affected:** event-saver
**Location:** `event_saver/application/services/projection_executor.py:60-66`
**Source audit:** event-saver_audit

Bare `except Exception: logger.exception(...)` with no re-raise, no metric, no recovery path.

**Recommendation:** Emit structured metric on failure. Document whether projection failure should nack the message.

---

### M-12: `IngestEventUseCase` partial state on booking upsert failure

**Services affected:** event-saver
**Location:** `event_saver/application/use_cases/ingest_event.py:93-127`
**Source audit:** event-saver_audit

If `upsert()` raises after some projections committed (due to C-5), partial state is not rolled back.

**Recommendation:** Fix C-5 first. Then a single `session.commit()` makes all writes atomic.

---

### M-13: `TelegramNotificationProjection.handle()` returns SQL with `user_id=None`

**Services affected:** event-saver
**Location:** `event_saver/infrastructure/persistence/projections/notification_projection.py:199`
**Source audit:** event-saver_audit

No guard for `None` user_id. Causes DB error or useless rows.

**Recommendation:** Add `if user_id is None: return None` guard.

---

### M-14: `BookingDataExtractor` only maps two event types to status

**Services affected:** event-saver
**Location:** `event_saver/domain/services/booking_extractor.py:9-12`
**Source audit:** event-saver_audit

Only `booking.created` and `booking.cancelled` produce a status. `rescheduled`, `reassigned`, `reminder_sent` produce `status=None`.

**Recommendation:** Document intentional omissions or add entries for all event types.

---

### M-15: `declare=False` on event-saver consumer queues

**Services affected:** event-saver
**Location:** `event_saver/adapters/consumer.py:57`
**Source audit:** event-saver_audit

If queues don't pre-exist, consumer crashes or silently fails. No startup check.

**Recommendation:** Either `declare=True` with full arguments, or ensure topology manager runs before consumer startup.

---

### M-16: CLAUDE.md references non-existent files in event-saver

**Services affected:** event-saver
**Location:** `event_saver/CLAUDE.md`
**Source audit:** event-saver_audit

References `domain/models/participant.py` and `infrastructure/persistence/repositories/participant_repository.py` which don't exist.

**Recommendation:** Update docs to reflect actual architecture.

---

### M-17: `get_booking_details` N+1 pattern (7 sequential DB round-trips)

**Services affected:** event-admin
**Location:** `event-admin/event_admin/adapters/bookings_db.py:77-352`
**Source audit:** event-admin_audit

7 sequential queries per booking detail request.

**Recommendation:** Use `asyncio.gather` for parallel execution or combine with JOINs.

---

### M-18: `BookingDetailsDto` mutable list fields violate frozen-dataclass contract

**Services affected:** event-admin
**Location:** `event-admin/event_admin/dto/bookings.py:79-82,118-136`
**Source audit:** event-admin_audit

`frozen=True` only prevents field reassignment; `list` contents remain mutable.

**Recommendation:** Replace `list[...]` with `tuple[..., ...]` in frozen DTOs.

---

### M-19: JWT default secret in event-admin config (duplicate of C-6 detail)

**Services affected:** event-admin
**Location:** `event-admin/event_admin/config.py:29`
**Source audit:** event-admin_audit

Covered by C-6. Additionally: `jwt_expire_minutes` defaults to 24 hours (excessively long for admin token with no revocation).

---

### M-20: `BookingDetailsResponse` silently drops fields from DTO

**Services affected:** event-admin
**Location:** `event-admin/event_admin/schemas/bookings.py:196-239`
**Source audit:** event-admin_audit

`first_seen_at`, `last_seen_at`, `updated_at` and other fields omitted without documentation.

**Recommendation:** Audit each `from_dto()`. Add comments for intentional omissions.

---

### M-21: `admin_users` table has no proper Alembic migration

**Services affected:** event-admin, event-saver
**Location:** `event-admin/event_admin/db/models.py:11-26`
**Source audits:** event-admin_audit, x2_data_ownership

Migration SQL in docstring. `admin_users` managed by event-saver's alembic branch, coupling the services.

**Recommendation:** Give event-admin its own alembic instance for `admin_users`.

---

### M-22: Login endpoint does not log failed authentication attempts

**Services affected:** event-admin
**Location:** `event-admin/event_admin/routes.py:37-53`
**Source audit:** event-admin_audit

No structured log for brute-force detection.

**Recommendation:** Add `logger.warning("login_failed", ...)` for each failure branch.

---

### M-23: No request ID / correlation ID in event-admin logs

**Services affected:** event-admin
**Location:** `event-admin/event_admin/middleware.py`
**Source audit:** event-admin_audit

No per-request UUID in structlog context.

**Recommendation:** Add middleware generating `uuid.uuid4()` per request via `structlog.contextvars`.

---

### M-24: Double JWT validation in event-users (middleware + route dependency)

**Services affected:** event-users
**Location:** `event-users/event_users/middleware.py:12-40`, `event-users/event_users/auth.py:27-47`
**Source audit:** event-users_audit

JWT decoded twice per request. `/health` endpoint gated by JWT, breaking liveness probes.

**Recommendation:** Centralize auth. Add `/health` to `public_paths`.

---

### M-25: `upsert_user_from_crm` COALESCE silently ignores CRM null updates

**Services affected:** event-users
**Location:** `event-users/event_users/adapters/users_db.py:229-231`
**Source audit:** event-users_audit

CRM-intentional nulls are preserved as existing values. Stale data accumulates.

**Recommendation:** Use direct assignment if CRM is source of truth. Document the contract.

---

### M-26: `crm_encryption_key` not validated at startup

**Services affected:** event-users
**Location:** `event-users/event_users/ioc.py:101`
**Source audit:** event-users_audit

Invalid hex or wrong length only surfaces at first sync attempt, minutes after startup.

**Recommendation:** Add `field_validator` ensuring valid hex decoding to 32 bytes.

---

### M-27: `update_user` PATCH semantics on a PUT endpoint

**Services affected:** event-users
**Location:** `event-users/event_users/adapters/users_db.py:108-143`
**Source audit:** event-users_audit

None fields silently skipped instead of clearing. Semantic mismatch.

**Recommendation:** Rename to PATCH, or use `model_fields_set` to distinguish absent from null.

---

### M-28: getBookingDetails silently retries on 404

**Services affected:** event-admin-frontend
**Location:** `event-admin-frontend/src/modules/bookings/bookingsApi.ts:33-40`
**Source audit:** event-admin-frontend_audit

404 is deterministic; retry is dead code adding latency.

**Recommendation:** Remove the catch block's retry. Propagate 404 directly.

---

### M-29: UserInfo N+1 HTTP requests (one per user per render)

**Services affected:** event-admin-frontend, event-users
**Location:** `event-admin-frontend/src/modules/shared/UserInfo.tsx:13-30`
**Source audit:** event-admin-frontend_audit

Multiple `GET /api/users/id/{userId}` per page load with no deduplication or caching.

**Recommendation:** Add client-side cache keyed by userId (React Query, SWR, or useRef Map).

---

### M-30: Dev bypass login hardcodes role as "admin" + no MODE guard

**Services affected:** event-admin-frontend
**Location:** `event-admin-frontend/src/modules/auth/LoginPage.tsx:7,98`
**Source audit:** event-admin-frontend_audit

Dev bypass grants admin role regardless of token contents. No `import.meta.env.DEV` guard; can ship to production if env var set.

**Recommendation:** Decode role from token. Wrap in `import.meta.env.DEV` check.

---

## LOW Findings

---

### L-1: No automated tests (all services)

**Services affected:** event-receiver, event-saver, event-admin, event-admin-frontend, event-users, event-notifier, event-schemas
**Source audits:** All service audits

No service has meaningful test coverage. Per-service priorities:

| Service | Priority tests |
|---------|---------------|
| event-receiver | Routing logic, auth methods, normalizer edge cases |
| event-saver | EventParser hash computation, ProjectionExecutor exception handling, EventRepository idempotency |
| event-admin | PasswordService, TOTPService, JWTAuthMiddleware debug bypass |
| event-admin-frontend | parseRoute routing, apiRequest 401 handling, URL construction |
| event-users | decrypt_payload (correct/wrong key/bad JSON), verify_bearer_token, upsert idempotency |
| event-notifier | Consumer CloudEvent parsing, booking_id extraction fallbacks |
| event-schemas | EventType completeness in EVENT_PRIORITIES/SCHEMA_VERSIONS, enum spelling, model coverage |

**Recommendation:** Add test suites to all services. The routing bug (C-1), broken except syntax (C-8), and wrong getUserById URL (H-15) would all have been caught by basic tests.

---

### L-2: `logger.py` suppresses irrelevant packages (aiokafka, asyncio_redis, botocore)

**Services affected:** event-receiver, event-admin
**Location:** `event-receiver/event_receiver/logger.py:67-71`, `event-admin/event_admin/logger.py:72-73`
**Source audits:** event-receiver_audit, event-admin_audit

Copy-paste from another service. None of these packages are used.

**Recommendation:** Remove. Add `aio_pika` / `faststream` suppression if needed.

---

### L-3: `schemas.py` empty placeholder in event-receiver

**Services affected:** event-receiver
**Location:** `event-receiver/event_receiver/schemas.py`
**Source audit:** event-receiver_audit

Imported by nothing. Contains only a docstring.

**Recommendation:** Remove until needed.

---

### L-4: CLAUDE.md service name inconsistency (`event-manager` vs `event-receiver`)

**Services affected:** event-receiver
**Location:** `event-receiver/CLAUDE.md:3`
**Source audit:** event-receiver_audit

Docs call it `event-manager`; package/docker/app all use `event-receiver`.

**Recommendation:** Standardize on `event-receiver`.

---

### L-5: `ioc.py` uses bare `Callable` type annotation

**Services affected:** event-receiver
**Location:** `event-receiver/event_receiver/ioc.py:80,108`
**Source audit:** event-receiver_audit

Prevents type checking of getstream decoder signature.

**Recommendation:** Replace with `Callable[[str], str]`.

---

### L-6: `IngestController` at `Scope.REQUEST` with no per-request state

**Services affected:** event-receiver
**Location:** `event-receiver/event_receiver/ioc.py:133-145`
**Source audit:** event-receiver_audit

All fields are `Scope.APP` singletons. Per-request allocation is unnecessary overhead.

**Recommendation:** Change to `Scope.APP`.

---

### L-7: `ioc.py` / `ioc_new.py` documentation confusion in event-saver

**Services affected:** event-saver
**Location:** `CLAUDE.md:121,130,258`
**Source audit:** event-saver_audit

CLAUDE.md references `ioc_new.py` (never created) and calls `ioc.py` "legacy". In reality `ioc.py` IS the clean-architecture container.

**Recommendation:** Replace all `ioc_new.py` references with `ioc.py`. Remove "Legacy" labels.

---

### L-8: `adapters/event_store.py` listed as "legacy to remove" but already deleted

**Services affected:** event-saver
**Location:** `CLAUDE.md`
**Source audit:** event-saver_audit

Stale documentation. File was already deleted per REFACTORING_SUMMARY.md.

**Recommendation:** Remove legacy section from CLAUDE.md.

---

### L-9: `EventRouter` and `IEventRouter` wired but never called in event-saver

**Services affected:** event-saver
**Location:** `event_saver/ioc.py:93-109`
**Source audit:** event-saver_audit

Publisher exists for potential outbound re-publishing which is not implemented. Adds unused complexity.

**Recommendation:** Remove or add a TODO with ticket reference.

---

### L-10: `_parse_occurred_at` duplicated between consumer.py and event_parser.py

**Services affected:** event-saver
**Location:** `event_saver/adapters/consumer.py:21-29`, `event_saver/domain/services/event_parser.py:70-79`
**Source audit:** event-saver_audit

Identical function in two places. Second parse is a no-op.

**Recommendation:** Remove from `consumer.py`. Let `EventParser` do the single authoritative parse.

---

### L-11: QUEUES_DIGEST.md in event-saver omits 2 queue names

**Services affected:** event-saver
**Location:** `QUEUES_DIGEST.md`
**Source audit:** event-saver_audit

Missing `events.chat` (GetStream) and `events.mail` (UniSender) from summary table.

**Recommendation:** Add to match all 10 routing destinations.

---

### L-12: `GET /auth/logout` is a no-op with no session invalidation

**Services affected:** event-admin
**Location:** `event-admin/event_admin/routes.py:56-58`
**Source audit:** event-admin_audit

Returns 204 but does nothing. JWTs are stateless with 24-hour expiry and no revocation.

**Recommendation:** Implement token blocklist, or shorten JWT TTL and add refresh tokens.

---

### L-13: `CrmClient.fetch_users` creates new httpx client per call

**Services affected:** event-users
**Location:** `event-users/event_users/crm/client.py:24-45`
**Source audit:** event-users_audit

New TCP connection + TLS handshake per page. Wasteful for paginated sync.

**Recommendation:** Instantiate client once in `__init__`, close in `close()`.

---

### L-14: `get_user_by_email_role` uses path params for email -- special chars break routing

**Services affected:** event-users
**Location:** `event-users/event_users/routes.py:67`
**Source audit:** event-users_audit

Emails with `+`, `.`, `%` may be decoded inconsistently by proxies.

**Recommendation:** Use query parameters or document required encoding.

---

### L-15: `/health` endpoint protected by JWT middleware in event-users

**Services affected:** event-users
**Location:** `event-users/event_users/main.py:58`, `middleware.py:23-24`
**Source audit:** event-users_audit

Liveness probes will receive 401 without a token.

**Recommendation:** Add `/health` to `public_paths`.

---

### L-16: `GETSTREAM_CHANEL_CREATED/DELETED` misspelled (should be CHANNEL)

**Services affected:** event-schemas, event-receiver
**Location:** `event-schemas/event_schemas/types.py:35-36`
**Source audit:** event-schemas_audit

Missing second "N" in CHANNEL. String values are correct but Python identifiers have typo.

**Recommendation:** Rename to `GETSTREAM_CHANNEL_CREATED` / `GETSTREAM_CHANNEL_DELETED`.

---

### L-17: EVENT_PRIORITIES and EVENT_SCHEMA_VERSIONS missing entries

**Services affected:** event-schemas
**Location:** `event-schemas/event_schemas/types.py:86-140`
**Source audit:** event-schemas_audit

`GETSTREAM_CHANEL_CREATED` and `GETSTREAM_CHANEL_DELETED` missing from both maps. Silent fallback to defaults.

**Recommendation:** Add explicit entries. Add completeness assertion test.

---

### L-18: `NOTIFICATION_SERVICE_ARCHITECTURE.md` is stale and misleading

**Services affected:** event-notifier
**Location:** `event-notifier/NOTIFICATION_SERVICE_ARCHITECTURE.md`
**Source audit:** event-notifier_audit

Describes a completely different design (meeting.* events, Jinja2, aiohttp, WhatsApp). None exists in current code.

**Recommendation:** Archive or replace with current documentation.

---

## Per-Service Summary

| Service | CRITICAL | HIGH | MEDIUM | LOW | Top Concern |
|---|---|---|---|---|---|
| event-receiver | 4 | 5 | 9 | 6 | Routing rules shadow booking lifecycle queues (C-1) |
| event-saver | 2 | 4 | 6 | 5 | SqlExecutor double-commit breaks atomicity (C-5) |
| event-admin | 2 | 4 | 7 | 5 | DEBUG=True disables all auth (C-14) |
| event-admin-frontend | 2 | 3 | 5 | 4 | getUserById calls wrong URL (H-15) |
| event-users | 2 | 4 | 5 | 5 | Hardcoded JWT default secret (C-6) |
| event-notifier | 3 | 5 | 6 | 4 | FOR UPDATE without transaction (C-11) |
| event-schemas | 3 | 6 | 6 | 3 | Dual EventType enums (C-9) |

**Note:** Some findings span multiple services. The counts above reflect findings where each service is affected, not deduplicated totals. The deduplicated totals are in the Executive Summary.

---

## Cross-Cutting Findings

### Message Topology (x1_message_topology)

The message topology audit identified 6 contract inconsistencies, all of which are covered by findings above:

- **IC-1: First-match routing shadows `events.booking.lifecycle`** -- see **C-1**
- **IC-2: event-notifier consumer queue mismatch** -- see **C-3**
- **IC-3: QUEUES_DIGEST documentation errors** -- see **M-2**, **M-4**
- **IC-4: event-saver topology manager does not create DLQ/priority queues** -- see **H-11**. Additionally, conflicting queue declaration arguments between event-receiver (with `x-max-priority=10` and DLX) and event-saver (plain durable) can cause RabbitMQ declaration conflicts if both services declare the same queue.
- **IC-5: `events.notifications` not in event-saver's default routing config** -- consequence of **C-1**. event-saver never subscribes to the queue that actually receives booking lifecycle events.
- **IC-6: event-notifier `infrastructure/publisher.py` does not exist** -- see **H-19**

**Orphaned queues:** `events.booking.lifecycle`, `events.booking.reminder`, `events.notification.commands` receive no messages or have no consumer due to the routing and naming bugs above.

### Data Ownership (x2_data_ownership)

Key findings from the data ownership audit:

- **event-admin uses same PostgreSQL user as event-saver** -- covered by **C-5** (event-admin has write methods) and **H-13** (no role enforcement). Specific recommendation: create `event_admin_ro` role with SELECT-only privileges.
- **No database-level FK from event-saver user_id columns to event-users** -- two separate PostgreSQL instances; cross-DB FKs impossible. User deletion in event-users leaves orphaned UUID references. See **M-21** for related migration coupling.
- **`admin_users` table managed by event-saver's alembic** -- see **M-21**
- **event-admin-frontend calls event-users directly without gateway** -- see **H-15** (wrong URL) and **H-18** (no server-side RBAC). Missing `VITE_USERS_API_BASE_URL` causes silent misconfiguration.
- **event-users `.env.example` has wrong database name** (`zhivaya-admin` instead of `zhivaya-users`, port 5439 instead of 5446) -- a developer following this would point event-users at event-saver's database, risking data corruption.
- **event-notifier has no migration framework** -- raw SQL bootstrap in `db/schema.py`. No rollback capability or version tracking.

### Dependency Graph (x3_dependency_graph)

Key architectural observations:

- **No circular dependencies** exist in the currently active code. The planned `event-notifier -> event-receiver` feedback loop (delivery results) is not implemented.
- **Single points of failure:** RabbitMQ and PostgreSQL [main DB] are the two components whose failure halts the entire system.
- **Minimum viable subset for booking receipt:** event-receiver + RabbitMQ + event-saver + PostgreSQL [main DB]. All other services can fail independently.
- **Shared JWT secret coupling:** event-admin-frontend forwards admin user JWTs to event-users. Both services must share the same JWT secret. This is implicit coupling with no explicit documentation.
- **event-schemas runtime dependency:** event-receiver and event-saver fail to start if event-schemas is not installed. It is a compile-time/import-time dependency, not a runtime service.
