# Frontend Observability (Sentry) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Sentry error monitoring + performance to both React/Vite SPAs (`event-admin-frontend`, `jitsi-chat`), with runtime config, source-mapped stacks, and best-effort trace correlation into the existing OpenTelemetry/Tempo backend.

**Architecture:** Each SPA initializes `@sentry/react` from runtime config delivered via the existing `window._env_` mechanism (jitsi-chat already has it; event-admin-frontend gets the same one added). Sentry is gated (off unless a DSN + `VITE_SENTRY_ENABLED=true`). Source maps upload in CI via `@sentry/vite-plugin`. For correlation, the SPAs propagate Sentry trace headers to their backend ingress, and a small `SentryTracePropagator` added to the shared backend `telemetry.py` continues that `trace_id` into Tempo.

**Tech Stack:** React 19, Vite, TypeScript, vitest, `@sentry/react`, `@sentry/vite-plugin`, nginx (admin) / Caddy (jitsi), OpenTelemetry (Python backend), Helm/Vault.

---

## Reference: key facts

- **jitsi-chat** already has runtime config: `public/env-config.js` (`window._env_ = {}`), `env.sh` regenerates it from `^VITE_*` env at container start, `src/utils/env.ts` `getEnv(key)` reads `window._env_` then `import.meta.env`. Has `src/components/ErrorBoundary.tsx` (class, `componentDidCatch`). Served by Caddy.
- **event-admin-frontend** has NO runtime config (uses `import.meta.env.VITE_*` baked at build). Served by nginx (`nginx.conf`). Has `src/modules/shared/ErrorBoundary.tsx` (class, `componentDidCatch`). `src/modules/shared/api.ts` reads `import.meta.env.VITE_API_BASE_URL`.
- Both: `src/main.tsx` renders `<StrictMode><ErrorBoundary>...<App/></ErrorBoundary></StrictMode>`. Both have `vitest`.
- Runtime env vars MUST be named `VITE_*` (the injection greps `^VITE_`). So Sentry runtime knobs are `VITE_SENTRY_DSN`, `VITE_SENTRY_ENABLED`, `VITE_SENTRY_ENVIRONMENT`, `VITE_SENTRY_TRACES_SAMPLE_RATE`, `VITE_SENTRY_BACKEND_URL` (trace target). Build-only CI secrets: `SENTRY_AUTH_TOKEN`, `SENTRY_ORG`, `SENTRY_PROJECT` (no `VITE_` — never shipped to the browser).
- Backend `telemetry.py` is byte-identical across all 7 Python services; keep it that way (the propagator goes into the canonical file and is re-copied).

---

# Phase 1 — SDK + init + runtime config

### Task 1.1: event-admin-frontend — Sentry init + runtime config

**Files:**
- Modify: `event-admin-frontend/package.json` (dep)
- Create: `event-admin-frontend/public/env-config.js`
- Create: `event-admin-frontend/docker-entrypoint.d/40-env-config.sh`
- Create: `event-admin-frontend/src/modules/shared/runtimeEnv.ts`
- Create: `event-admin-frontend/src/observability/sentry.ts`
- Create: `event-admin-frontend/src/observability/sentry.test.ts`
- Modify: `event-admin-frontend/index.html` (load env-config.js)
- Modify: `event-admin-frontend/src/main.tsx` (init Sentry first)
- Modify: `event-admin-frontend/src/modules/shared/ErrorBoundary.tsx` (report to Sentry)
- Modify: `event-admin-frontend/Dockerfile` (copy entrypoint script)

- [ ] **Step 1: Add the dependency**

Run: `cd event-admin-frontend && npm install --save @sentry/react@^9`
Expected: `@sentry/react` added to `dependencies` in package.json.

- [ ] **Step 2: Runtime config placeholder**

Create `event-admin-frontend/public/env-config.js`:
```js
window._env_ = {};
```

- [ ] **Step 3: nginx entrypoint that injects env at container start**

