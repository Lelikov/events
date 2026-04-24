# Documentation Index

Find answers to common questions by following the references below.

---

## Getting Started

### How do I set up the project for the first time?
See [ONBOARDING.md](./ONBOARDING.md) -- "How to Run the Full System Locally" section. Start with infrastructure (Docker), install deps, run migrations, start services.

### What is the minimum I need to run for my specific task?
See [ONBOARDING.md](./ONBOARDING.md) -- "Minimum Viable Setup" table. It maps each service to its dependencies and when you can skip it.

### Where should I look first to understand the system?
Start with the root [CLAUDE.md](../../CLAUDE.md) for the system overview and data flow diagram, then read the CLAUDE.md inside the specific service you will be working on.

### What are the most common mistakes I should avoid?
See [ONBOARDING.md](./ONBOARDING.md) -- "Common Mistakes New Developers Make" section. Covers 10 pitfalls derived from audit findings.

---

## Understanding the System

### How does data flow from an external webhook to the database?
Root [CLAUDE.md](../../CLAUDE.md) -- "System Data Flow" diagram. In detail: external client hits event-receiver HTTP endpoint, event-receiver validates auth, normalizes to CloudEvent, publishes to RabbitMQ topic exchange. event-saver consumes from queues, parses, deduplicates, saves raw event, extracts participants/bookings, and runs projections.

### How does RabbitMQ routing work?
See `event-receiver/CLAUDE.md` -- "Event Routing" section and `event-receiver/QUEUES_DIGEST.md` for the full routing table. Rules use glob patterns on `source` and `type` fields; first match wins.

### What queues exist and who consumes them?
See `event-saver/QUEUES_DIGEST.md` for event-saver's subscriptions and `event-notifier/CLAUDE.md` for notification queue consumption. Root [CLAUDE.md](../../CLAUDE.md) lists all default queues.

### What is the CloudEvents format used here?
Root [CLAUDE.md](../../CLAUDE.md) -- "CloudEvents Format" section. Binary mode: headers carry metadata (`ce-type`, `ce-source`, `ce-id`, `ce-time`, `ce-booking_id`), body carries JSON payload.

### What are projections and how do they work?
See `event-saver/CLAUDE.md` -- "Projection System" section. Projections are independent handlers that compute denormalized views (meeting links, notifications, chat events, video events) from raw events as they arrive.

### How does dependency injection work?
All services use Dishka. See any service's `CLAUDE.md` -- "DI scopes" section. `ioc.py` in each service defines APP-scoped singletons and REQUEST-scoped per-request instances. Protocols in `interfaces/` define contracts.

### What are the architectural decisions behind this system?
See `event-saver/docs/architecture/ARCHITECTURE_DECISION_RECORDS.md` for formal ADRs. Also `event-saver/REFACTORING_SUMMARY.md` for the clean-architecture refactoring rationale.

---

## Service-Specific

### event-receiver

**How do I add a new webhook ingestion endpoint?**
See `event-receiver/CLAUDE.md` -- "Adding a New Ingest Endpoint" (6-step guide). Also see [event-receiver/docs/SERVICE_OVERVIEW.md](../../event-receiver/docs/SERVICE_OVERVIEW.md).

**What authentication methods are supported?**
JWT verification, HMAC signature (GetStream), MD5 signature (UniSender), and API key (booking, Jitsi). See `event-receiver/CLAUDE.md` -- "HTTP Endpoints" and `security.py`.

**How do I add a new event type to routing?**
Add a rule to `_default_route_rules()` in `event-receiver/event_receiver/config.py`. Position matters (first match wins). Update `QUEUES_DIGEST.md`.

### event-saver

**How do I add a new projection?**
See `event-saver/CLAUDE.md` -- "When adding new features" section. Create handler in `infrastructure/persistence/projections/`, implement `BaseProjection`, register in `ioc.py`.

**How do I run a migration?**
See [ONBOARDING.md](./ONBOARDING.md) -- "How to Run a Database Migration" section. `cd event-saver && alembic upgrade head`.

**What is the database schema?**
See `event-saver/CLAUDE.md` -- "Database Schema" section and [event-saver/docs/DATA_MODEL.md](../../event-saver/docs/DATA_MODEL.md). Core tables: `events`, `bookings`, `participants`, plus projection tables.

