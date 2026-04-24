# Architecture Audit & Documentation Sprint — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Audit all 7 services for architectural, contract, scalability, and resilience issues; remediate CRITICAL and HIGH findings; then produce comprehensive living documentation of the cleaned-up system.

**Architecture:** Two sequential phases gated by a user review checkpoint. Phase 1 dispatches 10 parallel subagents (7 service + 3 cross-cutting) to produce structured audit findings, then applies discovered fixes. Phase 2 dispatches 9 parallel subagents to produce living documentation of the post-remediation state.

**Tech Stack:** Python 3.14, FastAPI, FastStream, RabbitMQ, PostgreSQL, SQLAlchemy 2.x async, Dishka, Pydantic v2, TypeScript/React/Vite, structlog, Ruff, uv

---

## File Map

### Created by Phase 1

```
docs/audit/raw/                          # individual agent outputs (scratch space)
  event-receiver_audit.md
  event-saver_audit.md
  event-admin_audit.md
  event-admin-frontend_audit.md
  event-users_audit.md
  event-notifier_audit.md
  event-schemas_audit.md
  x1_message_topology.md
  x2_data_ownership.md
  x3_dependency_graph.md
docs/audit/
  AUDIT_REPORT.md                        # all findings, grouped by severity+category
  CONTRACT_MAP.md                        # complete message contract map
  SCALABILITY_GAPS.md                    # bottlenecks with specific fixes
  DEPENDENCY_GRAPH.md                    # Mermaid service dependency diagram
```

### Created by Phase 2

```
docs/architecture/
  ARCHITECTURE.md
  MESSAGE_CONTRACTS.md
  CODING_STANDARDS.md
  ONBOARDING.md
  INDEX.md
  services/
    event-receiver/
      SERVICE_OVERVIEW.md
      API_CONTRACTS.md
      DATA_MODEL.md
      DEPENDENCIES.md
    event-saver/
      SERVICE_OVERVIEW.md
      API_CONTRACTS.md
      DATA_MODEL.md
      DEPENDENCIES.md
    event-admin/
      SERVICE_OVERVIEW.md
      API_CONTRACTS.md
      DATA_MODEL.md
      DEPENDENCIES.md
    event-admin-frontend/
      SERVICE_OVERVIEW.md
      API_CONTRACTS.md
      DEPENDENCIES.md
    event-users/
      SERVICE_OVERVIEW.md
      API_CONTRACTS.md
      DATA_MODEL.md
      DEPENDENCIES.md
    event-notifier/
      SERVICE_OVERVIEW.md
      API_CONTRACTS.md
      DEPENDENCIES.md
    event-schemas/
      SERVICE_OVERVIEW.md
      API_CONTRACTS.md
      DEPENDENCIES.md
```

### Modified by Phase 1 fixes (anticipated)

```
event-saver/ioc.py                       # delete (legacy, superseded by ioc_new.py)
event-saver/adapters/event_store.py      # delete (legacy, superseded by clean arch)
event-saver/main.py                      # update import if it references old ioc.py
event-notifier/infrastructure/publisher.py   # add error logging/alerting
```

---

## ═══════════════════════════════════════
## PHASE 1: AUDIT + REMEDIATION
## ═══════════════════════════════════════

---

### Task 1: Create output directory structure

**Files:**
- Create: `docs/audit/raw/` (directory)
- Create: `docs/architecture/services/` (directory tree)

- [ ] **Step 1: Create all output directories**

```bash
cd /Users/alexandrlelikov/PycharmProjects/events
mkdir -p docs/audit/raw
mkdir -p docs/architecture/services/event-receiver
mkdir -p docs/architecture/services/event-saver
mkdir -p docs/architecture/services/event-admin
mkdir -p docs/architecture/services/event-admin-frontend
mkdir -p docs/architecture/services/event-users
mkdir -p docs/architecture/services/event-notifier
mkdir -p docs/architecture/services/event-schemas
```

- [ ] **Step 2: Verify structure**

```bash
find docs/audit docs/architecture/services -type d | sort
```

Expected output:
```
docs/audit
docs/audit/raw
docs/architecture/services
docs/architecture/services/event-admin
docs/architecture/services/event-admin-frontend
docs/architecture/services/event-notifier
docs/architecture/services/event-receiver
docs/architecture/services/event-saver
docs/architecture/services/event-schemas
docs/architecture/services/event-users
```

- [ ] **Step 3: Commit**

```bash
git add docs/
git commit -m "chore: create audit and architecture docs directory structure"
```

---

### Task 2: Phase 1 — Dispatch service audit subagents S1–S7 in parallel

Use `superpowers:dispatching-parallel-agents` to run all 7 service audit subagents simultaneously. Each writes its findings to `docs/audit/raw/<service>_audit.md`.

**Files:**
- Create: `docs/audit/raw/event-receiver_audit.md`
- Create: `docs/audit/raw/event-saver_audit.md`
- Create: `docs/audit/raw/event-admin_audit.md`
- Create: `docs/audit/raw/event-admin-frontend_audit.md`
- Create: `docs/audit/raw/event-users_audit.md`
- Create: `docs/audit/raw/event-notifier_audit.md`
- Create: `docs/audit/raw/event-schemas_audit.md`

- [ ] **Step 1: Invoke dispatching-parallel-agents skill with the 7 prompts below**

Dispatch all 7 agents simultaneously. Each agent is independent — do not wait for one before starting another.

---

**S1 — event-receiver audit prompt:**

```
You are auditing the `event-receiver` service in a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/event-receiver/.

CONTEXT:
- Pre-production system — nothing is live yet. Code can be modified.
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read (faster, structural context).
- Do NOT modify any code. Audit only. Write findings to docs/audit/raw/event-receiver_audit.md.

WHAT TO ANALYZE:

1. ARCHITECTURE QUALITY
   - Single responsibility: does this service own one clear domain?
   - Coupling: does it reach into other services' databases or internal state?
   - Cohesion: are all responsibilities genuinely related?
   - Boundary violations: any logic that belongs elsewhere?

2. MESSAGE CONTRACT CONSISTENCY
   - Every RabbitMQ producer: exact exchange name, routing key, payload schema, versioning approach
   - Are routing rules (event_receiver/routing.py + config.py) consistent with what event-saver consumes?
   - Dead letter handling: is there a DLQ configured? What happens to failed publishes?
   - Verify: does the CloudEvents binary format (ce-* headers) match what event-saver's consumer.py expects?

3. SCALABILITY
   - Shared mutable state: any in-memory caches, module-level globals, or local file writes?
   - Idempotency: what happens if the same HTTP request is received twice?
   - Any requests that will degrade under load?

4. ERROR HANDLING AND RESILIENCE
   - How does the service handle RabbitMQ connection loss?
   - What happens if the broker is unavailable at startup?
   - Are webhook signature validation errors surfaced correctly?
   - What fails silently?

5. CODE STYLE AND CONSISTENCY
   - Naming conventions (controllers, adapters, interfaces)
   - Error raising/catching patterns (errors.py)
   - Config loading (config.py Settings class)
   - Test structure and coverage (what's tested vs untested)
   - Divergences from stated conventions in CLAUDE.md

SPECIFIC CONCERNS TO INVESTIGATE:
- Read event-receiver/QUEUES_DIGEST.md and event-receiver/EVENTS_DIGEST.md — are these accurate/up-to-date?
- Are all 5 auth methods (JWT, MD5, HMAC, API key) consistent in how they raise errors?
- Is there any hardcoded configuration that should be in Settings?
- Check event-receiver/PROJECT_CONTEXT.md for accuracy against current code.

OUTPUT FORMAT for each finding:
```
[SEVERITY] Short title

Services affected: list
Location: file/path:line-range
Description: what the problem is and why it matters
Recommendation: specific, actionable fix
```

Severity: CRITICAL (data loss/security/outage risk) | HIGH (reliability/scalability) | MEDIUM (maintainability) | LOW (style/minor)

Write all findings to: docs/audit/raw/event-receiver_audit.md
Start the file with: `# event-receiver Audit Findings\n\nAudited: 2026-04-19\n`
Group findings by: CRITICAL, HIGH, MEDIUM, LOW
End with a `## Summary` section: total finding count by severity, top 3 concerns.
```

---

**S2 — event-saver audit prompt:**

```
You are auditing the `event-saver` service in a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/event-saver/.

