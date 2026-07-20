import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { navigateTo, parseRoute } from './routing.ts'
import { setNavBlocker } from './navGuard.ts'

describe('parseRoute', () => {
  it('parses known routes', () => {
    expect(parseRoute('/login')).toEqual({ name: 'login' })
    expect(parseRoute('/')).toEqual({ name: 'schedule' })
    expect(parseRoute('/bookings')).toEqual({ name: 'bookings' })
    expect(parseRoute('/profile')).toEqual({ name: 'profile' })
  })

  it('returns not-found for unknown paths', () => {
    expect(parseRoute('/nope')).toEqual({ name: 'not-found' })
    expect(parseRoute('/bookings/123')).toEqual({ name: 'not-found' })
  })
})

describe('navigateTo guard', () => {
  const realConfirm = window.confirm
  beforeEach(() => {
    window.history.replaceState(null, '', '/schedule')
  })
  afterEach(() => {
    setNavBlocker(null)
    window.confirm = realConfirm
  })

  it('does not navigate when a dirty blocker is declined', () => {
    setNavBlocker(() => true)
    window.confirm = vi.fn().mockReturnValue(false)
    navigateTo('/bookings')
    expect(window.location.pathname).toBe('/schedule')
  })

  it('navigates when a dirty blocker is confirmed', () => {
    setNavBlocker(() => true)
    window.confirm = vi.fn().mockReturnValue(true)
    navigateTo('/bookings')
    expect(window.location.pathname).toBe('/bookings')
  })

  it('skips the guard for same-path navigation', () => {
    setNavBlocker(() => true)
    const confirm = vi.fn()
    window.confirm = confirm
    navigateTo('/schedule')
    expect(confirm).not.toHaveBeenCalled()
  })

  it('skips the guard when skipGuard is set', () => {
    setNavBlocker(() => true)
    const confirm = vi.fn()
    window.confirm = confirm
    navigateTo('/login', { replace: true, skipGuard: true })
    expect(confirm).not.toHaveBeenCalled()
    expect(window.location.pathname).toBe('/login')
  })
})
