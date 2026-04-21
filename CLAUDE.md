# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Monorepo Overview

This is a **multi-service event-driven system** for managing bookings and participants. Seven independent packages share this root directory; each has its own `CLAUDE.md` with service-specific commands and architecture.

| Service | Language/Stack | Role |
|---|---|---|
| `event-receiver/` | Python, FastAPI | Ingress: validates requests, publishes CloudEvents to RabbitMQ |
| `event-saver/` | Python, FastAPI, FastStream | Consumes RabbitMQ, **owns and writes** the PostgreSQL database |
| `event-admin/` | Python, FastAPI | Read-only API over `event-saver`'s DB |
| `event-admin-frontend/` | TypeScript, React, Vite | Admin UI for bookings and participants |
| `event-users/` | Python, FastAPI | Separate user/contact management service with CRM sync |
| `event-schemas/` | Python, Pydantic | Shared schema library; no runtime service |
| `jitsi-chat/` | TypeScript, React, Vite | Participant-facing video meeting + chat SPA |

## System Data Flow

```
External clients / webhooks        jitsi-chat SPA (Jitsi iframe events)
        │                                   │
        ▼                                   ▼
  event-receiver          (validates, normalizes → CloudEvent)
        │ RabbitMQ topic exchange
        ▼
  event-saver             (consumes queues, writes PostgreSQL)
        │
        ├──► event-admin  (read-only API from same DB)
        │         │
        │         ▼
        │   event-admin-frontend  (calls event-admin + event-users)
        │
        └──► [DB tables: events, bookings, participants, projections]

  event-users             (separate DB: users, user_contacts; CRM sync)
```

- **Database ownership**: `event-saver` owns all schema migrations (`alembic/` lives there). `event-admin` is read-only — never create migrations in `event-admin`.
- **Shared schemas**: `event-schemas` is a local pip package imported by `event-receiver` and `event-saver`.
- **participants.user_id** in `event-saver`'s DB references the UUID PK from `event-users`.

## Common Patterns Across Python Services

All Python services share the same conventions:

- **Python 3.14**, `uv` for dependency management
- **FastAPI** for HTTP, **Dishka** for dependency injection
- **Ruff** (line length 120) for linting/formatting; `pre-commit` hooks
- **Protocol-based interfaces** in `interfaces/` — loose coupling between layers
- **Frozen dataclasses** as DTOs for inter-layer communication
- **`adapters/sql.py`** (`SqlExecutor`) — raw `text()` SQL via `AsyncSession`; ORM models exist only for Alembic

### Code Style Rules

- **No `elif`** — use early returns, guard clauses, or mapping dicts instead of `elif` chains
- **Avoid `else`** — prefer early returns. Use `else` only when both branches are truly symmetric and an early return would hurt readability

```bash
# Standard commands (run inside each service directory)
uv sync                      # install deps
ruff check --fix .           # lint
ruff format .                # format
pre-commit run --all-files   # all hooks
```

## Documentation Structure

### Cross-Service Documentation (`docs/`)

The `docs/` directory at the monorepo root contains **cross-service** documentation about the system as a whole:
- `docs/architecture/ARCHITECTURE.md` — system topology, C4 diagrams, architectural decisions
- `docs/architecture/MESSAGE_CONTRACTS.md` — CloudEvent contracts between services
- `docs/architecture/CODING_STANDARDS.md` — shared coding conventions
- `docs/architecture/ONBOARDING.md` — developer onboarding guide
- `docs/architecture/INDEX.md` — FAQ-style documentation index
- `docs/audit/` — system-wide audit report, dependency graph, scalability gaps

### Per-Service Documentation

Each service has its own `CLAUDE.md` (commands, architecture) and `docs/` directory (detailed documentation):