CONTEXT:
- Pre-production system — nothing is live yet. Code can be modified.
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Do NOT modify any code yet. Audit only. Write findings to docs/audit/raw/event-saver_audit.md.

WHAT TO ANALYZE:

1. ARCHITECTURE QUALITY
   - The CLAUDE.md notes a clean architecture refactor happened (see ioc_new.py vs ioc.py, and adapters/event_store.py vs new clean arch). Is the old code still referenced anywhere? What would break if it were deleted?
   - Are all layers (domain, application, infrastructure) properly separated — no domain importing from infrastructure?
   - Are all projections (MeetingLinkProjection, EmailNotificationProjection, etc.) independent and not sharing mutable state?
   - Is the projection system genuinely extensible, or does adding a projection require changes in multiple places?

2. MESSAGE CONTRACT CONSISTENCY
   - Every RabbitMQ consumer: queue name, binding, expected CloudEvent schema
   - Does consumer.py parse all the CloudEvent header fields it claims to? Trace the flow from raw message → ParsedEvent.
   - What happens if a message arrives with an unknown event type?
   - What happens if required CloudEvent headers are missing?
   - Dead letter handling: does FastStream configure a DLQ? What is the retry behavior?
   - Event deduplication: is the hash-based approach (md5(payload::text)) actually idempotent for all event types?

3. SCALABILITY
   - Are any repositories or projections holding in-memory state across requests?
   - The deduplication constraint: is it correct? Could two logically-different events hash to the same value?
   - Are there any N+1 query patterns in projections?
   - Does projection failure block raw event storage? (Check try/except boundaries in use case)

4. ERROR HANDLING AND RESILIENCE
   - What happens if PostgreSQL is unreachable?
   - What happens if a projection raises an unhandled exception — does it nak the message?
   - Is there logging for every failure path?
   - What happens during schema migrations while the service is running?

5. LEGACY CODE ASSESSMENT
   - Identify exactly which files are legacy: ioc.py, adapters/event_store.py, any others
   - For each: is it still imported anywhere? List every import site with file:line
   - What is the safe deletion order?
   - Are the existing docs (REFACTORING_SUMMARY.md, docs/architecture/C4_DIAGRAMS.md, docs/architecture/ARCHITECTURE_DECISION_RECORDS.md) accurate against current code? Flag any stale claims.

6. CODE STYLE AND CONSISTENCY
   - Does the new clean architecture match the patterns described in CLAUDE.md?
   - Are frozen dataclasses used consistently as DTOs?
   - Test coverage: what is tested? What critical paths have no tests?

SPECIFIC CONCERNS:
- Read event-saver/QUEUES_DIGEST.md — does it match the actual consumer queue bindings in code?
- Read event-saver/EVENTS_DIGEST.md — does it match the actual domain models?
- Check ioc_new.py vs ioc.py — which one is actually used in main.py?
- Check if GETSTREAM_USER_ID_ENCRYPTION_KEY is actually used and where.

OUTPUT FORMAT for each finding:
```
[SEVERITY] Short title

Services affected: list
Location: file/path:line-range
Description: what the problem is and why it matters
Recommendation: specific, actionable fix
```

Severity: CRITICAL | HIGH | MEDIUM | LOW

Write all findings to: docs/audit/raw/event-saver_audit.md
Start with: `# event-saver Audit Findings\n\nAudited: 2026-04-19\n`
Group by: CRITICAL, HIGH, MEDIUM, LOW
End with `## Summary` and `## Legacy Deletion Plan` (ordered list of files safe to delete and why).
```

---

**S3 — event-admin audit prompt:**

```
You are auditing the `event-admin` service in a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/event-admin/.

CONTEXT:
- Pre-production system — nothing is live yet. Code can be modified.
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Do NOT modify any code. Audit only. Write findings to docs/audit/raw/event-admin_audit.md.

WHAT TO ANALYZE:

1. ARCHITECTURE QUALITY
   - This service is described as read-only over event-saver's DB. Does it actually enforce read-only access at the DB connection level, or just by convention?
   - Does it share the same database connection credentials as event-saver? If so, is there a read-only user/role?
   - Are all controllers truly thin, or is business logic leaking into them?
   - Does it import anything from event-saver directly? Any shared code that should be in event-schemas?

2. API SURFACE
   - List every HTTP endpoint: method, path, auth mechanism, query parameters, response schema
   - Is there an OpenAPI/Swagger schema generated? Does it match actual behavior?
   - Is authentication implemented consistently across all endpoints?
   - What happens if a booking_id that doesn't exist is requested?

3. SCALABILITY
   - Are raw SQL queries in adapters/bookings_db.py potentially slow on large datasets?
   - Are there any missing indexes that would be needed for the query patterns used?
   - Is the SqlExecutor connection pool sized appropriately, or left at default?

4. ERROR HANDLING
   - What HTTP status codes are returned for: missing resource, DB error, auth failure?
   - Are errors from SQLAlchemy propagated correctly or swallowed?
   - Is there structured logging for failed requests?

5. CODE STYLE AND CONSISTENCY
   - Does the pattern (routes → controllers → adapters → sql.py) match across all endpoints?
   - Are all DTOs frozen dataclasses?
   - Are all response schemas using from_dto() pattern?
   - Any deviation from the conventions described in CLAUDE.md?

SPECIFIC CONCERNS:
- The CLAUDE.md says "never create migrations here" — verify there is no alembic/ directory.
- Check if db/models.py ORM models are consistent with event-saver's actual schema.
- Does the auth endpoint (POST /auth/login) exist in event-admin (the frontend uses it)?

OUTPUT FORMAT for each finding:
```
[SEVERITY] Short title

Services affected: list
Location: file/path:line-range
Description: what the problem is and why it matters
Recommendation: specific, actionable fix
```

Severity: CRITICAL | HIGH | MEDIUM | LOW

Write all findings to: docs/audit/raw/event-admin_audit.md
Start with: `# event-admin Audit Findings\n\nAudited: 2026-04-19\n`
Group by: CRITICAL, HIGH, MEDIUM, LOW
End with `## Summary`.
```

---

**S4 — event-admin-frontend audit prompt:**

```
You are auditing the `event-admin-frontend` service in a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend/.

CONTEXT:
- Pre-production system — nothing is live yet. Code can be modified.
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Do NOT modify any code. Audit only. Write findings to docs/audit/raw/event-admin-frontend_audit.md.
- No test runner is configured for this service.

WHAT TO ANALYZE:

1. ARCHITECTURE QUALITY
   - Is the module structure (auth, bookings, settings, participants, shared) clean?
   - Is there any business logic in components that should be in API/utility layers?
   - Is the manual routing (no router library) robust? What happens with unknown paths?
   - Are there any circular imports between modules?

2. API CONTRACT ASSUMPTIONS
   - Enumerate every API call made to event-admin backend: exact endpoint, method, expected response shape
   - Enumerate every API call made to event-users: exact endpoint, method, expected response shape
   - Do these match the actual endpoints defined in event-admin and event-users?
   - What happens in the UI if an API call returns unexpected fields or missing fields?

3. SECURITY
   - JWT is stored in localStorage — is this acceptable? (Flag as known pattern, not a bug)
   - VITE_USERS_API_TOKEN is a static bearer token — where does it come from? Is it a secret that could be exposed in the bundle?
   - Is the dev bypass login (VITE_ENABLE_DEV_BYPASS_LOGIN) gated so it can't accidentally be enabled in production?

4. ERROR HANDLING
   - Are API errors surfaced to the user or silently dropped?
   - What happens when the JWT expires mid-session?
   - Is the ApiError class used consistently across all API calls?

5. CODE STYLE AND CONSISTENCY
   - Is TypeScript used strictly (no `any` types)?
   - Are there any components that have grown too large?
   - Is the formatDateTime locale (ru-RU) intentional and documented?