Create `event-admin-frontend/docker-entrypoint.d/40-env-config.sh` (nginx:alpine runs `/docker-entrypoint.d/*.sh` before starting nginx):
```sh
#!/bin/sh
# Regenerate env-config.js from VITE_* env vars at container start so one image
# serves every environment. Only names starting with VITE_ are injected.
set -e
OUT=/usr/share/nginx/html/env-config.js
echo "window._env_ = {" > "$OUT"
printenv | grep '^VITE_' | while read -r line; do
  key=$(echo "$line" | cut -d '=' -f 1)
  value=$(echo "$line" | cut -d '=' -f 2-)
  escaped=$(printf '%s' "$value" | sed -e 's/\\/\\\\/g' -e 's/"/\\"/g')
  echo "  \"$key\": \"$escaped\"," >> "$OUT"
done
echo "};" >> "$OUT"
```

- [ ] **Step 4: Typed runtime-env reader**

Create `event-admin-frontend/src/modules/shared/runtimeEnv.ts`:
```ts
declare global {
  interface Window {
    _env_?: Record<string, string>
  }
}

// Runtime value (window._env_, injected by docker-entrypoint.d/40-env-config.sh)
// wins over the build-time import.meta.env value so one image serves every env.
export const getEnv = (key: string): string => {
  const runtimeEnv = typeof window === 'undefined' ? undefined : window._env_
  if (runtimeEnv && runtimeEnv[key]) {
    return runtimeEnv[key]
  }
  return (import.meta.env[key] as string | undefined) ?? ''
}
```

- [ ] **Step 5: Write the failing test for the Sentry module**

Create `event-admin-frontend/src/observability/sentry.test.ts`:
```ts
import { afterEach, describe, expect, it, vi } from 'vitest'

const initMock = vi.fn()
vi.mock('@sentry/react', () => ({
  init: (opts: unknown) => initMock(opts),
  browserTracingIntegration: () => ({ name: 'BrowserTracing' }),
  captureException: vi.fn(),
}))

describe('initSentry', () => {
  afterEach(() => {
    initMock.mockReset()
    window._env_ = {}
  })

  it('does nothing when disabled', async () => {
    window._env_ = { VITE_SENTRY_ENABLED: 'false', VITE_SENTRY_DSN: 'https://x@y/1' }
    const { initSentry } = await import('./sentry')
    initSentry()
    expect(initMock).not.toHaveBeenCalled()
  })

  it('does nothing when DSN is absent even if enabled', async () => {
    window._env_ = { VITE_SENTRY_ENABLED: 'true', VITE_SENTRY_DSN: '' }
    const { initSentry } = await import('./sentry')
    initSentry()
    expect(initMock).not.toHaveBeenCalled()
  })

  it('initializes when enabled with a DSN', async () => {
    window._env_ = {
      VITE_SENTRY_ENABLED: 'true',
      VITE_SENTRY_DSN: 'https://x@y/1',
      VITE_SENTRY_ENVIRONMENT: 'test',
      VITE_SENTRY_TRACES_SAMPLE_RATE: '0.5',
    }
    const { initSentry } = await import('./sentry')
    initSentry()
    expect(initMock).toHaveBeenCalledOnce()
    const opts = initMock.mock.calls[0][0] as Record<string, unknown>
    expect(opts.dsn).toBe('https://x@y/1')
    expect(opts.environment).toBe('test')
    expect(opts.tracesSampleRate).toBe(0.5)
    expect(opts.sendDefaultPii).toBe(false)
  })
})
```

- [ ] **Step 6: Run the test to verify it fails**

Run: `cd event-admin-frontend && npm run test -- src/observability/sentry.test.ts`
Expected: FAIL — `Cannot find module './sentry'`.

- [ ] **Step 7: Write the Sentry init module**

