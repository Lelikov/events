# Architecture Audit & Documentation Sprint — Design Spec
Date: 2026-04-19

## Context

7-service event-driven monorepo for managing bookings and participants. Services are **under active development, not yet in production**. All code is fair game for modification — legacy code should be removed, not just flagged.

## Services in scope

| Service | Maturity | Special notes |
|---|---|---|
| `event-receiver` | Stable | HTTP ingress, 5 auth methods, webhook integrations |
| `event-saver` | Stable + legacy debt | Owns DB/migrations; legacy `ioc.py` + `adapters/event_store.py` to remove |
| `event-admin` | Stable | Read-only API over event-saver's DB |
| `event-admin-frontend` | Stable | TypeScript/React/Vite admin UI |
| `event-users` | Stable | User/contact management + CRM sync |
| `event-notifier` | New/immature | Notification dispatcher; PushChannel disabled; fire-and-forget publisher |
| `event-schemas` | Library | Shared Pydantic schemas, no runtime service |

## Known concerns (all confirmed in scope)

- Message contract drift between producers and consumers
- Legacy code in `event-saver` (to be removed, not just documented)
- `event-notifier` production readiness gaps
- Idempotency of all RabbitMQ consumers
- CRM sync opacity in `event-users`
- Existing `event-saver` docs (ADRs, C4, REFACTORING_SUMMARY) — audit for accuracy, update stale, integrate

## Approach: Option B — sequential phases, parallel within each phase

### Phase 1: Audit + Remediation

10 parallel subagents:

**Service subagents (S1–S7):**
- S1 `event-receiver` — auth methods, routing rules, webhook contract integrity
- S2 `event-saver` — legacy code removal, clean arch completeness, idempotency, projections
- S3 `event-admin` — read boundary violations, query performance
- S4 `event-admin-frontend` — API contract assumptions vs actual backend
- S5 `event-users` — CRM sync reliability, contact resolution
- S6 `event-notifier` — production readiness, disabled PushChannel, fire-and-forget risk
- S7 `event-schemas` — schema versioning, drift risk, consistency

**Cross-cutting subagents (X1–X3):**
- X1 Message topology — all exchanges, queues, bindings, routing keys; orphans
- X2 Data ownership — entity ownership, cross-DB queries, duplication
- X3 Dependency graph — full directed graph, SPOFs, critical path

**Findings categorized as:**
- CRITICAL — data loss, security, system-wide outage risk → **fix immediately**
- HIGH — reliability/scalability → **fix in Phase 1**
- MEDIUM — maintainability/consistency → **document + fix if straightforward**
- LOW — style/minor → **document only**

**Phase 1 outputs (docs/audit/):**
- `AUDIT_REPORT.md` — all findings grouped by category, with fix status
- `CONTRACT_MAP.md` — complete message contract map
- `SCALABILITY_GAPS.md` — bottlenecks with specific fixes
- `DEPENDENCY_GRAPH.md` — Mermaid service dependency diagram

**Review gate:** User reviews audit findings before Phase 2 begins.

### Phase 2: Living Architecture Documentation

9 parallel subagents (after audit review):

**Service subagents (one per service):**
Each produces in `docs/architecture/services/<name>/`:
- `SERVICE_OVERVIEW.md`
- `API_CONTRACTS.md`
- `DATA_MODEL.md`
- `CELERY_TASKS.md` (if applicable)
- `DEPENDENCIES.md`

**Cross-cutting subagents:**
- D1 → `ARCHITECTURE.md`, `MESSAGE_CONTRACTS.md`, `CODING_STANDARDS.md`
- D2 → `ONBOARDING.md`, `INDEX.md`

## Documentation rules

- Document what IS (post-remediation), not what should be
- Every factual claim references file + line range
- Inconsistencies documented explicitly as known divergences
- Uncertainties stated explicitly, not papered over
- All diagrams in Mermaid
- `event-notifier` maturity flagged wherever relevant

## Output structure

```
docs/
├── audit/
│   ├── AUDIT_REPORT.md
│   ├── CONTRACT_MAP.md
│   ├── SCALABILITY_GAPS.md
│   └── DEPENDENCY_GRAPH.md
└── architecture/
    ├── INDEX.md
    ├── ARCHITECTURE.md
    ├── MESSAGE_CONTRACTS.md
    ├── CODING_STANDARDS.md
    ├── ONBOARDING.md
    └── services/
        ├── event-receiver/
        ├── event-saver/
        ├── event-admin/
        ├── event-admin-frontend/
        ├── event-users/
        ├── event-notifier/
        └── event-schemas/
```

## Constraints

- No migrations, DB writes, or queue changes
- Legacy code removal allowed and expected
- CRITICAL/HIGH findings fixed in Phase 1
- Existing event-saver docs audited, updated, integrated (not discarded)
