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