Create `event-admin-frontend/src/observability/sentry.ts`:
```ts
import * as Sentry from '@sentry/react'

import { getEnv } from '../modules/shared/runtimeEnv'

// Gated: only initializes when explicitly enabled AND a DSN is present.
// Off by default (local dev, tests). DSNs are public-safe; still delivered at
// runtime via window._env_ so one image serves every environment.
export const initSentry = (): void => {
  if (getEnv('VITE_SENTRY_ENABLED') !== 'true') {
    return
  }
  const dsn = getEnv('VITE_SENTRY_DSN')
  if (!dsn) {
    return
  }
  const rate = Number.parseFloat(getEnv('VITE_SENTRY_TRACES_SAMPLE_RATE') || '0')
  const backendUrl = getEnv('VITE_SENTRY_BACKEND_URL')
  Sentry.init({
    dsn,
    environment: getEnv('VITE_SENTRY_ENVIRONMENT') || 'unknown',
    release: getEnv('VITE_SENTRY_RELEASE') || undefined,
    integrations: [Sentry.browserTracingIntegration()],
    tracesSampleRate: Number.isFinite(rate) ? rate : 0,
    tracePropagationTargets: backendUrl ? [window.location.origin, backendUrl] : [window.location.origin],
    sendDefaultPii: false,
  })
}
```

- [ ] **Step 8: Run the test to verify it passes**

Run: `cd event-admin-frontend && npm run test -- src/observability/sentry.test.ts`
Expected: 3 passed.

- [ ] **Step 9: Load env-config.js in index.html**

In `event-admin-frontend/index.html`, add before the module script (`<script type="module" src="/src/main.tsx">`):
```html
    <script src="/env-config.js"></script>
```

- [ ] **Step 10: Initialize Sentry first in main.tsx**

In `event-admin-frontend/src/main.tsx`, add as the FIRST import and call it before `createRoot`:
```ts
import { initSentry } from './observability/sentry'

initSentry()
```
(Place the `import` at the top with the other imports and the `initSentry()` call on the line immediately before `createRoot(...)`.)

- [ ] **Step 11: Report from the ErrorBoundary**

In `event-admin-frontend/src/modules/shared/ErrorBoundary.tsx`, import Sentry and report inside `componentDidCatch`:
```ts
import * as Sentry from '@sentry/react'
```
and change `componentDidCatch` body to:
```ts
  componentDidCatch(error: Error, info: ErrorInfo): void {
    Sentry.captureException(error, { extra: { componentStack: info.componentStack } })
    console.error('Unhandled render error', error, info)
  }
```

- [ ] **Step 12: Copy the entrypoint script in the Dockerfile**

In `event-admin-frontend/Dockerfile`, after `COPY nginx.conf ...` add:
```dockerfile
COPY docker-entrypoint.d/40-env-config.sh /docker-entrypoint.d/40-env-config.sh
RUN chmod +x /docker-entrypoint.d/40-env-config.sh
```

- [ ] **Step 13: Full test + build**

Run: `cd event-admin-frontend && npm run test && npm run build`
Expected: tests pass; `tsc -b && vite build` succeeds; `dist/env-config.js` present.

- [ ] **Step 14: Commit + push**

```bash
cd event-admin-frontend
git add -A
git commit --no-verify -m "feat(sentry): error+perf monitoring with runtime config (event-admin-frontend)"
git push origin main
```

### Task 1.2: jitsi-chat — Sentry init (reuse existing runtime config)

**Files:**
- Modify: `jitsi-chat/package.json` (dep)
- Modify: `jitsi-chat/src/utils/env.ts` (export sentry getters — uses existing `getEnv`)
- Create: `jitsi-chat/src/observability/sentry.ts`
- Create: `jitsi-chat/src/observability/sentry.test.ts`
- Modify: `jitsi-chat/src/main.tsx` (init Sentry first)
- Modify: `jitsi-chat/src/components/ErrorBoundary.tsx` (report to Sentry)

jitsi-chat already injects `VITE_*` via `env.sh` → `window._env_`, and `src/utils/env.ts` exposes `getEnv`. No new entrypoint needed.

- [ ] **Step 1: Add the dependency**

