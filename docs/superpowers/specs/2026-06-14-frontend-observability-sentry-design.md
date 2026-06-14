# Frontend Observability (Sentry) — Design

**Date:** 2026-06-14
**Status:** Approved

## Goal

Error monitoring + performance for the two React/Vite SPAs (`event-admin-frontend`, `jitsi-chat`):
capture JS exceptions with readable (source-mapped) stack traces, web-vitals/fetch performance,
and best-effort correlation into the existing OpenTelemetry/Tempo backend. Closes the last
observability gap — the backend has metrics, logs, traces; the browser had nothing.

## Decisions (interview 2026-06-14)

| Topic | Decision |
|---|---|
| Backend | **Sentry SaaS** (sentry.io) — zero ops, full features; DSN is public-safe |
| Capture scope | **Errors + performance** (`@sentry/react` + `browserTracingIntegration`). No session replay |
| Trace ↔ Tempo link | **Best-effort correlation** — frontend propagates trace headers to the backend; `event-admin` + `event-receiver` continue the same `trace_id` into Tempo so a Sentry event links to its Tempo trace |
| Scope | Both SPAs (`event-admin-frontend`, `jitsi-chat`). Backend tracing already shipped |
| Config delivery | **Runtime** (`/config.js` injected by nginx/Caddy from env) — one image per env, no rebuild |
| Enablement | Gated: Sentry inits only when a DSN is present / `SENTRY_ENABLED=true`. OFF in local dev |

## Current state

- Both SPAs: React 19 + Vite + TypeScript, `vitest` tests, eslint. Entry `src/main.tsx`, root `src/App.tsx`.
- `event-admin-frontend` is served by **nginx** (`nginx.conf`, same-origin proxy to event-admin);
  `jitsi-chat` by **Caddy** (`Caddyfile`). Each has a `Dockerfile` and `vite.config.ts`.
- No existing error tracking or runtime-config mechanism in either SPA.
- Backend (7 Python services) is OTel-instrumented with the global W3C `tracecontext` propagator;
  `event-admin` and `event-receiver` are the ingress points the two SPAs call.
- Both frontend repos have GitHub remotes (`Lelikov/event-admin-frontend`, `Lelikov/jitsi-chat`)
  with `publish-image.yml` CI.

## Components

### 1. Sentry init module (`src/observability/sentry.ts`, one per SPA, near-identical)
- `import * as Sentry from "@sentry/react"`; a `initSentry()` that reads runtime config (below) and,
  only when `sentryEnabled` and a `sentryDsn` are present, calls:
  ```ts
  Sentry.init({
    dsn, environment, release,
    integrations: [Sentry.browserTracingIntegration()],
    tracesSampleRate,
    tracePropagationTargets,   // backend ingress URLs (Phase 3)
    sendDefaultPii: false,
  })
  ```
- Imported **first** in `main.tsx` (before app render) so early errors are captured.
- `App` wrapped in `Sentry.ErrorBoundary` with a minimal fallback UI.
- No-op (returns early) when disabled → local dev and tests stay clean.

### 2. Runtime config (`/config.js`)
- A small `public/config.js` placeholder sets `window.__APP_CONFIG__ = {}` for local dev.
- In the container, the nginx/Caddy **entrypoint** renders `/config.js` from env vars at start:
  `window.__APP_CONFIG__ = { sentryDsn, sentryEnvironment, sentryTracesSampleRate, sentryEnabled, ... }`.
- `index.html` loads `/config.js` (a plain `<script src="/config.js">`) **before** the bundle.
- A typed `readConfig()` helper reads `window.__APP_CONFIG__` with safe defaults.
- DSNs are not secret; still delivered via env so the same image serves every environment.

### 3. Best-effort trace correlation
- Frontend: `tracePropagationTargets` set to the backend ingress origins each SPA calls
  (`event-admin-frontend` → event-admin; `jitsi-chat` → event-receiver). Sentry's
  `browserTracingIntegration` then attaches `sentry-trace` + `baggage` headers (trace id = 32-hex)
  to those fetch/XHR requests.
