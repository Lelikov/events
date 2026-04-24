# Scalability Gaps

Generated: 2026-04-20

## Idempotency Issues

- **event-receiver**: Generates an idempotency key (`generate_idempotency_key`) and includes it as a CloudEvent extension attribute, but never stores or checks it. Duplicate webhook deliveries produce duplicate RabbitMQ messages. Deduplication is deferred entirely to downstream consumers.
- **event-saver**: Hash-based dedup via `ON CONFLICT (booking_id, event_type, source, hash)`. The hash is computed with `ujson.dumps` in Python but the DB constraint references `md5(payload::text)` in Postgres -- these serializations are not equivalent (key ordering, float formatting). Legacy events without an idempotency key fall back to this potentially broken hash path.
- **event-notifier**: `processed_events` table provides idempotency via `ON CONFLICT DO NOTHING` on `cloud_event_id`. However, the `FOR UPDATE SKIP LOCKED` in `fetch_pending_outbox` runs outside a transaction (asyncpg autocommit), so the row lock is acquired and immediately released -- defeating SKIP LOCKED entirely. Concurrent instances or rapid poll cycles can pick up the same outbox rows, causing duplicate deliveries.

## Database Bottlenecks

- **event-admin**: `get_booking_details` performs 7 sequential DB round-trips per request (booking row + organizer history + meeting links + email notifications + email status history + telegram notifications + chat/video events). Classic N+1 pattern that holds a connection for the entire duration.
- **event-admin**: `GET /bookings` has no pagination -- `SELECT ... FROM bookings ORDER BY last_seen_at DESC` with no `LIMIT` or `OFFSET`. Unbounded result set consumes unbounded memory, blocks connection pool, risks OOM. Same applies to `GET /bookings/future-email-bounced`.
- **event-users**: `list_users` makes N+1 queries -- one `_fetch_contacts(user_id)` per user in result set. With limit=500, this is 502 sequential DB round-trips per request, causing high latency and connection pool exhaustion.
- **event-users**: CRM sync upserts row-by-row via `upsert_user_from_crm` with per-statement auto-commit. No batching, no bulk insert, no staging table approach. Each user triggers its own `COMMIT`.
- **event-notifier**: `processed_events` table stores every processed `cloud_event_id` permanently with no TTL, expiry, cleanup, partitioning, or scheduled deletion. Architecture doc mentions "TTL 7 days" but no such mechanism exists. Unbounded growth degrades lookups and increases vacuum pressure.
- **event-notifier**: Outbox polling fires every 1 second (`poll_interval=1.0`) regardless of whether the previous poll returned any records. During quiet periods, this generates one DB query per second continuously with no exponential backoff on empty batches.

## Transaction Atomicity Issues

- **event-saver**: `SqlExecutor.execute()` calls `session.commit()` unconditionally after every SQL statement. Each projection commits independently -- if projection 3 of 7 fails, projections 1 and 2 are already permanently committed with no rollback. The final `session.commit()` in `CleanArchitectureEventStore.save_event()` is a no-op on an already-committed session. Advertised transactional behavior does not match reality.
- **event-users**: Same `SqlExecutor.execute()` auto-commit pattern. Multi-step writes (e.g., `create_user` which inserts user then upserts contacts) commit in separate transactions. A contact upsert failure after user insert leaves a stranded user row that `ioc.py`'s session rollback cannot undo.
- **event-admin**: Same `SqlExecutor` with `execute()` and `execute_in_transaction()` methods exposed. Although event-admin is intended as read-only, the write-capable executor exists and the DB connection uses superuser credentials (`postgres`/`postgres`) with no read-only role enforcement.
- **event-notifier**: `FOR UPDATE SKIP LOCKED` in `fetch_pending_outbox` runs without `async with conn.transaction()`. asyncpg operates in autocommit mode by default. The row lock is acquired and immediately released when the statement completes, creating a window for concurrent duplicate processing.

## Shared Mutable State