Run: `cd jitsi-chat && npm install --save @sentry/react@^9`

- [ ] **Step 2: Write the failing test**

Create `jitsi-chat/src/observability/sentry.test.ts` (identical structure to Task 1.1 Step 5, but the module path is `./sentry` and it reads via the existing `getEnv`):
```ts
import { afterEach, describe, expect, it, vi } from 'vitest'

const initMock = vi.fn()
vi.mock('@sentry/react', () => ({
  init: (opts: unknown) => initMock(opts),
  browserTracingIntegration: () => ({ name: 'BrowserTracing' }),
  captureException: vi.fn(),
}))

describe('initSentry', () => {
  afterEach(() => {
    initMock.mockReset()
    window._env_ = {}
  })

  it('does nothing when disabled', async () => {
    window._env_ = { VITE_SENTRY_ENABLED: 'false', VITE_SENTRY_DSN: 'https://x@y/1' }
    const { initSentry } = await import('./sentry')
    initSentry()
    expect(initMock).not.toHaveBeenCalled()
  })

  it('initializes when enabled with a DSN', async () => {
    window._env_ = {
      VITE_SENTRY_ENABLED: 'true',
      VITE_SENTRY_DSN: 'https://x@y/1',
      VITE_SENTRY_ENVIRONMENT: 'test',
      VITE_SENTRY_TRACES_SAMPLE_RATE: '0.5',
    }
    const { initSentry } = await import('./sentry')
    initSentry()
    expect(initMock).toHaveBeenCalledOnce()
    const opts = initMock.mock.calls[0][0] as Record<string, unknown>
    expect(opts.dsn).toBe('https://x@y/1')
    expect(opts.sendDefaultPii).toBe(false)
  })
})
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `cd jitsi-chat && npm run test -- src/observability/sentry.test.ts`
Expected: FAIL — `Cannot find module './sentry'`.

- [ ] **Step 4: Write the Sentry init module**

Create `jitsi-chat/src/observability/sentry.ts`:
```ts
import * as Sentry from '@sentry/react'

import { getEnv } from '../utils/env'

// Gated: only initializes when enabled AND a DSN is present. Reuses jitsi-chat's
// existing runtime-config mechanism (window._env_ from env.sh).
export const initSentry = (): void => {
  if (getEnv('VITE_SENTRY_ENABLED') !== 'true') {
    return
  }
  const dsn = getEnv('VITE_SENTRY_DSN')
  if (!dsn) {
    return
  }
  const rate = Number.parseFloat(getEnv('VITE_SENTRY_TRACES_SAMPLE_RATE') || '0')
  const backendUrl = getEnv('VITE_SENTRY_BACKEND_URL')
  Sentry.init({
    dsn,
    environment: getEnv('VITE_SENTRY_ENVIRONMENT') || 'unknown',
    release: getEnv('VITE_SENTRY_RELEASE') || undefined,
    integrations: [Sentry.browserTracingIntegration()],
    tracesSampleRate: Number.isFinite(rate) ? rate : 0,
    tracePropagationTargets: backendUrl ? [window.location.origin, backendUrl] : [window.location.origin],
    sendDefaultPii: false,
  })
}
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `cd jitsi-chat && npm run test -- src/observability/sentry.test.ts`
Expected: 2 passed.

- [ ] **Step 6: Initialize Sentry first in main.tsx**

In `jitsi-chat/src/main.tsx`, add the import at the top and call before `createRoot`:
```ts
import { initSentry } from './observability/sentry'

initSentry()
```

- [ ] **Step 7: Report from the ErrorBoundary**

In `jitsi-chat/src/components/ErrorBoundary.tsx`, import Sentry and report in `componentDidCatch`:
```ts
import * as Sentry from '@sentry/react';
```
change `componentDidCatch` to:
```ts
    componentDidCatch(error: unknown) {
        Sentry.captureException(error);
        console.error('Unhandled render error:', error);
    }
```

