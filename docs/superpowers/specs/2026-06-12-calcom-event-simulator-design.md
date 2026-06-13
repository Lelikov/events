# cal.com Event Simulator — Design

**Date:** 2026-06-12
**Status:** Approved

## Goal

A small CLI tool to generate realistic raw cal.com webhooks (as captured in
`event-booking/requests.jsonl`) so the whole chain can be debugged:
cal.com DB rows → signed webhook → `/event/calcom` → RabbitMQ → saver/booking/notifier.

## Decisions (interview 2026-06-12)

| Question | Decision |
|---|---|
| Form | CLI script `scripts/calcom_sim.py` in the ROOT repo, PEP 723 inline deps, run via `uv run` |
| Modes | single `create` / `cancel` / `reschedule` + `lifecycle` scenario + `--starts-in` for reminder debugging |
| Stream/load mode | not needed |

## Behavior

- **Defaults from root `.env`/`.env.example`** (receiver URL `http://localhost:8888`,
  `CALCOM_WEBHOOK_SECRET`, cal.com DSN of the `pg-calcom` compose container); every value
  overridable by CLI flag. Works against the root compose stack with zero configuration.
- **Payloads modeled on real captures** in `event-booking/requests.jsonl` (BOOKING_CREATED /
  BOOKING_CANCELLED / BOOKING_RESCHEDULED), randomized: names, emails, uid, bookingId,
  times, locale (`ru`/`en`), timeZone.
- **Writes the cal.com fixture DB first** (Booking + Attendee rows, matching seeded users/
  EventType), because event-booking enriches directly from that DB — webhook alone is not
  a realistic simulation. `--no-db` skips this; `--dry-run` prints the payload and exits.
- **Reschedule semantics match real cal.com**: new `uid` + `rescheduleUid` pointing at the
  old booking; new Booking row inserted, old row marked cancelled/rescheduled.
- **HMAC**: `X-Cal-Signature-256` = HMAC-SHA256 hexdigest over the raw JSON body.
- `lifecycle [--pause N]`: created → rescheduled → cancelled in one run.
- `create --starts-in 12m`: near-future booking to exercise event-booking's reminder
  scheduler.
- After each send: print HTTP status, uid/bookingId, and a short "what to check" hint
  (saver SQL, notifier outbox, WireMock journal).
- Idempotency note: event-receiver caches identical payloads for 10 min — the generator
  always randomizes ids, so this should not bite; documented anyway.

## Verification

Dry-run output sanity; then against the live compose stack: `create`, `lifecycle`,
`create --starts-in` → booking rows in pg-saver, outbox rows in pg-notifier, WireMock hits.
Stack torn down afterwards (`down -v`).

## Out of scope

- GetStream/Jitsi/UniSender webhook simulation (other ingress paths; `simulate_booking.py`
  already covers canonical events).
- Load testing.