- **event-notifier**: No in-memory state that blocks horizontal scaling was identified, BUT the `FOR UPDATE SKIP LOCKED` bug means multiple instances will process the same outbox rows concurrently rather than partitioning work correctly. Horizontal scaling is broken until the transaction bug is fixed.
- **event-receiver**: `IngestController` is in `Scope.REQUEST` but has no per-request state -- all injected fields are `Scope.APP` singletons. Creating a new controller instance per request adds unnecessary allocation overhead under high throughput.
- **event-receiver**: `RequestLoggerMiddleware` appends to a single `incoming_requests.jsonl` file via `anyio.open_file` with no concurrent-write protection. Under load, multiple workers race to append to the same file.

## Throughput Concerns

- **event-notifier**: N HTTP calls to event-users per notification event -- one `get_contacts_by_id(user_id)` per recipient. If a booking event has multiple participants, each triggers an independent HTTP round-trip to event-users with no batching or concurrent execution.
- **event-notifier**: When event-users is unreachable, `get_contacts_by_id` returns `[]`, the use case logs a warning and returns normally (no exception raised). FastStream ACKs the message. The notification is silently and permanently lost -- not written to outbox, not retried, not dead-lettered.
- **event-notifier**: No explicit HTTP timeouts configured on any `httpx.AsyncClient`. Defaults to 5s (httpx global), but no `connect_timeout` vs `read_timeout` distinction. A full batch of 10 slow records at 5s each = 50s processing time, blocking the single-threaded asyncio event loop.
- **event-users**: CRM sync fires every 10 seconds by default (`crm_sync_interval_seconds: int = 10`), not 5 minutes as documented. No exponential backoff on errors -- a 1-hour CRM outage generates ~360 failed HTTP requests. Additionally, `CrmClient.fetch_users` creates a new `httpx.AsyncClient` per page (new TCP connection + TLS handshake each time).
- **event-admin-frontend**: `UserInfo` component fires `GET /api/users/id/{userId}` in a `useEffect` on every mount with no deduplication or caching. A booking detail page with 10 notifications triggers 10 separate HTTP requests to event-users. Combined with the broken `getUserById` URL (calls wrong endpoint), all these requests fail silently.
- **event-receiver**: No retry or circuit-breaker on `event-users` HTTP calls inside the publish path. A transient event-users timeout (up to 10s) directly extends webhook response latency. `tenacity` is in dependencies but unused.

## Missing Infrastructure Patterns

- **No DLQ on event-saver consumer queues**: `RabbitEventConsumerRunner.start()` declares subscriptions with no `x-dead-letter-exchange` or `x-dead-letter-routing-key`. Failed messages are either discarded or requeued indefinitely. No retry limit, no dead-letter destination. A single poison-pill message can block queue processing.
- **No DLQ on event-notifier consumer queue**: `declare=False` on consumer queue, no DLQ binding. No explicit `ack_policy` set on the FastStream subscriber. Unparseable messages may requeue infinitely (poison-pill loop) or be silently dropped depending on FastStream version defaults.
- **No circuit breaker/retry on event-receiver to event-users HTTP calls**: `UserResolver.resolve_or_create()` is called for every participant in every event with no retry policy, no exponential backoff, no circuit-breaker. A single slow event-users call extends webhook response latency by up to 10 seconds.
- **No consumer ACK policy explicitly set**: Both event-saver and event-notifier rely on FastStream's default ACK behavior (which varies by version). Neither service explicitly configures `ack_policy`, `prefetch`, or retry semantics on their subscribers.
- **Queue declaration argument mismatch between event-receiver and event-saver**: event-receiver's `RabbitTopologyManager` creates queues with `x-max-priority=10` and `x-dead-letter-exchange=events.dlx`. event-saver's `RabbitTopologyManager` creates plain durable queues without those arguments. If both services declare the same queue, RabbitMQ will reject the second declaration due to argument mismatch, crashing whichever service starts second.
- **No RabbitMQ connection retry at startup**: event-receiver's `broker.connect()` in `lifespan` has no retry, timeout, or error handling. If RabbitMQ is unavailable (container startup race), the app fails immediately with no retry window, causing Kubernetes crash loops.
- **No delivery result events published by event-notifier**: The entire `notification.*.message_sent` pipeline back to event-receiver is unimplemented. `OutboxSender` delivers directly to external APIs but never publishes result events. Routing rules and queue bindings exist on the receiver side for these events, but no producer exists.
- **event-notifier `processed_events` has no cleanup mechanism**: No TTL, no scheduled DELETE, no partition pruning. Table grows without bound.

