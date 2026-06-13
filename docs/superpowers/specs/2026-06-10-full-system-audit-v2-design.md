# Full System Audit v2 — Design

**Date:** 2026-06-10
**Status:** Approved (interview conducted 2026-06-10)

## Goal

Fresh full audit of the events monorepo (9 services) with immediate remediation of **all**
confirmed findings (CRITICAL through LOW), executed by a multi-agent workflow.

## Interview Outcomes

| Question | Decision |
|---|---|
| Audit mode | Fresh full audit (re-verify April findings + find new) |
| Fix policy | Fix everything (all severities), including frontends |
| Git | `audit-fixes` branch + atomic commits in each nested repo; event-booking commits to root repo branch `feat/event-booking-service` |
| event-notifier | Redesign allowed (internals free; unisender/telegram contracts pinned) |
| Internal contracts | May change freely if all producers/consumers + event-schemas + docs updated |
| Verification | Tests + linters per fix; integration check by replaying real cal.com webhook from `~/PycharmProjects/calendar-bot/requests.jsonl` |
| Priorities | All four equally: delivery reliability, data integrity, security, schema consistency |

## Hard Invariants (do not change)

1. cal.com webhook format into event-receiver (`requests.jsonl` is the source of truth).
2. External contracts: UniSender Go, Telegram Bot API, GetStream, Jitsi JWT, Shortify.
3. cal.com PostgreSQL schema (event-booking reads it as-is).

## Phases

0. **Baseline** — git state of all 9 repos, existing test status, locate docker-compose.
1. **Find** — 13 parallel auditors: 9 per-service (re-verify old AUDIT.md findings + new
   findings, all severities) + 4 cross-cutting (cal.com end-to-end flow vs real payloads,
   RabbitMQ topology, delivery reliability/idempotency, security).
2. **Verify** — per-service adversarial verifier: each finding checked against actual code;
   false positives and duplicates dropped; severity adjusted.
3. **Fix** — ordered waves:
   1. Contracts wave: event-schemas + coordinated producer/consumer changes (dual enums,
      queue names, missing types) — one agent.
   2. Per-service fixers in parallel (separate repos → no conflicts); each fix has a test;
      pytest + ruff before each commit. Code style: no elif/else, early returns,
      Protocol interfaces, frozen DTOs.
   3. event-notifier: redesign mandate.
4. **Integration** — docker-compose RabbitMQ/Postgres, replay real cal.com webhook,
   trace chain receiver → queues → saver/booking → notifier outbox.
5. **Docs + report** — per-service AUDIT.md/SERVICE_OVERVIEW/digests, root docs/audit/*,
   MESSAGE_CONTRACTS.md; final report: found / fixed / remaining (with reasons).

## Out of Scope

- Production deployment concerns (system is pre-prod).
- New features beyond what fixes require.