**How does event deduplication work?**
Composite unique constraint on `(booking_id, event_type, source, md5(payload::text))`. Uses `ON CONFLICT DO NOTHING`. See `event-saver/CLAUDE.md` -- "Event Deduplication".

### event-admin

**How do I add a new read endpoint?**
See `event-admin/CLAUDE.md` -- "Adding a new endpoint" pattern: route in `routes.py` -> protocol method -> adapter implementation -> DTO -> response schema with `from_dto()`.

**Why can I not create migrations here?**
event-admin is read-only on event-saver's database. Migrations belong in `event-saver/alembic/`. See [event-admin/docs/SERVICE_OVERVIEW.md](../../event-admin/docs/SERVICE_OVERVIEW.md).

### event-admin-frontend

**How does routing work without a router library?**
Manual implementation in `src/modules/shared/routing.ts`. `parseRoute(pathname)` returns a typed discriminated union. `navigateTo(path)` uses `history.pushState`. See `event-admin-frontend/CLAUDE.md` -- "Routing" section.

**How does auth work?**
JWT stored in localStorage. Login sends email + password + TOTP to `POST /auth/login` on event-admin. Same JWT used for both event-admin and event-users API calls. See `event-admin-frontend/CLAUDE.md` -- "Auth flow".

**What environment variables do I need?**
`VITE_API_BASE_URL` (event-admin) and `VITE_USERS_API_BASE_URL` (event-users). See `event-admin-frontend/CLAUDE.md` -- "Environment Variables" table.

### jitsi-chat

**What does jitsi-chat do?**
Participant-facing video meeting + chat SPA. Embeds Jitsi via @jitsi/react-sdk and Stream Chat. Sends CloudEvents (binary mode) to event-receiver for all meeting events. See [jitsi-chat/docs/SERVICE_OVERVIEW.md](../../jitsi-chat/docs/SERVICE_OVERVIEW.md).

**What events does it send?**
16 Jitsi iframe events mapped to `jitsi.{category}.{action}` CloudEvent types. See [jitsi-chat/docs/API_CONTRACTS.md](../../jitsi-chat/docs/API_CONTRACTS.md).

**What environment variables do I need?**
`VITE_JITSI_DOMAIN`, `VITE_WEBHOOK_URL`, `VITE_STREAM_CHAT_API_KEY`, `VITE_STREAM_CHAT_BASE_URL`. See `jitsi-chat/.env.example`.

### event-users

**How do I add a new user endpoint?**
See `event-users/CLAUDE.md` -- "Adding a new endpoint" pattern. Same Protocol/adapter/DTO/schema structure as event-admin.

**How does CRM sync work?**
Background asyncio task runs every 10 seconds (not 5 minutes as sometimes documented). Fetches encrypted user list from external CRM, decrypts with AES-256-CBC, upserts by `(email, role)`. See `event-users/CLAUDE.md` -- "CRM background sync".

**Where are user contact channels stored?**
`user_contacts` table with `(user_id, channel)` unique constraint. Channels: telegram, push. See [event-users/docs/DATA_MODEL.md](../../event-users/docs/DATA_MODEL.md).

### event-notifier

**How do I add a new notification channel?**
See `event-notifier/CLAUDE.md` -- "Adding a New Channel" (4-step guide). Implement `INotificationChannel`, register in `ioc.py`, add `ChannelType` enum value.

**How does template mapping work?**
`trigger_event` string (e.g., `"BOOKING_CREATED"`) maps to provider-specific templates. Each channel has its own `_TEMPLATE_MAP` dict. Unknown trigger events fail gracefully.

**What queue does it consume from?**
Intended: `events.notification.commands`. Actual default: `events.notifications` (known mismatch -- audit finding C-3).

### event-schemas

**How do I add a new event type?**
Add to `EventType` enum in `types.py`. Add entries in `EVENT_PRIORITIES` and `EVENT_SCHEMA_VERSIONS`. Create a Pydantic model in the appropriate module (booking.py, chat.py, etc.). Re-export from `__init__.py`.

**What priority should my new event type have?**
See `event-schemas/CLAUDE.md` -- "Event priorities" table. 10 = booking lifecycle, 7 = notifications/reminders, 5 = chat/meetings/external.

---

## Making Changes

