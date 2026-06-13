# event-shortener Service — Design

**Date:** 2026-06-13
**Status:** Approved

## Goal

A new minimal URL-shortener service built to the event-* conventions, replacing the WireMock
`shortify` stub in the contour. It serves only what the system needs (the contract
event-booking already calls + a public redirect) — none of the standalone Shortify's
users/JWT/admin/pagination/Sentry/rate-limit machinery.

## Decisions (interview 2026-06-13)

| Question | Decision |
|---|---|
| Build vs reuse | Write a NEW service to event-* principles (the existing Shortify has too much we don't need) |
| Storage | PostgreSQL (load is small: redirect = one indexed lookup; internal meeting links only) |
| Transport | All four operations stay **REST** (synchronous; caller needs the result and the short link must exist before the notification fires). No RabbitMQ consumer — service is pure HTTP. **event-booking is unchanged.** |
| Metrics | Included (implementer discretion): `/health`, `/ready`, `/metrics` like every other service |
| Placement | New nested git repo `events/event-shortener`, internal port 8888 |

## API (exact match to event-booking's existing adapter)

Auth on `/api/v1/*`: static `Authorization: Bearer <SHORTENER_API_KEY>`, constant-time compare.
The public redirect is unauthenticated. Because the new service accepts the static Bearer key
the booking adapter sends today, **no change to event-booking is required**.

- `POST /api/v1/urls/shorten` `{long_url, expires_at(float epoch), not_before(float epoch), external_id}`
  → `201 {ident}`. **Idempotent by external_id**: a repeat returns the existing ident (safe on
  redelivery).
- `GET /api/v1/urls/external/{external_id}` → `200 {ident}` / `404`.
- `PATCH /api/v1/urls/external/{old_external_id}` `{long_url, expires_at, not_before, external_id}`
  → `200 {ident}`. Updates data + external_id; the ident is preserved.
- `DELETE /api/v1/urls/external/{external_id}` → `200 {}`.
- `GET /{ident}` — public redirect: `307` to long_url when now ∈ [not_before, expires_at];
  `410` outside the window; `404` if unknown/deleted.

## Data (PostgreSQL, own alembic)

`short_urls`:
`id bigserial PK, ident text UNIQUE NOT NULL, external_id text UNIQUE NOT NULL,
long_url text NOT NULL, not_before timestamptz NULL, expires_at timestamptz NULL,
created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now()`.
`ident`: base62, 7 chars, generated with retry on unique-violation. Redirect = single indexed
lookup on `ident`; no cache needed at this scale. Float epoch in/out, stored as timestamptz.

## Conventions

Python 3.14, FastAPI, Dishka DI, `SqlExecutor` raw `text()` SQL (ORM only for alembic),
Protocol interfaces in `interfaces/`, frozen dataclass DTOs, ruff line 120, no elif / avoid
else. `/metrics` via prometheus-client: HTTP RED + `shortener_urls_created_total`,
`shortener_redirects_total{result=ok|expired|not_found}`. `/health` (liveness), `/ready`
(DB ping). Own CLAUDE.md + docs (SERVICE_OVERVIEW, API_CONTRACTS, DEPENDENCIES, AUDIT).

## Compose / contour integration

- New `event-shortener` + `pg-shortener` (postgres:16) containers in the default stack;
  entrypoint runs `alembic upgrade head`.
- Drop the `shortify` WireMock mapping; point `SHORTENER_URL` → `http://event-shortener:8888`
  (booking still builds `{base_url}/{ident}` — same internal-link behavior as the mock today;
  a public-link split (`SHORTENER_PUBLIC_URL` in booking) is a noted future option, out of scope).
- Keep the other WireMock mocks (unisender/telegram/getstream) untouched.
- Observability profile: `pg-exporter-shortener` + Prometheus scrape job `event-shortener`.

## Verification

- pytest: shorten/get/update/delete/redirect, auth (401 without/with bad key), time-window
  (410 expired / not-yet-active), idempotency by external_id, ident collision retry.
- Live e2e on the stack: `calcom_sim.py create` → booking calls the real shortener → row in
  `pg-shortener`, redirect resolves; teardown.

## Out of scope

- Async (RabbitMQ) operations — all REST per decision.
- Public/internal URL split-horizon in booking (future `SHORTENER_PUBLIC_URL`).
- Migrating the standalone Shortify's history/branding.