| Service | `CLAUDE.md` | `docs/` contents |
|---|---|---|
| `event-receiver/` | ingress endpoints, auth, RabbitMQ | SERVICE_OVERVIEW, API_CONTRACTS, DEPENDENCIES, AUDIT |
| `event-saver/` | clean architecture, projections, DB schema | SERVICE_OVERVIEW, API_CONTRACTS, DATA_MODEL, DEPENDENCIES, AUDIT |
| `event-admin/` | read-only API, DI scopes, endpoint pattern | SERVICE_OVERVIEW, API_CONTRACTS, DATA_MODEL, DEPENDENCIES, AUDIT |
| `event-admin-frontend/` | Vite/React, routing, auth flow | SERVICE_OVERVIEW, API_CONTRACTS, DEPENDENCIES, AUDIT |
| `event-users/` | user/contact CRUD, CRM sync | SERVICE_OVERVIEW, API_CONTRACTS, DATA_MODEL, DEPENDENCIES, AUDIT |
| `event-notifier/` | notification dispatch, channels | SERVICE_OVERVIEW, API_CONTRACTS, DEPENDENCIES, AUDIT |
| `event-schemas/` | event types, priorities, versioning | SERVICE_OVERVIEW, API_CONTRACTS, DEPENDENCIES, AUDIT |
| `jitsi-chat/` | Jitsi video + Stream Chat SPA | SERVICE_OVERVIEW, API_CONTRACTS, DEPENDENCIES |

## RabbitMQ Queue Routing

Events flow through a **topic exchange** with routing keys matching queue names. Default queues:
- `events.booking.lifecycle` — booking created/cancelled/rescheduled/reassigned
- `events.notification.delivery` — email/Telegram notifications
- `events.chat.activity` — GetStream chat events
- `events.jitsi` — Jitsi meeting events
- `events.unrouted` — fallback

Routing rules use glob patterns on `source` and `type` fields. See `event-receiver/QUEUES_DIGEST.md` and `event-saver/QUEUES_DIGEST.md` for full mappings.

## CloudEvents Format

All inter-service messages use **CloudEvents binary mode**:
- Headers: `ce-type`, `ce-source`, `ce-id`, `ce-time`, `ce-booking_id`, `ce-specversion`
- Body: event payload (JSON)

Event schemas and priorities are defined in `event-schemas/event_schemas/`:
- Priority 10 (CRITICAL): booking lifecycle
- Priority 7 (HIGH): notifications, reminders  
- Priority 5 (NORMAL): chat, meetings, external integrations

## Documentation Requirements

All code changes MUST include corresponding documentation updates:
- Architectural changes → update `docs/architecture/` files
- New event types or queue changes → update per-service `QUEUES_DIGEST.md` and `EVENTS_DIGEST.md`
- Changed interfaces or cross-service contracts → update `docs/architecture/MESSAGE_CONTRACTS.md`
- Bug fixes for audit findings → update per-service `docs/AUDIT.md`
- New services or endpoints → update `docs/architecture/ONBOARDING.md`

<!-- code-review-graph MCP tools -->
## MCP Tools: code-review-graph

**IMPORTANT: This project has a knowledge graph. ALWAYS use the
code-review-graph MCP tools BEFORE using Grep/Glob/Read to explore
the codebase.** The graph is faster, cheaper (fewer tokens), and gives
you structural context (callers, dependents, test coverage) that file
scanning cannot.

### When to use graph tools FIRST

- **Exploring code**: `semantic_search_nodes` or `query_graph` instead of Grep
- **Understanding impact**: `get_impact_radius` instead of manually tracing imports
- **Code review**: `detect_changes` + `get_review_context` instead of reading entire files
- **Finding relationships**: `query_graph` with callers_of/callees_of/imports_of/tests_for
- **Architecture questions**: `get_architecture_overview` + `list_communities`

Fall back to Grep/Glob/Read **only** when the graph doesn't cover what you need.

### Key Tools

| Tool | Use when |
|------|----------|
| `detect_changes` | Reviewing code changes — gives risk-scored analysis |
| `get_review_context` | Need source snippets for review — token-efficient |
| `get_impact_radius` | Understanding blast radius of a change |
| `get_affected_flows` | Finding which execution paths are impacted |
| `query_graph` | Tracing callers, callees, imports, tests, dependencies |
| `semantic_search_nodes` | Finding functions/classes by name or keyword |
| `get_architecture_overview` | Understanding high-level codebase structure |
| `refactor_tool` | Planning renames, finding dead code |

### Workflow

1. The graph auto-updates on file changes (via hooks).
2. Use `detect_changes` for code review.
3. Use `get_affected_flows` to understand impact.
4. Use `query_graph` pattern="tests_for" to check coverage.
