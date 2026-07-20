import { afterEach, describe, expect, it, vi } from 'vitest'
import { cancelLeave, confirmLeave, isLeavePending, requestLeave, setNavBlocker, subscribeGuard } from './navGuard.ts'

afterEach(() => {
  setNavBlocker(null)
  if (isLeavePending()) cancelLeave()
})

describe('navGuard', () => {
  it('runs the action immediately when no blocker is registered', () => {
    const proceed = vi.fn()
    requestLeave(proceed)
    expect(proceed).toHaveBeenCalledTimes(1)
    expect(isLeavePending()).toBe(false)
  })

  it('runs the action immediately when the blocker reports clean', () => {
    setNavBlocker(() => false)
    const proceed = vi.fn()
    requestLeave(proceed)
    expect(proceed).toHaveBeenCalledTimes(1)
    expect(isLeavePending()).toBe(false)
  })

  it('defers the action and marks pending when blocked', () => {
    setNavBlocker(() => true)
    const proceed = vi.fn()
    requestLeave(proceed)
    expect(proceed).not.toHaveBeenCalled()
    expect(isLeavePending()).toBe(true)
  })

  it('confirmLeave runs the pending action and clears pending', () => {
    setNavBlocker(() => true)
    const proceed = vi.fn()
    requestLeave(proceed)
    confirmLeave()
    expect(proceed).toHaveBeenCalledTimes(1)
    expect(isLeavePending()).toBe(false)
  })

  it('cancelLeave clears pending without running the action', () => {
    setNavBlocker(() => true)
    const proceed = vi.fn()
    requestLeave(proceed)
    cancelLeave()
    expect(proceed).not.toHaveBeenCalled()
    expect(isLeavePending()).toBe(false)
  })

  it('notifies subscribers when the pending state changes', () => {
    const cb = vi.fn()
    const unsub = subscribeGuard(cb)
    setNavBlocker(() => true)
    requestLeave(() => {})
    expect(cb).toHaveBeenCalled()
    unsub()
    cancelLeave()
    const callsAfterUnsub = cb.mock.calls.length
    setNavBlocker(() => true)
    requestLeave(() => {})
    expect(cb.mock.calls.length).toBe(callsAfterUnsub)
  })
})
