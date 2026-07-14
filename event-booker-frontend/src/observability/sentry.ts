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