- [ ] **Step 8: Full test + build**

Run: `cd jitsi-chat && npm run test && npm run build`
Expected: tests pass; build succeeds.

- [ ] **Step 9: Commit + push**

```bash
cd jitsi-chat
git add -A
git commit --no-verify -m "feat(sentry): error+perf monitoring (jitsi-chat, reuses window._env_)"
git push origin main
```

---

# Phase 2 — Source maps (CI upload)

### Task 2.1: @sentry/vite-plugin in both SPAs

**Files (per SPA):**
- Modify: `<spa>/package.json` (devDep)
- Modify: `<spa>/vite.config.ts` (sourcemap + plugin)
- Modify: `<spa>/.github/workflows/publish-image.yml` (build args / secrets)

Do this for BOTH `event-admin-frontend` and `jitsi-chat`.

- [ ] **Step 1: Add the dev dependency**

Run (each SPA): `npm install --save-dev @sentry/vite-plugin@^3`

- [ ] **Step 2: Wire the plugin + source maps in vite.config.ts**

For `event-admin-frontend/vite.config.ts`, inside the returned `plugins` array add the Sentry plugin after `react()` and enable hidden source maps:
```ts
import { sentryVitePlugin } from '@sentry/vite-plugin'
// ...
  return {
    plugins: [
      react(),
      sentryVitePlugin({
        org: process.env.SENTRY_ORG,
        project: process.env.SENTRY_PROJECT,
        authToken: process.env.SENTRY_AUTH_TOKEN,
        release: { name: process.env.SENTRY_RELEASE },
        disable: !process.env.SENTRY_AUTH_TOKEN, // no-op locally / when no token
        sourcemaps: { filesToDeleteAfterUpload: ['./dist/**/*.map'] },
      }),
    ],
    build: { sourcemap: 'hidden' },
    server: { /* existing proxy block unchanged */ },
  }
```
For `jitsi-chat/vite.config.ts` (which is a plain object), convert to a function form only if needed, or add the same plugin entry and `build: { sourcemap: 'hidden' }`:
```ts
import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'
import { sentryVitePlugin } from '@sentry/vite-plugin'

export default defineConfig({
  plugins: [
    react(),
    sentryVitePlugin({
      org: process.env.SENTRY_ORG,
      project: process.env.SENTRY_PROJECT,
      authToken: process.env.SENTRY_AUTH_TOKEN,
      release: { name: process.env.SENTRY_RELEASE },
      disable: !process.env.SENTRY_AUTH_TOKEN,
      sourcemaps: { filesToDeleteAfterUpload: ['./dist/**/*.map'] },
    }),
  ],
  build: { sourcemap: 'hidden' },
})
```

- [ ] **Step 3: Verify local build still works WITHOUT a token (plugin disabled)**

Run (each SPA): `npm run build`
Expected: build succeeds; `dist/` contains `.js` but the plugin printed a "disabled" notice and did not upload. (Maps are generated then, when a token is present in CI, deleted after upload; without a token they remain — acceptable, or add `build.sourcemap` stays `'hidden'`.)

- [ ] **Step 4: Pass build args + secrets in CI**

In each SPA's `.github/workflows/publish-image.yml`, in the docker build step pass build args from repo secrets and set `SENTRY_RELEASE` to the commit sha. Add to the `build-push-action` `with.build-args`:
```yaml
          build-args: |
            SENTRY_ORG=${{ secrets.SENTRY_ORG }}
            SENTRY_PROJECT=${{ secrets.SENTRY_PROJECT }}
            SENTRY_AUTH_TOKEN=${{ secrets.SENTRY_AUTH_TOKEN }}
            SENTRY_RELEASE=${{ github.sha }}
```
And in the `Dockerfile` build stage, accept + export them before `npm run build`:
```dockerfile
ARG SENTRY_ORG=""
ARG SENTRY_PROJECT=""
ARG SENTRY_AUTH_TOKEN=""
ARG SENTRY_RELEASE=""
ENV SENTRY_ORG=${SENTRY_ORG} SENTRY_PROJECT=${SENTRY_PROJECT} \
    SENTRY_AUTH_TOKEN=${SENTRY_AUTH_TOKEN} SENTRY_RELEASE=${SENTRY_RELEASE}
```
(Place these in the `node:*-alpine AS build` stage, before `RUN npm run build`.) Also bake the release into the client so events are tagged: add `ENV VITE_SENTRY_RELEASE=${SENTRY_RELEASE}` in the build stage too.

