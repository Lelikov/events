import { afterEach, beforeEach, describe, expect, it } from 'vitest'
import { navigateTo, parseRoute } from './routing.ts'
import { cancelLeave, confirmLeave, isLeavePending, setNavBlocker } from './navGuard.ts'

describe('parseRoute', () => {
  it('parses known routes', () => {
    expect(parseRoute('/login')).toEqual({ name: 'login' })
    expect(parseRoute('/')).toEqual({ name: 'bookings' })
    expect(parseRoute('/schedule')).toEqual({ name: 'schedule' })
    expect(parseRoute('/bookings')).toEqual({ name: 'bookings' })
    expect(parseRoute('/profile')).toEqual({ name: 'profile' })
  })

  it('returns not-found for unknown paths', () => {
    expect(parseRoute('/nope')).toEqual({ name: 'not-found' })
    expect(parseRoute('/bookings/123')).toEqual({ name: 'not-found' })
  })
})

describe('navigateTo guard', () => {
  beforeEach(() => {
    window.history.replaceState(null, '', '/schedule')
  })
  afterEach(() => {
    setNavBlocker(null)
    if (isLeavePending()) cancelLeave()
  })

  it('defers navigation when blocked until confirmLeave', () => {
    setNavBlocker(() => true)
    navigateTo('/bookings')
    expect(window.location.pathname).toBe('/schedule')
    expect(isLeavePending()).toBe(true)
    confirmLeave()
    expect(window.location.pathname).toBe('/bookings')
  })

  it('does not navigate when the leave is cancelled', () => {
    setNavBlocker(() => true)
    navigateTo('/bookings')
    cancelLeave()
    expect(window.location.pathname).toBe('/schedule')
    expect(isLeavePending()).toBe(false)
  })

  it('navigates immediately for same-path navigation', () => {
    setNavBlocker(() => true)
    navigateTo('/schedule')
    expect(isLeavePending()).toBe(false)
  })

  it('navigates immediately when skipGuard is set', () => {
    setNavBlocker(() => true)
    navigateTo('/login', { replace: true, skipGuard: true })
    expect(isLeavePending()).toBe(false)
    expect(window.location.pathname).toBe('/login')
  })

  it('navigates immediately when nothing is blocked', () => {
    navigateTo('/bookings')
    expect(window.location.pathname).toBe('/bookings')
    expect(isLeavePending()).toBe(false)
  })
})