## Recommended Fixes (Priority Order)

1. **Fix `SqlExecutor.execute()` auto-commit across all services** (event-saver, event-users, event-admin). Remove `session.commit()` from `execute()` and let the session lifecycle owner manage transaction boundaries. This is the single highest-impact change -- it fixes atomicity for projections, CRM sync upserts, and user creation in one pattern fix.

2. **Fix event-notifier `FOR UPDATE SKIP LOCKED` to run inside a transaction**. Wrap `fetch_pending_outbox` in `async with conn.transaction()` or adopt a two-step UPDATE-then-SELECT pattern. Without this fix, horizontal scaling of event-notifier causes duplicate notification deliveries.

3. **Add DLQ configuration to event-saver and event-notifier consumer queues**. Set `x-dead-letter-exchange` and `x-delivery-limit` arguments on queue declarations. Add explicit `ack_policy` to FastStream subscribers. This prevents poison-pill infinite requeue and provides a recovery path for failed messages.

4. **Resolve queue declaration argument mismatch between event-receiver and event-saver**. Align both `RabbitTopologyManager` implementations to declare queues with identical arguments (priority, DLX), or designate a single service as the authoritative queue creator.

5. **Add pagination to `GET /bookings` in event-admin** (and corresponding frontend changes). Apply `LIMIT :limit OFFSET :offset` with a hard server-side maximum (e.g., 500). This prevents unbounded memory consumption as the bookings table grows.

6. **Fix event-users `list_users` N+1 query**. Replace per-user `_fetch_contacts(user_id)` with a single batch query using `WHERE user_id = ANY(:ids)`, then group contacts by `user_id` in Python. Reduces 502 round-trips to 2.

7. **Batch event-admin `get_booking_details` queries**. Run the 7 sub-queries concurrently with `asyncio.gather()` or combine into fewer JOINs. Reduces per-request latency proportionally.

8. **Add retry/circuit-breaker on event-receiver to event-users HTTP calls**. Wrap `_get_user` and `_create_user` with `tenacity.retry` (already in dependencies). Consider making user enrichment optional to decouple RabbitMQ publish availability from event-users availability.

9. **Add exponential backoff to event-notifier outbox polling on empty batches**. Double the sleep interval up to a cap (e.g., 30 seconds), reset to 1 second when records are found. Alternatively use PostgreSQL LISTEN/NOTIFY.

10. **Add TTL/cleanup for event-notifier `processed_events` table**. Implement scheduled `DELETE FROM processed_events WHERE processed_at < NOW() - INTERVAL '7 days'` as a background task, pg_cron job, or Kubernetes CronJob.

11. **Fix CRM sync interval default** from 10 seconds to 300 seconds. Add exponential backoff with jitter on consecutive failures (cap at 30 minutes). Replace per-page `httpx.AsyncClient` instantiation with a shared client.

12. **Add client-side user cache in event-admin-frontend**. Introduce a `useRef` Map or React Query/SWR cache keyed by `userId` in `UserInfo` component to eliminate N+1 HTTP calls per page render.

13. **Raise exception in event-notifier when event-users contact resolution fails due to infrastructure error** (5xx/timeout), so FastStream nacks the message for requeue. Distinguish from 404 (user not found -- acceptable to skip).

14. **Configure explicit HTTP timeouts on all event-notifier `httpx.AsyncClient` instances**. Set `httpx.Timeout(connect=3.0, read=10.0, write=5.0)` for UniSender, Telegram, and event-users clients.

15. **Add RabbitMQ connection retry with exponential backoff at event-receiver startup**. Wrap `broker.connect()` in a `tenacity.AsyncRetrying` loop to survive container startup races.