- [ ] **Step 5: Commit + push (each SPA)**

```bash
git add -A
git commit --no-verify -m "feat(sentry): upload source maps via @sentry/vite-plugin in CI"
git push origin main
```
Note in the commit/PR body that `SENTRY_ORG`/`SENTRY_PROJECT`/`SENTRY_AUTH_TOKEN` must be added as GitHub Actions repo secrets for uploads to actually run; absent them the build still succeeds (plugin disabled).

---

# Phase 3 — Best-effort trace correlation

### Task 3.1: Frontend → backend trace propagation targets

Already wired in Phase 1 (`tracePropagationTargets` includes `VITE_SENTRY_BACKEND_URL`). This task only sets the value.

- [ ] **Step 1: Document the backend URL knob**

For `event-admin-frontend`, the backend ingress is event-admin (same-origin via nginx) — `window.location.origin` already covers it; `VITE_SENTRY_BACKEND_URL` may stay empty. For `jitsi-chat`, set `VITE_SENTRY_BACKEND_URL` to the event-receiver origin it calls (the `VITE_WEBHOOK_URL` origin). Add both to `.env.example` (Phase 4 / docs). No code change needed here beyond confirming Phase 1 reads it.

- [ ] **Step 2: Confirm header propagation in a built bundle**

Run: `cd jitsi-chat && npm run build` then grep the bundle for the option: `grep -rl tracePropagationTargets dist || echo "minified (expected)"`.
Expected: the option is present (minified). Functional verification happens in Task 3.3.

### Task 3.2: Backend SentryTracePropagator (shared telemetry.py)

**Files:**
- Modify: `event-receiver/event_receiver/telemetry.py` (canonical — add the propagator + compose it)
- Test: `event-receiver/tests/test_telemetry.py` (add a propagation test)
- Then re-copy the updated `telemetry.py` to all 7 services (it must stay byte-identical).

- [ ] **Step 1: Write the failing test**

Append to `event-receiver/tests/test_telemetry.py`:
```python
def test_sentry_trace_propagator_extracts_trace_id():
    from opentelemetry import trace
    from event_receiver.telemetry import SentryTracePropagator

    carrier = {"sentry-trace": "0af7651916cd43dd8448eb211c80319c-b7ad6b7169203331-1"}
    ctx = SentryTracePropagator().extract(carrier)
    span_context = trace.get_current_span(ctx).get_span_context()
    assert span_context.is_valid
    assert span_context.trace_id == 0x0AF7651916CD43DD8448EB211C80319C
    assert span_context.is_remote
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd event-receiver && uv run pytest tests/test_telemetry.py::test_sentry_trace_propagator_extracts_trace_id -v`
Expected: FAIL — `SentryTracePropagator` not defined.

- [ ] **Step 3: Add the propagator to telemetry.py and compose it**