SPECIFIC CONCERNS:
- The participants module uses VITE_USERS_API_TOKEN (static token) while bookings use the JWT from auth. Is this intentional? Is there a risk of token exposure?
- Is the role-based access control (admin vs user) enforced server-side or only client-side?
- Does the frontend handle the case where event-admin and event-users are on different auth systems?

OUTPUT FORMAT for each finding:
```
[SEVERITY] Short title

Services affected: list
Location: file/path:line-range
Description: what the problem is and why it matters
Recommendation: specific, actionable fix
```

Severity: CRITICAL | HIGH | MEDIUM | LOW

Write all findings to: docs/audit/raw/event-admin-frontend_audit.md
Start with: `# event-admin-frontend Audit Findings\n\nAudited: 2026-04-19\n`
Group by: CRITICAL, HIGH, MEDIUM, LOW
End with `## Summary`.
```

---

**S5 — event-users audit prompt:**

```
You are auditing the `event-users` service in a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/event-users/.

CONTEXT:
- Pre-production system — nothing is live yet. Code can be modified.
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Do NOT modify any code. Audit only. Write findings to docs/audit/raw/event-users_audit.md.

WHAT TO ANALYZE:

1. ARCHITECTURE QUALITY
   - Does this service own a clean domain (users + contacts)?
   - Is the CRM sync logic properly isolated (crm/ subdirectory)?
   - Does it have any dependencies on event-saver's DB or schema?
   - Are users and user_contacts the only tables? Any schema leakage?

2. CRM SYNC RELIABILITY
   - The sync runs every 5 minutes as a background asyncio task. What happens if it crashes? Is it restarted?
   - What happens if the CRM API is unreachable?
   - Is the AES-256-CBC decryption correct? What happens if the encryption key is wrong?
   - What happens if the decrypted payload has unexpected fields?
   - Is the upsert on (email, role) idempotent? Can it cause duplicate users?
   - Is there any rate limiting or backoff if the CRM API returns errors repeatedly?

3. API SURFACE
   - List every HTTP endpoint: method, path, auth, query params, response schema
   - The event-notifier calls GET /api/users?email=&role=&limit=1 — does this endpoint exist and match?
   - The event-admin-frontend calls event-users with a static bearer token — is there a real auth check?
   - What user fields are exposed in the API vs stored in DB?

4. SCALABILITY
   - The CRM sync upserts all users every 5 minutes — what happens with 10k users? 100k?
   - Are there indexes on (email, role) for fast lookup?
   - Any missing indexes for the query patterns in users_db.py?

5. ERROR HANDLING
   - What happens if an upsert fails mid-sync?
   - Is the sync transactional (all-or-nothing) or row-by-row?
   - Are CRM sync errors logged with enough context to debug?
   - What happens if POSTGRES_DSN is wrong at startup?

6. CONTACT RESOLUTION
   - event-notifier calls this service to resolve channels (email, telegram, push) per recipient
   - What happens if a user has no contacts registered?
   - Is the user_contacts table populated by the CRM sync, or only via API?

SPECIFIC CONCERNS:
- CRM_ENCRYPTION_KEY handling: is it validated at startup or only at sync time?
- Is the background sync task properly cancelled on shutdown?
- Does the service handle the case where a user appears in CRM with a role it doesn't recognize?

OUTPUT FORMAT for each finding:
```
[SEVERITY] Short title

Services affected: list
Location: file/path:line-range
Description: what the problem is and why it matters
Recommendation: specific, actionable fix
```

Severity: CRITICAL | HIGH | MEDIUM | LOW

Write all findings to: docs/audit/raw/event-users_audit.md
Start with: `# event-users Audit Findings\n\nAudited: 2026-04-19\n`
Group by: CRITICAL, HIGH, MEDIUM, LOW
End with `## Summary`.
```

---

**S6 — event-notifier audit prompt:**

```
You are auditing the `event-notifier` service in a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/event-notifier/.

CONTEXT:
- Pre-production system — nothing is live yet. Code can be modified.
- This is a RELATIVELY NEW service — assess production readiness explicitly.
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Do NOT modify any code. Audit only. Write findings to docs/audit/raw/event-notifier_audit.md.

WHAT TO ANALYZE:

1. PRODUCTION READINESS
   - Is the service complete enough for production use?
   - PushChannel is wired but commented out — is FCM_PROJECT_ID still required as an env var? What happens at startup if it's missing?
   - Are all required env vars validated at startup (fail fast) or only at use time (fail late)?
   - Is there a health check endpoint?

2. MESSAGE CONTRACT CONSISTENCY
   - What CloudEvent type does it consume from RabbitMQ? (notification.send_requested)
   - Exact queue: events.notification.commands — is this consistent with what event-receiver routes to?
   - What is the full expected payload schema for NotificationCommand?
   - What happens if the payload is missing required fields?
   - What happens if `trigger_event` is not in _TEMPLATE_MAP/_MESSAGE_TEMPLATES?
   - Dead letter handling: does FastStream configure a DLQ for this consumer?
   - What CloudEvents does it publish back (notification.*.message_sent)? Full schema?

