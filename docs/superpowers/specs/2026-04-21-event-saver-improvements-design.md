# Event-Saver Service Improvements

**Date:** 2026-04-21
**Scope:** Full revision of event-saver service, prioritized by severity
**Approach:** Severity-first (critical → low), each phase independently deployable

## Phase 1: CRITICAL — Transactional Atomicity and DLQ

### 1.1 SqlExecutor Auto-Commit (C-5)

**Problem:** `SqlExecutor.execute()` calls `session.commit()` after each SQL statement. If projection 5 of 7 fails, projections 1-4 are already committed — partial state in DB.

**Solution:**
- Remove `commit()` from `SqlExecutor.execute()`
- Single `commit()` remains in `CleanArchitectureEventStore.save_event()` after all projections complete
- One transaction per entire event ingestion pipeline

**Files:** `event_saver/adapters/sql.py`, `event_saver/infrastructure/persistence/event_store_facade.py`

### 1.2 Dead Letter Queue Configuration

**Problem:** Failed messages are silently lost. No retry, no DLQ.

**Solution:**
- Add `x-dead-letter-exchange` and `x-dead-letter-routing-key` arguments to queue declarations in `RabbitTopologyManager`
- Add `nack(requeue=False)` on processing errors in consumer
- Apply same DLQ arguments in `event-receiver` queue declarations to keep topology consistent across services

**Files:**
- `event_saver/adapters/publisher.py` (RabbitTopologyManager)
- `event_saver/adapters/consumer.py` (error handling)
- `event-receiver/` — matching DLQ queue arguments

## Phase 2: HIGH — Architecture Violations and Dead Code

### 2.1 Clean Architecture Import Violations

**Problem:** Application layer imports concrete infrastructure classes instead of protocols.

**Solution:** Replace concrete imports with protocol interfaces from `interfaces/`. Wire through Dishka DI.

**Files:** `event_saver/application/use_cases/ingest_event.py`, `event_saver/application/services/projection_executor.py`, `event_saver/ioc.py`

### 2.2 Dead Code Removal

**Remove:**
- `CloudEventPublisher` and `RabbitTopologyManager` — wired but never called
- `SqlExecutor.execute_in_transaction()` — unused method
- `IEventProjectionStatementFactory` — orphaned interface
- Remove DI registrations from `ioc.py`

**Files:** `event_saver/adapters/publisher.py`, `event_saver/adapters/sql.py`, `event_saver/interfaces/`, `event_saver/ioc.py`

### 2.3 Missing BOOKING_RESCHEDULED in EventType

**Problem:** Rescheduled events are processed but the type is not in the enum.

**Solution:** Add `BOOKING_RESCHEDULED` to EventType enum, update status mappings in `BookingDataExtractor`.

**Files:** `event_saver/domain/models/event.py` or relevant enum location, `event_saver/domain/services/booking_extractor.py`

## Phase 3: MEDIUM — Reliability and Consistency

### 3.1 Silent Projection Failures

**Problem:** Projections catch exceptions and log, but no alerting. Broken projection can silently fail for weeks.

**Solution:** Re-raise after logging so the error propagates to consumer, which nacks the message into DLQ (from Phase 1). DLQ monitoring provides visibility.

**Files:** `event_saver/application/services/projection_executor.py`

### 3.2 Duplicated `_parse_occurred_at`

**Problem:** Same datetime parsing logic in `consumer.py` and `event_parser.py`.

**Solution:** Keep only in `EventParser` (domain layer). Consumer delegates to parser instead of duplicating.

**Files:** `event_saver/adapters/consumer.py`, `event_saver/domain/services/event_parser.py`

### 3.3 Deduplication Hash Mismatch

**Problem:** Python `ujson.dumps()` may produce different hash than PostgreSQL `md5(payload::text)`.

**Solution:** Use `json.dumps(payload, sort_keys=True, ensure_ascii=False)` for deterministic serialization.

**Files:** `event_saver/domain/services/event_parser.py`

### 3.4 TelegramNotificationProjection NULL user_id

**Problem:** SQL insert can write NULL user_id.

**Solution:** Add user_id presence check in `can_handle()` — skip if absent.

**Files:** `event_saver/infrastructure/persistence/projections/notification_projection.py`

## Phase 4: LOW — Cleanup

### 4.1 Documentation Fixes

- Replace all `ioc_new.py` references with `ioc.py` in CLAUDE.md
- Sync QUEUES_DIGEST.md table with actual config in `config.py`

### 4.2 Queue Declaration

**Problem:** `declare=False` on queues causes crash if queues don't pre-exist.

**Solution:** Remove `declare=False` or add explicit declaration in startup.

**Files:** `event_saver/adapters/consumer.py`

## Phase 5: Tests

### 5.1 Unit Tests — Domain Layer

- `EventParser.parse()` — correct parsing, edge cases (missing fields, malformed dates)
- `ParticipantExtractor.extract()` — various payload structures
- `BookingDataExtractor.extract()` — status mapping, missing fields

### 5.2 Unit Tests — Application Layer

- `IngestEventUseCase.execute()` — happy path, duplicate detection, projection error propagation
- `ProjectionExecutor` — can_handle routing, handle results

### 5.3 Integration Tests

- Full pipeline: message in → parse → save → project → commit
- Deduplication: repeated message does not create duplicate
- Atomicity: projection error rolls back entire transaction

## Cross-Cutting: Documentation

Every phase includes:
- Update `docs/AUDIT.md` — close resolved findings
- Update service `CLAUDE.md` if commands/architecture changed
- Add mandatory documentation rule to root and service `CLAUDE.md`