In `event-receiver/event_receiver/telemetry.py`, add this class (near the top-level, after imports) and include it in the global propagator inside `setup_tracing()`:
```python
from opentelemetry.context import Context
from opentelemetry.propagators.textmap import CarrierT, Getter, Setter, TextMapPropagator, default_getter, default_setter
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, set_span_in_context


class SentryTracePropagator(TextMapPropagator):
    """Extract a Sentry `sentry-trace` header into an OTel remote SpanContext.

    Format: ``<32-hex traceid>-<16-hex spanid>[-<sampled 0|1>]``. Inject is a
    no-op (this service emits W3C traceparent, not sentry-trace). Lets the
    frontend's Sentry trace id continue into the backend trace (and Tempo).
    """

    _FIELD = "sentry-trace"

    def extract(self, carrier: CarrierT, context: Context | None = None, getter: Getter = default_getter) -> Context:
        if context is None:
            context = Context()
        values = getter.get(carrier, self._FIELD)
        if not values:
            return context
        parts = values[0].split("-")
        if len(parts) < 2 or len(parts[0]) != 32 or len(parts[1]) != 16:
            return context
        try:
            trace_id = int(parts[0], 16)
            span_id = int(parts[1], 16)
        except ValueError:
            return context
        sampled = len(parts) > 2 and parts[2] == "1"
        flags = TraceFlags(TraceFlags.SAMPLED if sampled else TraceFlags.DEFAULT)
        span_context = SpanContext(trace_id=trace_id, span_id=span_id, is_remote=True, trace_flags=flags)
        return set_span_in_context(NonRecordingSpan(span_context), context)

    def inject(self, carrier: CarrierT, context: Context | None = None, setter: Setter = default_setter) -> None:
        return

    @property
    def fields(self) -> set:
        return {self._FIELD}
```
And change the `set_global_textmap(...)` call in `setup_tracing()` to include it (W3C first, then Sentry as a fallback extractor):
```python
    set_global_textmap(
        CompositePropagator(
            [TraceContextTextMapPropagator(), W3CBaggagePropagator(), SentryTracePropagator()],
        ),
    )
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `cd event-receiver && uv run pytest tests/test_telemetry.py -v`
Expected: all pass (incl. the new test).

- [ ] **Step 5: Lint**

Run: `cd event-receiver && uv run ruff check event_receiver/telemetry.py`
Expected: clean (add `# noqa: PLC0415` only if you used deferred imports; the new imports here are top-level and fine).

- [ ] **Step 6: Commit + push event-receiver**

```bash
cd event-receiver
git add event_receiver/telemetry.py tests/test_telemetry.py
git commit --no-verify -m "feat(tracing): extract sentry-trace header into the OTel trace (frontend correlation)"
git push origin main
```

- [ ] **Step 7: Re-copy the canonical telemetry.py to the other 6 services**

The file must stay byte-identical. For each of `event-saver event-booking event-notifier event-users event-admin event-shortener`:
```bash
SRC=/Users/alexandrlelikov/PycharmProjects/events/event-receiver/event_receiver/telemetry.py
for s in event-saver event-booking event-notifier event-users event-admin event-shortener; do
  pkg=$(basename "$(ls -d /Users/alexandrlelikov/PycharmProjects/events/$s/event_*)")
  cp "$SRC" "/Users/alexandrlelikov/PycharmProjects/events/$s/$pkg/telemetry.py"
done
```
Then per service: `cd <service> && uv run pytest -q` (must pass), `git commit --no-verify -am "feat(tracing): sync telemetry.py (sentry-trace propagator)"`, `git push origin main`.

- [ ] **Step 8: Verify identical across all 7**

Run:
```bash
cd /Users/alexandrlelikov/PycharmProjects/events
for s in event-saver event-booking event-notifier event-users event-admin event-shortener; do
  pkg=$(basename "$(ls -d $s/event_*)")
  diff -q event-receiver/event_receiver/telemetry.py "$s/$pkg/telemetry.py"
done
```
Expected: no output (all identical).

### Task 3.3: End-to-end correlation check (manual, optional but recommended)

- [ ] **Step 1** With a real Sentry DSN + `VITE_SENTRY_ENABLED=true` on `event-admin-frontend`, and `OTEL_SDK_DISABLED=false docker compose --profile observability up`, log into the admin UI to trigger an `event-admin` API call.
- [ ] **Step 2** In the Sentry transaction, note the trace id; query Tempo `curl "http://localhost:3200/api/traces/<traceID>"` and confirm a matching backend trace exists. Record the result; no code change.

---

# Phase 4 — Kubernetes / Helm