### How do I add a new event type end-to-end?
1. Define schema model in `event-schemas` (appropriate module)
2. Add to `EventType` enum, `EVENT_PRIORITIES`, `EVENT_SCHEMA_VERSIONS`
3. Add routing rule in `event-receiver/config.py` (watch position!)
4. Add normalizer logic in `event-receiver/normalizers.py` if needed
5. Add projection handler in `event-saver` if the event needs derived views
6. Update `EVENTS_DIGEST.md` and `QUEUES_DIGEST.md` in both services

### How do I add a new API endpoint to event-admin?
Follow the pattern in `event-admin/CLAUDE.md`: route -> protocol -> adapter (SQL query) -> DTO -> response schema. Never add write operations.

### How do I modify the database schema?
Create a migration in `event-saver/alembic/`: `cd event-saver && alembic revision --autogenerate -m "description"`. For event-users tables, use `event-users/alembic/`.

### How do I add a new notification channel?
See `event-notifier/CLAUDE.md` -- "Adding a New Channel". Implement protocol, register in DI, add channel type, map templates.

---

## Debugging

### How do I trace a message through the system?
1. Check event-receiver logs for the incoming HTTP request and published routing key
2. Open RabbitMQ Management UI (http://localhost:15672, guest/guest) to see which queue received the message
3. Check event-saver logs for consumption and projection execution
4. Query `events` table: `SELECT * FROM events WHERE booking_id = '...' ORDER BY occurred_at DESC`

### How do I inspect RabbitMQ queues?
See [ONBOARDING.md](./ONBOARDING.md) -- "How to Inspect RabbitMQ Queues Locally". Management UI at http://localhost:15672 with guest/guest.

### Why are messages not arriving at event-saver?
Most likely cause: routing rule mismatch. Check `event-receiver/config.py` routing rules -- first match wins. Known bug (C-1): booking lifecycle events route to `events.notifications` instead of `events.booking.lifecycle`. Also check that event-saver subscribes to the correct queue names in its config.

### Why are notifications not being sent?
Check three things: (1) event-notifier's `notifications_queue` config -- default is `events.notifications`, intended is `events.notification.commands`; (2) event-users must be reachable for contact lookups; (3) channel credentials (UNISENDER_API_KEY, TELEGRAM_BOT_TOKEN) must be valid.

### How do I check if an event was deduplicated?
Query the events table. If `INSERT` returned no rows (ON CONFLICT DO NOTHING), the event was a duplicate. Check dedup hash: composite of `(booking_id, event_type, source, md5(payload::text))`.

### Why is the frontend showing blank user info?
Known bug (H-15): frontend calls `GET /api/users/${id}` but backend expects `GET /api/users/id/{user_id}`. The UserInfo component silently fails.

---

## Known Issues

### Where is the full audit report?
See [../audit/AUDIT_REPORT.md](../audit/AUDIT_REPORT.md). Contains 14 CRITICAL, 24 HIGH, 30 MEDIUM, and 18 LOW findings.

### What are the most critical bugs?
1. Booking lifecycle events never reach event-saver due to routing rule shadowing (C-1)
2. SqlExecutor auto-commits break transactional atomicity (C-5)
3. Queue name mismatch between event-receiver and event-notifier (C-3)
4. Dual EventType enums with incompatible values (C-9)
5. Hardcoded JWT default secret in production configs (C-6)

See [../audit/AUDIT_REPORT.md](../audit/AUDIT_REPORT.md) -- "Top 5 Most Critical Systemic Concerns".

### What is incomplete or not yet implemented?
- event-notifier delivery result publishing (H-19) -- documented but no code exists
- event-notifier has no migration framework
- Push notifications (FCM) are wired but commented out
- No automated tests in any service except event-notifier
- No DLQ in event-saver or event-notifier consumers
- No pagination on `GET /bookings` (H-14)

### Where is the dependency/scalability analysis?
See [../audit/DEPENDENCY_GRAPH.md](../audit/DEPENDENCY_GRAPH.md) and [../audit/SCALABILITY_GAPS.md](../audit/SCALABILITY_GAPS.md).

### Where are the contract mismatches documented?
See [../audit/CONTRACT_MAP.md](../audit/CONTRACT_MAP.md) for the full mapping of expected vs actual contracts between services.