3. RESILIENCE
   - The publisher (infrastructure/publisher.py) is fire-and-forget — errors are logged only. Is this acceptable? What delivery guarantees exist?
   - What happens if event-users is unreachable during fan-out? (Check the "fallback: email-only contacts" behavior)
   - What happens if UniSender Go API is down?
   - What happens if Telegram Bot API is down?
   - Are per-channel failures isolated (one failing channel doesn't prevent others)?
   - Are HTTP calls to external APIs made with timeouts?

4. SCALABILITY
   - The use case calls event-users once per recipient. For a notification to N recipients, that's N HTTP calls. Is there batching?
   - Are there any shared mutable state concerns (e.g., shared httpx clients)?
   - Is the consumer idempotent? What happens if the same notification.send_requested message is delivered twice?

5. SECURITY
   - JWT used for event-receiver — is it rotated? Is its expiry handled?
   - Bearer token for event-users — same token as used by event-admin-frontend?
   - Are API keys (UNISENDER_API_KEY, TELEGRAM_BOT_TOKEN) loaded from env correctly?

6. CODE STYLE AND CONSISTENCY
   - Does it follow the same patterns as other Python services (Dishka DI, Protocol interfaces, frozen dataclasses)?
   - Are all DI scopes correct (APP vs REQUEST)?
   - Test coverage: what is tested? Are the infrastructure tests comprehensive?

SPECIFIC CONCERNS:
- declare=False on the consumer: queue must pre-exist. Who creates it? Is there a topology manager?
- Is there a consumer for events.notification.delivery (the queue name in event-saver/event-receiver) or events.notification.commands? Are these different queues?
- The ioc.py has all DI at Scope.APP — is this correct for stateful objects like httpx clients?

OUTPUT FORMAT for each finding:
```
[SEVERITY] Short title

Services affected: list
Location: file/path:line-range
Description: what the problem is and why it matters
Recommendation: specific, actionable fix
```

Severity: CRITICAL | HIGH | MEDIUM | LOW

Write all findings to: docs/audit/raw/event-notifier_audit.md
Start with: `# event-notifier Audit Findings\n\nAudited: 2026-04-19\n`
Group by: CRITICAL, HIGH, MEDIUM, LOW
End with `## Summary` and `## Production Readiness Assessment` (go/no-go with blockers).
```

---

**S7 — event-schemas audit prompt:**

```
You are auditing the `event-schemas` shared library in a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/event-schemas/.

CONTEXT:
- Pre-production system — nothing is live yet. Code can be modified.
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Do NOT modify any code. Audit only. Write findings to docs/audit/raw/event-schemas_audit.md.

WHAT TO ANALYZE:

1. SCHEMA COMPLETENESS AND ACCURACY
   - Does every event type in EVENT_SCHEMA_VERSIONS have a corresponding Pydantic model?
   - Does every event type in EVENT_PRIORITIES have a corresponding model?
   - Are there any event types used in event-receiver or event-saver that are NOT defined here?
   - Are there any models defined here that are NOT used anywhere?

2. SCHEMA DRIFT
   - Compare: what event-receiver publishes (check its routing.py and routes.py) vs what models are defined here
   - Compare: what event-saver consumes (check its consumer.py and domain models) vs what models are defined here
   - Are field names consistent between the schemas library and the actual payloads being sent?
   - Are required vs optional fields consistent between producer and consumer expectations?

3. VERSIONING
   - Are EVENT_SCHEMA_VERSIONS actually used anywhere (checked at runtime)?
   - Is there a mechanism to reject messages with an incompatible schema version?
   - Are breaking changes (field renames, type changes) protected by the versioning approach?

4. CORRECTNESS OF VALIDATORS
   - Are EmailStr fields used everywhere email addresses appear?
   - Is the IANA timezone pattern validator correct and complete?
   - Are there any fields that should be validated but aren't?
   - Are Optional fields appropriately marked vs required?

5. CONSISTENCY ACROSS MODELS
   - Do all booking lifecycle events share a consistent base structure?
   - Are UserInfo and ClientInfo used consistently, or are some events duplicating their fields?
   - Is extra="allow" on external models actually needed and documented?

6. PUBLIC API
   - Does __init__.py re-export everything consumers need?
   - Are there any internal implementation details inadvertently exported?

SPECIFIC CONCERNS:
- event-notifier uses `trigger_event` string matching — is this string defined in event-schemas or hardcoded in notifier?
- Are the NormalizedPayload TypedDicts in normalized.py consistent with the Pydantic models?
- Is there a way for event-receiver and event-saver to get out of sync on schema versions?

OUTPUT FORMAT for each finding:
```
[SEVERITY] Short title

Services affected: list
Location: file/path:line-range
Description: what the problem is and why it matters
Recommendation: specific, actionable fix
```

Severity: CRITICAL | HIGH | MEDIUM | LOW

Write all findings to: docs/audit/raw/event-schemas_audit.md
Start with: `# event-schemas Audit Findings\n\nAudited: 2026-04-19\n`
Group by: CRITICAL, HIGH, MEDIUM, LOW
End with `## Summary` and `## Drift Matrix` (table: event type | schema defined? | used in receiver? | used in saver?).
```

---

- [ ] **Step 2: Wait for all 7 agents to complete, verify all files exist**

```bash
ls docs/audit/raw/
```

Expected: 7 files (`event-receiver_audit.md`, `event-saver_audit.md`, `event-admin_audit.md`, `event-admin-frontend_audit.md`, `event-users_audit.md`, `event-notifier_audit.md`, `event-schemas_audit.md`)

---

### Task 3: Phase 1 — Dispatch cross-cutting audit subagents X1–X3 in parallel

Use `superpowers:dispatching-parallel-agents` to run all 3 cross-cutting audit subagents simultaneously.

**Files:**
- Create: `docs/audit/raw/x1_message_topology.md`
- Create: `docs/audit/raw/x2_data_ownership.md`
- Create: `docs/audit/raw/x3_dependency_graph.md`

- [ ] **Step 1: Invoke dispatching-parallel-agents with the 3 prompts below**

---

**X1 — Message topology audit prompt:**

```
You are performing a complete message topology audit of a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/.

CONTEXT:
- 7 services: event-receiver, event-saver, event-admin, event-admin-frontend, event-users, event-notifier, event-schemas
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Do NOT modify any code. Write findings to docs/audit/raw/x1_message_topology.md.

YOUR TASK: Produce a complete map of every RabbitMQ message in the system.

1. EXCHANGE INVENTORY
   For each exchange: name, type (topic/direct/fanout), durable, who declares it, which services use it

2. QUEUE INVENTORY
   For each queue: name, durable, exclusive, arguments (x-max-priority? x-dead-letter-exchange?), who declares it, which service consumes it, routing key binding

3. PRODUCER INVENTORY
   For each message producer: service name, exchange, routing key pattern, payload schema (reference the model), when it publishes, CloudEvent type field value

4. CONSUMER INVENTORY
   For each message consumer: service name, queue subscribed to, expected payload schema, processing guarantee (ack on receipt vs ack after processing), what happens on exception, retry behavior

5. ORPHAN DETECTION
   - Any queue that has a producer but no consumer?
   - Any queue that has a consumer but no known producer?
   - Any routing key that matches no queue binding?

6. COMPETING CONSUMER DETECTION
   - Any queue consumed by multiple services? Is this intentional?

7. END-TO-END FLOW TRACES
   For each major business operation, trace the complete message chain:
   a. Booking created: from external webhook → event-receiver → RabbitMQ → event-saver → projections → notification triggered → event-notifier → delivery result → event-receiver
   b. Notification send: from event-saver projection → what queue? → event-notifier → delivery
   c. Any other distinct flows

8. KNOWN CONTRACT INCONSISTENCIES
   - Any place where the routing key a producer uses doesn't match the binding a consumer expects
   - Any field present in a published payload that the consumer doesn't expect/handle
   - Any field the consumer requires that the producer may not include

Write to: docs/audit/raw/x1_message_topology.md
Start with: `# Message Topology Audit\n\nAudited: 2026-04-19\n`
Use tables for exchange/queue/producer/consumer inventories.
Use Mermaid sequence diagrams for end-to-end flow traces.
End with `## Contract Inconsistencies` and `## Orphaned Queues/Producers`.
```

---

**X2 — Data ownership audit prompt:**

```
You are performing a data ownership audit of a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/.

CONTEXT:
- 7 services. Two databases: event-saver's DB (events, bookings, participants, projections) and event-users' DB (users, user_contacts).
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Do NOT modify any code. Write findings to docs/audit/raw/x2_data_ownership.md.

YOUR TASK:

1. ENTITY OWNERSHIP MAP
   For every data entity in the system, identify:
   - Which service owns it (can write to it)
   - Which services read it
   - The table name and service/DB it lives in
   - How other services access it (HTTP API, direct DB connection, RabbitMQ message)

2. CROSS-SERVICE DATA REFERENCES
   - participants.user_id references event-users UUID PK — is this enforced? How is it populated?
   - Does event-admin ever join across the participants.user_id reference to call event-users?
   - Is there any service that directly connects to another service's database?

3. DATA DUPLICATION
   - Is any entity stored in more than one place?
   - If so: is there a sync strategy? Which copy is authoritative?
   - Are participant emails stored in both event-saver (participants table) and event-users (users table)?

4. SHARED SCHEMA RISK
   - Do event-saver and event-admin connect to the same PostgreSQL database? Same schema/user?
   - What happens to event-admin if event-saver runs a migration?
   - Is there any shared database schema between event-users and event-saver?

5. MIGRATION OWNERSHIP
   - event-saver owns alembic/. Does event-admin have its own alembic/?
   - Does event-users have its own alembic?
   - Are there any migration conflicts possible?

Write to: docs/audit/raw/x2_data_ownership.md
Start with: `# Data Ownership Audit\n\nAudited: 2026-04-19\n`
Use a table for the entity ownership map.
Flag any CRITICAL (data loss/corruption risk), HIGH (tight coupling), MEDIUM (documentation gap) issues in the standard finding format.
```

---

**X3 — Dependency graph audit prompt:**

```
You are building a complete service dependency graph for a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/.

CONTEXT:
- 7 services: event-receiver, event-saver, event-admin, event-admin-frontend, event-users, event-notifier, event-schemas
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Do NOT modify any code. Write findings to docs/audit/raw/x3_dependency_graph.md.

YOUR TASK:

1. SYNCHRONOUS DEPENDENCIES (HTTP calls)
   For each HTTP call between services: caller → callee, endpoint called, when it happens

2. ASYNCHRONOUS DEPENDENCIES (RabbitMQ)
   For each message dependency: producer → [queue] → consumer, event type

3. SHARED LIBRARY DEPENDENCIES
   event-schemas is imported by which services? For what?

4. INFRASTRUCTURE DEPENDENCIES
   For each service: PostgreSQL (yes/no, which DB), RabbitMQ (yes/no, producer/consumer/both), any other external

5. EXTERNAL DEPENDENCIES
   For each external API call: service → external API, what it's used for, what happens if it's down

6. COMPLETE DEPENDENCY GRAPH
   Produce a Mermaid diagram showing all service-to-service dependencies (both sync and async), annotated with dependency type.

7. ANALYSIS
   - Circular dependencies: any service A → B → A?
   - Single points of failure: if service X goes down, what else fails?
   - Critical path: which services must be up for a booking creation to complete end-to-end?
   - Which services can fail independently without affecting core booking flow?
   - What is the minimum viable subset of services to run for basic booking functionality?

Write to: docs/audit/raw/x3_dependency_graph.md
Start with: `# Service Dependency Graph\n\nAudited: 2026-04-19\n`
Include the Mermaid diagram early in the document.
Tables for sync/async dependencies.
End with `## Failure Impact Analysis` table: service | fails → what breaks?.
```

---

- [ ] **Step 2: Wait for all 3 agents to complete, verify files exist**

```bash
ls docs/audit/raw/
```

Expected: 10 files total (7 from Task 2 + 3 new cross-cutting files).

---

### Task 4: Consolidate Phase 1 findings into 4 audit documents

Read all 10 raw audit files and produce the 4 consolidated outputs.

**Files:**
- Create: `docs/audit/AUDIT_REPORT.md`
- Create: `docs/audit/CONTRACT_MAP.md`
- Create: `docs/audit/SCALABILITY_GAPS.md`
- Create: `docs/audit/DEPENDENCY_GRAPH.md`

- [ ] **Step 1: Read all 10 raw audit files**

```bash
cat docs/audit/raw/*.md | wc -l  # sanity check — should be several hundred lines
```

- [ ] **Step 2: Write AUDIT_REPORT.md**

Consolidate all findings from the 10 raw files. Structure:

```markdown
# Architecture Audit Report

Generated: 2026-04-19

## Executive Summary
[Total findings by severity. Top 5 most critical concerns. Recommended fix order.]

## CRITICAL Findings
[All CRITICAL findings from all services, deduplicated. Each in standard format.]

## HIGH Findings
[All HIGH findings.]

## MEDIUM Findings
[All MEDIUM findings.]

## LOW Findings
[All LOW findings.]

## Per-Service Summary
| Service | CRITICAL | HIGH | MEDIUM | LOW | Top Concern |
|---|---|---|---|---|---|

## Cross-Cutting Findings
[Findings from X1, X2, X3 that span multiple services.]
```

- [ ] **Step 3: Write CONTRACT_MAP.md**

Extract from x1_message_topology.md. Structure:

```markdown
# Message Contract Map

Generated: 2026-04-19

## Exchange Registry
[Table: exchange name | type | durable | declared by]

## Queue Registry
[Table: queue name | bindings | consumer service | DLQ configured?]

## Message Type Registry
[For each CloudEvent type: producer | consumer | exchange | routing key | payload schema ref | schema version]

## End-to-End Flow: Booking Created
[Mermaid sequence diagram]

## End-to-End Flow: Notification Send
[Mermaid sequence diagram]

## Known Contract Inconsistencies
[Findings where producer/consumer don't match]

## Orphaned Queues and Producers
[Queues with no consumer, producers with no matching queue]
```

- [ ] **Step 4: Write SCALABILITY_GAPS.md**

Extract all scalability-related findings from all service audits. Structure:

```markdown
# Scalability Gaps

Generated: 2026-04-19

## Idempotency Issues
[Per service: is the consumer idempotent? What breaks on duplicate delivery?]

## Database Bottlenecks
[Per service: slow queries, missing indexes, N+1 patterns]

## Shared Mutable State
[Any in-memory state that breaks horizontal scaling]

## Batch/Throughput Concerns
[event-notifier N HTTP calls per notification, CRM sync bulk upsert, etc.]

## Missing Infrastructure Patterns
[Missing DLQs, missing retries, missing circuit breakers]

## Recommended Fixes (Priority Order)
[Numbered list, highest impact first]
```

- [ ] **Step 5: Write DEPENDENCY_GRAPH.md**

Extract from x3_dependency_graph.md. Structure:

```markdown
# Service Dependency Graph

Generated: 2026-04-19

## System Topology

[Mermaid diagram — all services, all dependencies, annotated]

## Synchronous Dependencies
[Table: caller | callee | endpoint | purpose]

## Asynchronous Dependencies
[Table: producer | queue | consumer | event type]

## External Dependencies
[Table: service | external system | purpose | failure impact]

## Single Points of Failure
[List services whose failure cascades]

## Critical Path: Booking Creation
[Ordered list of services that must be up]

## Failure Impact Analysis
[Table: service | direct failures | cascading failures]
```

- [ ] **Step 6: Commit all 4 audit files**

```bash
git add docs/audit/
git commit -m "docs: add Phase 1 audit report, contract map, scalability gaps, dependency graph"
```

---

### Task 5: Apply CRITICAL fixes

Read AUDIT_REPORT.md, identify all CRITICAL findings, fix them. The known CRITICAL candidates based on context are listed below; there may be additional ones discovered by the audit agents.

**Known anticipated CRITICAL fixes:**

**5a — Remove event-saver legacy code**

- [ ] **Step 1: Verify which files main.py imports**

```bash
head -30 event-saver/event_saver/main.py
```

Confirm it imports from `ioc_new` not `ioc`.

- [ ] **Step 2: Check for any remaining imports of old ioc.py**

```bash
grep -r "from event_saver.ioc import\|import event_saver.ioc" event-saver/ --include="*.py"
```

Expected: no output. If there are imports, update them to use `ioc_new` equivalents before deleting.

- [ ] **Step 3: Check for any remaining imports of old event_store.py**

```bash
grep -r "from event_saver.adapters.event_store\|import event_saver.adapters.event_store" event-saver/ --include="*.py"
```

Expected: no output. If there are imports, update them before deleting.

- [ ] **Step 4: Delete legacy files**

```bash
rm event-saver/event_saver/ioc.py
rm event-saver/event_saver/adapters/event_store.py
```

- [ ] **Step 5: Verify service still starts cleanly (import check)**

```bash
cd event-saver && uv run python -c "from event_saver.main import app; print('OK')"
```

Expected: `OK`

- [ ] **Step 6: Commit**

```bash
git add event-saver/event_saver/ioc.py event-saver/event_saver/adapters/event_store.py
git commit -m "chore(event-saver): remove legacy ioc.py and event_store.py adapter"
```

**5b — Apply any additional CRITICAL findings from audit**

For each CRITICAL finding in `docs/audit/AUDIT_REPORT.md`:
- Read the finding, its location, and recommendation
- Apply the minimal fix described in the recommendation
- Verify the fix (import check, lint, or test run as appropriate)
- Commit with message: `fix(<service>): <short title from finding>`

---

### Task 6: Apply HIGH fixes

For each HIGH finding in `docs/audit/AUDIT_REPORT.md`, apply the fix in the following priority order:

1. Missing DLQ configurations (message loss risk)
2. Non-idempotent consumers
3. Silent failure paths (fire-and-forget with no observability)
4. Missing startup validation (fail-late env var issues)
5. Security issues (token exposure, missing auth checks)

For each fix:
- [ ] Read the finding and recommendation
- [ ] Apply the minimal fix
- [ ] Run lint check: `cd <service> && uv run ruff check --fix . && uv run ruff format .` (or `npm run lint` for frontend)
- [ ] Commit: `fix(<service>): <short title>`

---

### Task 7: Update existing event-saver docs for accuracy

The event-saver service has existing docs that need accuracy review after the legacy removal.

**Files:**
- Modify: `event-saver/REFACTORING_SUMMARY.md` (mark legacy removal as done)
- Modify: `event-saver/docs/architecture/C4_DIAGRAMS.md` (update if stale)
- Modify: `event-saver/docs/architecture/ARCHITECTURE_DECISION_RECORDS.md` (update if stale)

- [ ] **Step 1: Read each existing doc**

```bash
cat event-saver/REFACTORING_SUMMARY.md
cat event-saver/docs/architecture/C4_DIAGRAMS.md
cat event-saver/docs/architecture/ARCHITECTURE_DECISION_RECORDS.md
```

- [ ] **Step 2: Cross-reference against current codebase**

For each claim in the docs, verify it's still true:
- Does `ioc_new.py` still exist at the claimed path?
- Do the C4 diagrams reflect the actual clean architecture layers?
- Do the ADRs reflect decisions that are actually implemented?

- [ ] **Step 3: Update any stale claims**

Edit the files to reflect current state. Add a note at the top: `> Last reviewed: 2026-04-19`

- [ ] **Step 4: Commit**

```bash
git add event-saver/REFACTORING_SUMMARY.md event-saver/docs/architecture/
git commit -m "docs(event-saver): update existing docs to reflect post-remediation state"
```

---

### ══ USER REVIEW GATE ══

**Present Phase 1 summary to user:**

> "Phase 1 complete. Here is what was found and fixed:
>
> - Audit findings: [X CRITICAL, Y HIGH, Z MEDIUM, W LOW]
> - Fixes applied: [list]
> - Key architectural risks identified: [top 3]
>
> Full report: `docs/audit/AUDIT_REPORT.md`
> Message contracts: `docs/audit/CONTRACT_MAP.md`
> Dependency graph: `docs/audit/DEPENDENCY_GRAPH.md`
>
> Ready to proceed to Phase 2 (documentation)? Any corrections or additional fixes before we proceed?"

**Do not begin Phase 2 until user approves.**

---

## ═══════════════════════════════════════
## PHASE 2: LIVING ARCHITECTURE DOCUMENTATION
## ═══════════════════════════════════════

---

### Task 8: Phase 2 — Dispatch service documentation subagents in parallel

Use `superpowers:dispatching-parallel-agents` to run all 7 service documentation subagents simultaneously.

**Core rule for all documentation subagents:** Document what IS (the post-remediation state), not what should be. Every factual claim must reference a specific file and line range. If something is inconsistent, document the inconsistency explicitly.

**Files created per service:**

Each agent creates files in `docs/architecture/services/<service-name>/`:
- `SERVICE_OVERVIEW.md`
- `API_CONTRACTS.md`
- `DATA_MODEL.md` (Python services with DB only)
- `DEPENDENCIES.md`

> **Note on CELERY_TASKS.md:** The sprint spec lists this as an optional output. None of the 7 services in this monorepo use Celery — they use FastStream/RabbitMQ for async processing. This file is intentionally omitted. If Celery is introduced to any service in the future, add `CELERY_TASKS.md` at that time.

- [ ] **Step 1: Invoke dispatching-parallel-agents with the 7 prompts below**

---

**D-S1 — event-receiver documentation prompt:**

```
You are writing living architecture documentation for the `event-receiver` service in a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/event-receiver/.

CONTEXT:
- System has been audited and CRITICAL/HIGH issues fixed. Document current state.
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Every factual claim must reference a specific file:line range.
- Document what IS, not what should be. Note inconsistencies explicitly.
- Use Mermaid for all diagrams.

Also read: docs/audit/raw/event-receiver_audit.md (for known limitations/inconsistencies to call out)

PRODUCE THESE FILES in docs/architecture/services/event-receiver/:

### SERVICE_OVERVIEW.md
- What business domain this service owns (1 paragraph)
- What it IS responsible for (bulleted list)
- What it is explicitly NOT responsible for (bulleted list)
- Runtime dependencies: what must be running for this service to start
- Key configuration: all env vars from config.py Settings, with types and defaults
- Known limitations or technical debt (from audit findings, marked explicitly)

### API_CONTRACTS.md
For each HTTP endpoint (from routes.py):
- Method + path
- Auth mechanism (which of the 5 auth methods)
- Request schema: all fields, types, required/optional
- Response schema: all fields, types
- Error codes and their meanings
- Example request/response

For each RabbitMQ message this service PUBLISHES:
- Exchange name
- Routing key
- CloudEvent type field value
- Full payload schema with field descriptions and types
- Message priority

### DEPENDENCIES.md
- What this service needs from other services (and what breaks without them)
- What this service provides to other services
- External APIs called: UniSender webhook ingest, GetStream webhook ingest, etc.
- What breaks if this service goes down (from docs/audit/raw/x3_dependency_graph.md)
- Infrastructure: RabbitMQ (producer? consumer?), PostgreSQL (yes/no)
```

---

**D-S2 — event-saver documentation prompt:**

```
You are writing living architecture documentation for the `event-saver` service in a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/event-saver/.

CONTEXT:
- Audit complete; legacy ioc.py and adapters/event_store.py have been deleted. Document current clean-arch state only.
- Existing docs in event-saver/docs/architecture/ have been reviewed; integrate accurate content, don't duplicate.
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Every factual claim must reference a specific file:line range.
- Use Mermaid for all diagrams.

Also read: docs/audit/raw/event-saver_audit.md and event-saver/docs/architecture/ (existing docs to integrate)

PRODUCE THESE FILES in docs/architecture/services/event-saver/:

### SERVICE_OVERVIEW.md
- Domain: event ingestion and projection
- Responsibilities (bulleted)
- NOT responsible for (bulleted) — especially: no HTTP ingress, no read API
- Runtime dependencies
- Key env vars from config.py
- Clean architecture layer map: which files implement which layer (Mermaid component diagram)
- Known limitations from audit

### API_CONTRACTS.md
- No HTTP API (state this explicitly)
- For each RabbitMQ queue this service CONSUMES: queue name, binding, routing key pattern, expected CloudEvent schema (full field list), processing guarantee, retry behavior, DLQ behavior
- Event deduplication: how it works (hash formula, what uniqueness means)

### DATA_MODEL.md
- All tables: events, bookings, participants, booking_organizer_history, and all projection tables
- For each table: all columns, types, constraints, indexes
- Business invariants enforced at DB level (unique constraints, not-null, etc.)
- Key relationships and why they exist
- Migration chain: ordered list of migrations with their purpose
- Mermaid ER diagram

### DEPENDENCIES.md
- Depends on: RabbitMQ (consumer), PostgreSQL (owner/writer)
- Provides to: event-admin (read-only DB access), event-notifier (indirectly via projections that trigger notifications)
- What breaks if event-saver goes down
- Shared schemas from event-schemas library
```

---

**D-S3 — event-admin documentation prompt:**

```
You are writing living architecture documentation for the `event-admin` service in a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/event-admin/.

CONTEXT:
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Every factual claim must reference a specific file:line range.
- Use Mermaid for all diagrams.
Also read: docs/audit/raw/event-admin_audit.md

PRODUCE THESE FILES in docs/architecture/services/event-admin/:

### SERVICE_OVERVIEW.md
- Domain: read-only API over event-saver's database
- Responsibilities and explicitly NOT responsible for (no writes, no migrations)
- Runtime dependencies
- Key env vars (POSTGRES_DSN, DEBUG, LOG_LEVEL)
- Layer map: routes → controllers → adapters → sql.py (with file references)
- Known limitations

### API_CONTRACTS.md
- Every HTTP endpoint with: method, path, auth, query params (types + defaults), response schema (all fields + types), error codes
- Include the auth/login endpoint if it exists
- DI scope for each endpoint's dependencies

### DATA_MODEL.md
- Tables read: list them (do not own them — note they're owned by event-saver)
- SQLAlchemy ORM models in db/models.py vs actual table schema
- Key SQL queries in adapters/bookings_db.py: purpose, tables joined, indexes used
- No ER diagram needed (event-saver owns it) — just reference event-saver/DATA_MODEL.md

### DEPENDENCIES.md
- Depends on: PostgreSQL (read-only), event-users (for participant user_id lookups if any)
- Provides to: event-admin-frontend
- What breaks if event-admin goes down
```

---

**D-S4 — event-admin-frontend documentation prompt:**

```
You are writing living architecture documentation for the `event-admin-frontend` service in a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/event-admin-frontend/.

CONTEXT:
- TypeScript/React/Vite, no test runner configured.
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Every factual claim must reference a specific file:line range.
- Use Mermaid for all diagrams.
Also read: docs/audit/raw/event-admin-frontend_audit.md

PRODUCE THESE FILES in docs/architecture/services/event-admin-frontend/:

### SERVICE_OVERVIEW.md
- Domain: admin UI for bookings and participants
- Module structure: auth, bookings, settings, participants, shared — what each owns
- Routing: how manual routing works (routing.ts, AppRoute discriminated union)
- Auth flow: login → JWT → localStorage → Bearer header (with note: dev bypass mechanism)
- Role-based access: admin vs user, what each can see
- Known limitations from audit (static token for event-users, no test runner, etc.)
- Env vars: all VITE_* variables with types and defaults

### API_CONTRACTS.md
- Every API call to event-admin: endpoint, method, request/response shape as TypeScript types, auth mechanism
- Every API call to event-users: endpoint, method, request/response shape, auth mechanism (note the static token)
- Mermaid sequence diagram: login flow
- Mermaid sequence diagram: booking detail load flow (how many API calls, in what order)

### DEPENDENCIES.md
- Depends on: event-admin (primary backend), event-users (participants API)
- Provides to: end users (admin UI)
- What breaks if event-admin goes down vs if event-users goes down
```

---

**D-S5 — event-users documentation prompt:**

```
You are writing living architecture documentation for the `event-users` service in a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/event-users/.

CONTEXT:
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Every factual claim must reference a specific file:line range.
- Use Mermaid for all diagrams.
Also read: docs/audit/raw/event-users_audit.md

PRODUCE THESE FILES in docs/architecture/services/event-users/:

### SERVICE_OVERVIEW.md
- Domain: user and contact management + CRM sync
- CRM sync: how it works, frequency, encryption (AES-256-CBC), upsert strategy
- What user_contacts contains and how it's populated
- Runtime dependencies
- Key env vars: all from config + CRM_ENCRYPTION_KEY, CRM_API_URL, etc.
- Known limitations (sync reliability, contact population, etc.)

### API_CONTRACTS.md
- Every HTTP endpoint: method, path, auth, query params, response schema
- Include: GET /api/users?email=&role=&limit=1 (called by event-notifier)
- Auth: what token is expected and how it's validated

### DATA_MODEL.md
- Tables: users (email, role, time_zone, unique on email+role), user_contacts (user_id FK, channel, contact_id)
- Constraints and indexes
- CRM sync upsert logic (what happens on conflict)
- Migration chain
- Mermaid ER diagram

### DEPENDENCIES.md
- Depends on: PostgreSQL (owner), external CRM API
- Provides to: event-notifier (contact resolution), event-admin-frontend (participants list)
- What breaks if event-users goes down (effect on notification delivery)
```

---

**D-S6 — event-notifier documentation prompt:**

```
You are writing living architecture documentation for the `event-notifier` service in a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/event-notifier/.

CONTEXT:
- This is a RELATIVELY NEW service — explicitly flag its maturity level in SERVICE_OVERVIEW.
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Every factual claim must reference a specific file:line range.
- Use Mermaid for all diagrams.
Also read: docs/audit/raw/event-notifier_audit.md (especially Production Readiness Assessment section)

PRODUCE THESE FILES in docs/architecture/services/event-notifier/:

### SERVICE_OVERVIEW.md
- Domain: notification fan-out dispatcher
- **Maturity notice at top of document** — note this is a new service; flag any known gaps from audit
- Request flow: RabbitMQ → consumer → use case → contact resolution → channel fan-out → result publish
- Channel support: Email (UniSender Go), Telegram (Bot API), Push (disabled — explain why and what's needed to enable)
- Template mapping: how trigger_event strings map to provider-specific templates
- Adding a new channel: step-by-step (from CLAUDE.md)
- Runtime dependencies
- Key env vars (all of them, with note about FCM vars being required even though Push is disabled)
- Known limitations from audit

### API_CONTRACTS.md
- RabbitMQ message CONSUMED: queue name, CloudEvent type, full NotificationCommand schema (all fields, types, required/optional)
- Processing: what happens per recipient (contact resolution → channel fan-out)
- RabbitMQ messages PUBLISHED: each notification.*.message_sent event type, full DeliveryResult schema
- What happens on: unknown trigger_event, missing recipient contacts, channel failure, use-case exception

### DEPENDENCIES.md
- Depends on: RabbitMQ (consumer + publisher via event-receiver), event-users (contact resolution), UniSender Go API, Telegram Bot API
- Provides to: event-receiver (delivery result events)
- Failure modes: what happens if each dependency is down
- Current gaps: fire-and-forget publisher (no delivery guarantee to event-receiver)
```

---

**D-S7 — event-schemas documentation prompt:**

```
You are writing living architecture documentation for the `event-schemas` shared library in a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/event-schemas/.

CONTEXT:
- This is a shared Python package (not a runtime service).
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Every factual claim must reference a specific file:line range.
- Use Mermaid for all diagrams.
Also read: docs/audit/raw/event-schemas_audit.md (especially Drift Matrix)

PRODUCE THESE FILES in docs/architecture/services/event-schemas/:

### SERVICE_OVERVIEW.md
- Not a runtime service — explain its role as a shared library
- Consumer services: event-receiver, event-saver (list what each imports and why)
- Module layout: types.py, booking.py, chat.py, meeting.py, notification.py, external.py, normalized.py
- Priority system: the 4 levels, which events are at each level
- Versioning: EVENT_SCHEMA_VERSIONS, what semver means here, how breaking changes should be handled
- Known limitations from audit (versioning enforcement, drift detection, etc.)

### API_CONTRACTS.md
- Every exported class: module, fields with types, validators, what it's used for
- EVENT_PRIORITIES map: complete table (EventType → priority)
- EVENT_SCHEMA_VERSIONS map: complete table (EventType → version)
- external.py models: note extra="allow" and why
- normalized.py TypedDicts: note they're not validated at runtime

### DEPENDENCIES.md
- Has no runtime dependencies (library only)
- Required by: event-receiver (which models), event-saver (which models)
- Versioning: how to consume a new version (pip install -e from monorepo root)
- What happens if event-schemas is updated without updating consumers
```

---

- [ ] **Step 2: Wait for all 7 documentation agents to complete, verify files exist**

```bash
find docs/architecture/services -name "*.md" | sort
```

Expected: at minimum 4 files per service × 6 services + 3 files × 1 service (event-admin-frontend has no DATA_MODEL) = ~27 files.

---

### Task 9: Phase 2 — Dispatch cross-cutting documentation subagents in parallel

Use `superpowers:dispatching-parallel-agents` to run 2 cross-cutting documentation subagents simultaneously.

**Files:**
- Create: `docs/architecture/ARCHITECTURE.md`
- Create: `docs/architecture/MESSAGE_CONTRACTS.md`
- Create: `docs/architecture/CODING_STANDARDS.md`
- Create: `docs/architecture/ONBOARDING.md`
- Create: `docs/architecture/INDEX.md`

- [ ] **Step 1: Invoke dispatching-parallel-agents with the 2 prompts below**

---

**D-X1 — ARCHITECTURE + MESSAGE_CONTRACTS + CODING_STANDARDS prompt:**

```
You are writing three cross-cutting architecture documents for a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/.

CONTEXT:
- 7 services: event-receiver, event-saver, event-admin, event-admin-frontend, event-users, event-notifier, event-schemas
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- Per-service documentation already exists in docs/architecture/services/. Read it as a source of truth.
- Also read: docs/audit/ (all 4 audit files) for known issues to acknowledge.
- Every factual claim must reference a specific file:line range.
- Document what IS, not what should be. Note inconsistencies explicitly.
- Use Mermaid for all diagrams.

PRODUCE THESE 3 FILES:

### docs/architecture/ARCHITECTURE.md

Structure:
- **System purpose** (2-3 sentences): what problem this system solves
- **Service inventory**: name | one-line purpose | tech stack | maturity (table)
- **System topology diagram** (Mermaid): all 7 services, all connections (sync + async), infrastructure (RabbitMQ, PostgreSQL ×2)
- **Key architectural decisions with rationale**:
  - Why microservices (not monolith) — be honest if the answer is "unclear" or "not documented"
  - Why RabbitMQ (not direct HTTP or Kafka)
  - Why this particular service split (event-receiver separate from event-saver)
  - Why event-notifier is a separate service (not part of event-saver)
  - Why event-admin is read-only (not part of event-saver)
  - Any decisions that looked wrong during audit — document honestly
- **What is intentionally out of scope** (no billing, no external user auth, etc.)
- **Known architectural concerns** (from docs/audit/AUDIT_REPORT.md — cross-reference)

### docs/architecture/MESSAGE_CONTRACTS.md

Structure:
- **Overview**: summary of messaging approach (CloudEvents binary mode, topic exchange)
- **Exchange and queue registry** (from docs/audit/CONTRACT_MAP.md — do not re-derive, reformat for docs)
- **Complete message type registry**: table with CloudEvent type | producer | consumer | exchange | routing key | schema version | priority
- **Per-message-type documentation**: for each CloudEvent type, full payload schema with field names, types, required/optional, description
- **End-to-end flow: Booking Created** (Mermaid sequence diagram — from contract map)
- **End-to-end flow: Notification Send** (Mermaid sequence diagram)
- **Schema versioning**: how versions work, how to bump, how consumers should handle unknown versions
- **Known contract inconsistencies** (from docs/audit/CONTRACT_MAP.md — document explicitly)
- **How to add a new message type**: step-by-step

### docs/architecture/CODING_STANDARDS.md

Document patterns ACTUALLY USED in this codebase (not aspirational). Derive from reading the code, not from wishful thinking.

Structure:
- **Python service patterns** (applies to event-receiver, event-saver, event-admin, event-users, event-notifier):
  - DI: Dishka AppProvider, Scope.APP vs Scope.REQUEST — what goes where and why
  - Interfaces: Protocol-based, where interfaces live, naming convention
  - DTOs: frozen dataclasses, where they live, naming convention
  - Controllers: what belongs in a controller vs adapter
  - Adapters/SQL: SqlExecutor pattern, raw text() queries, mapping RowMapping to DTOs
  - Error handling: how domain errors are raised and mapped to HTTP codes
  - Config: pydantic-settings, env var naming, .env file
  - Logging: structlog, how context is added
  - Tests: pytest-asyncio, what's mocked (httpx via respx, DB via pytest-postgresql or real?), what's not

- **TypeScript/React patterns** (event-admin-frontend):
  - Module structure: one module per feature area
  - API layer: apiRequest wrapper, error handling pattern
  - Auth: JWT in localStorage, AuthContext
  - Routing: manual routing via routing.ts, AppRoute discriminated union

- **Known divergences from conventions** (places where the codebase contradicts itself — document honestly):
  - event-notifier all at Scope.APP (vs request-scoped in other services)
  - event-admin-frontend: static bearer token for event-users (different from JWT auth)
  - event-saver: has both clean architecture and (now deleted) legacy code — note transition is complete
  - Any others found during audit
```

---

**D-X2 — ONBOARDING + INDEX prompt:**

```
You are writing the onboarding guide and documentation index for a pre-production events monorepo at /Users/alexandrlelikov/PycharmProjects/events/.

CONTEXT:
- 7 services: event-receiver, event-saver, event-admin, event-admin-frontend, event-users, event-notifier, event-schemas
- All per-service docs are in docs/architecture/services/
- Audit findings are in docs/audit/AUDIT_REPORT.md
- Use code-review-graph MCP tools FIRST before Grep/Glob/Read.
- These documents are for a new developer joining the project.
- Use Mermaid for any diagrams.

PRODUCE THESE 2 FILES:

### docs/architecture/ONBOARDING.md

Structure:
- **The 5 most important things to understand before touching this codebase**
  (Derive from architecture + audit. Examples: CloudEvents binary mode, clean architecture in event-saver, event-notifier is new/incomplete, etc.)
- **How to run the full system locally** (exact commands, including Docker Compose if present, env var setup)
- **Minimum viable setup**: which services can you skip to run just one service?
  - Table: service | depends on | can skip X if...
- **How to run tests**:
  - Per service: exact command
  - Note: event-admin-frontend has no test runner; event-schemas has no tests
- **How to inspect RabbitMQ queues locally**
  - Management UI URL, default credentials, what to look for
- **How to run a database migration**
  - Exact alembic commands for event-saver and event-users
- **Common mistakes new developers make**
  (Derive from audit findings. Examples: creating migrations in event-admin, not checking schema versions, assuming fire-and-forget publisher is reliable, etc.)
- **Glossary**: domain terms and internal jargon used in the code
  - CloudEvent, ce-* headers, booking_id, participant, projection, trigger_event, ChannelContact, DLQ, etc.

### docs/architecture/INDEX.md

Structure: FAQ format — map every question a new developer might ask to the document that answers it.

Organize into sections:
- **Getting started** (how to run, where to look first)
- **Understanding the system** (architecture, flows, decisions)
- **Service-specific questions** (one section per service)
- **Making changes** (how to add events, endpoints, services, channels)
- **Debugging** (how to trace a message, inspect queues, check logs)
- **Known issues** (where to find audit findings, what's incomplete)

Examples of Q→A mappings to include:
- "How does a booking event get from the external webhook to the database?" → MESSAGE_CONTRACTS.md + services/event-receiver/API_CONTRACTS.md
- "What does event-saver own?" → services/event-saver/SERVICE_OVERVIEW.md
- "How do I add a new notification channel?" → services/event-notifier/SERVICE_OVERVIEW.md
- "Why is there a separate event-admin service?" → ARCHITECTURE.md
- "How do I add a new message type?" → MESSAGE_CONTRACTS.md + CODING_STANDARDS.md
- "What breaks if I change a field in an event schema?" → services/event-schemas/API_CONTRACTS.md + MESSAGE_CONTRACTS.md
- "What's wrong with the current system?" → docs/audit/AUDIT_REPORT.md
- "How does user contact resolution work?" → services/event-notifier/API_CONTRACTS.md + services/event-users/API_CONTRACTS.md
- "What's the clean architecture in event-saver?" → services/event-saver/SERVICE_OVERVIEW.md
- "How do I run the tests?" → ONBOARDING.md
```

---

- [ ] **Step 2: Verify all 5 cross-cutting files exist**

```bash
ls docs/architecture/*.md
```

Expected: `ARCHITECTURE.md`, `MESSAGE_CONTRACTS.md`, `CODING_STANDARDS.md`, `ONBOARDING.md`, `INDEX.md`

---

### Task 10: Final commit and review

- [ ] **Step 1: Run lint on any modified Python services**

```bash
for dir in event-receiver event-saver event-admin event-users event-notifier; do
  echo "=== $dir ===" && cd /Users/alexandrlelikov/PycharmProjects/events/$dir && uv run ruff check . && uv run ruff format --check . && cd ..
done
```

Expected: no lint errors in any service.

- [ ] **Step 2: Verify complete docs output structure**

```bash
find docs/audit docs/architecture -name "*.md" | sort
```

Expected: 4 audit files + 5 cross-cutting docs + ~27 service docs = ~36 total `.md` files.

- [ ] **Step 3: Commit all documentation**

```bash
git add docs/architecture/
git commit -m "docs: add complete living architecture documentation for all 7 services"
```

- [ ] **Step 4: Present final summary to user**

> "Phase 2 complete. Documentation produced:
>
> - `docs/architecture/ARCHITECTURE.md` — system overview, topology diagram, key decisions
> - `docs/architecture/MESSAGE_CONTRACTS.md` — all message types, flow diagrams
> - `docs/architecture/CODING_STANDARDS.md` — actual patterns used across services
> - `docs/architecture/ONBOARDING.md` — how to run locally, common mistakes, glossary
> - `docs/architecture/INDEX.md` — FAQ index to all documentation
> - Per-service docs in `docs/architecture/services/<service>/` for all 7 services
>
> Sprint complete. All CRITICAL and HIGH audit findings from Phase 1 have been addressed."