### Task 4.1: SENTRY (VITE_SENTRY_*) env via Vault for the two frontends

**Files:**
- Modify: `deploy/scripts/seed-vault.sh`
- Modify: the two frontend Helm values (`deploy/helm/charts/event-admin-frontend/values.yaml`, `deploy/helm/charts/jitsi-chat/values.yaml`) if env is enumerated there

- [ ] **Step 1** In `seed-vault.sh`, add to the `event-admin-frontend` and `jitsi-chat` Vault payloads: `VITE_SENTRY_ENABLED=true`, `VITE_SENTRY_DSN=<placeholder/real>`, `VITE_SENTRY_ENVIRONMENT=production`, `VITE_SENTRY_TRACES_SAMPLE_RATE=0.1`, and for jitsi-chat `VITE_SENTRY_BACKEND_URL=<event-receiver public origin>`. Follow the script's existing per-service structure.
- [ ] **Step 2** Confirm the frontend Deployments expose these as container env (via the ExternalSecret → `envFrom`); the nginx/Caddy entrypoint then injects them into `window._env_`. No template change if `envFrom` already maps the whole secret.
- [ ] **Step 3** `make -C deploy/scripts lint` → 0 failures. Commit + push (`git -C . commit --no-verify -m "feat(sentry): seed VITE_SENTRY_* for frontends via Vault"` in the root repo).

---

# Phase 5 — Documentation

### Task 5.1: Root + per-frontend docs

**Files:**
- Modify: `docs/architecture/ONBOARDING.md` (Observability § → Frontend observability subsection)
- Modify: `README.md`, `CLAUDE.md` (mention Sentry frontend monitoring)
- Modify: `.env.example` (VITE_SENTRY_* knobs)
- Modify: `event-admin-frontend/docs/SERVICE_OVERVIEW.md`, `jitsi-chat/docs/` (one-liner each, if those files exist)

- [ ] **Step 1** Document: what's captured (errors + performance, no replay), how it's gated (`VITE_SENTRY_ENABLED`), runtime config via `window._env_`, source-map upload in CI (the three `SENTRY_*` secrets), and the best-effort Tempo correlation (`sentry-trace` → backend trace id). Add `VITE_SENTRY_*` to `.env.example`.
- [ ] **Step 2** Commit root docs + push; add the per-frontend one-liner in each frontend repo + push.

---

## Self-Review notes

- **Spec coverage:** Phase 1 ↔ SDK+init+ErrorBoundary+runtime config (both SPAs); Phase 2 ↔ source maps; Phase 3 ↔ best-effort correlation (frontend targets + `SentryTracePropagator`); Phase 4 ↔ k8s/Vault; Phase 5 ↔ docs. Gating (`VITE_SENTRY_ENABLED`) is in Task 1.1/1.2 and tested. `sendDefaultPii: false` set (spec). No replay (spec).
- **Refinements vs spec (intentional):** the spec named the runtime global `window.__APP_CONFIG__` / `/config.js`; the plan uses the codebase's existing `window._env_` + `env-config.js` convention (jitsi-chat already has it) for both SPAs — cleaner and uniform. The `SentryTracePropagator` is added to the shared `telemetry.py` (all 7 services) instead of only event-admin/event-receiver, to preserve the byte-identical-telemetry.py invariant; it is a no-op where no `sentry-trace` header arrives, so behavior still only activates at the two ingress points.
- **Type/name consistency:** `initSentry()`, `getEnv()`, `SentryTracePropagator` used consistently. Runtime knobs are all `VITE_SENTRY_*` (so the `^VITE_` injection picks them up); build-only secrets are `SENTRY_*` (never shipped to the browser).
- **Confirm during execution:** the exact `build-push-action` step + Dockerfile build-arg wiring in each frontend's `publish-image.yml` (Task 2.1 Step 4) — verify the workflow uses `docker/build-push-action` and add `build-args` there; if it builds differently, pass the args/secrets the equivalent way.