- Backend: add a small custom OTel `TextMapPropagator` (`SentryTracePropagator`, ~25 lines) that
  **extracts** an incoming `sentry-trace` header into an OTel `SpanContext` (no-op on inject), and
  compose it into the global propagator of **`event-admin`** and **`event-receiver`** only (extend
  their `telemetry.py` propagator list). Result: the backend span's `trace_id` equals the Sentry
  frontend trace id → in Tempo, search that id to see the backend trace; from Sentry, read the id.
  No Sentry SDK is pulled into Python — only a header parser.

### 4. Sourcemaps
- `vite.config.ts`: `build.sourcemap: "hidden"` (generate but don't reference publicly).
- `@sentry/vite-plugin` in the build uploads sourcemaps to Sentry and deletes them from the output;
  `release` = the git SHA. Requires CI secrets `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT`.
- Without the auth token (e.g. local build) the plugin is a no-op / skipped — the build still succeeds.

### 5. CI / Kubernetes
- Each frontend repo's `publish-image.yml`: the build step receives the Sentry CI secrets and uploads
  sourcemaps with `release=<sha>`; absent secrets → plain build, no upload (must not fail).
- k8s: frontend pods get `SENTRY_*` env via Vault/ESO; the nginx/Caddy entrypoint injects `/config.js`
  from env. Mirrors the backend `OTEL_*`-via-Vault approach. Add `SENTRY_*` to `seed-vault.sh` for the
  two frontends.

### 6. Tests (vitest)
- `sentry.ts`: gating — no DSN / disabled → `Sentry.init` is NOT called (mock `@sentry/react`).
- `ErrorBoundary` renders the fallback when a child throws.
- `readConfig()` returns safe defaults when `window.__APP_CONFIG__` is absent.

## Config / env

Per frontend container: `SENTRY_ENABLED` (default `false`), `SENTRY_DSN`, `SENTRY_ENVIRONMENT`,
`SENTRY_TRACES_SAMPLE_RATE` (e.g. `1.0` dev / `0.1` prod). `.env.example` documents them. Build-time
(CI only): `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT`, `SENTRY_RELEASE` (=sha).

## Phased implementation

1. **SDK + init + runtime config** — `@sentry/react`, `sentry.ts`, `ErrorBoundary`, `public/config.js`
   + nginx/Caddy entrypoint injection, gating, `readConfig()`; vitest. Both SPAs.
2. **Sourcemaps** — `@sentry/vite-plugin`, `build.sourcemap: "hidden"`, CI secrets, `release=sha`.
3. **Trace correlation** — frontend `tracePropagationTargets`; `SentryTracePropagator` composed into
   `event-admin` + `event-receiver` `telemetry.py`.
4. **k8s/Helm** — `SENTRY_*` via Vault for the two frontends; config injection in the pods.
5. **Docs** — ONBOARDING § Frontend observability, README, per-frontend `docs/`.

## Verification

Build each SPA with a real DSN + `SENTRY_ENABLED=true`; a deliberate `throw` is caught by
`ErrorBoundary` and appears in the Sentry project with a **source-mapped** stack and the correct
`release`; a transaction (page load + a backend fetch) appears under Performance. Then confirm an
`event-admin-frontend` → event-admin request produces a Tempo trace whose `trace_id` matches the
Sentry transaction's trace id (best-effort link). Local dev with `SENTRY_ENABLED=false` shows no
Sentry network calls.

## Out of scope

- Session Replay, profiling, user-feedback widget.
- Full unified frontend↔backend trace (single trace tree across Sentry + Tempo) — best-effort id
  correlation only.
- Browser tracing exported to the OTel Collector/Tempo directly (frontend traces live in Sentry).
- The two SPAs' existing UI/UX — untouched beyond the ErrorBoundary wrapper.
