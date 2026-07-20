import { afterEach, describe, expect, it, vi } from 'vitest'
import { confirmLeaveIfBlocked, setNavBlocker } from './navGuard.ts'

// happy-dom has no window.confirm, so tests install a vi.fn() explicitly.
const realConfirm = window.confirm
afterEach(() => {
  setNavBlocker(null)
  window.confirm = realConfirm
})

describe('navGuard', () => {
  it('allows navigation when no blocker is registered', () => {
    const confirm = vi.fn()
    window.confirm = confirm
    expect(confirmLeaveIfBlocked()).toBe(true)
    expect(confirm).not.toHaveBeenCalled()
  })

  it('allows navigation when the blocker reports clean', () => {
    setNavBlocker(() => false)
    const confirm = vi.fn()
    window.confirm = confirm
    expect(confirmLeaveIfBlocked()).toBe(true)
    expect(confirm).not.toHaveBeenCalled()
  })

  it('prompts and returns the confirm result when the blocker reports dirty', () => {
    setNavBlocker(() => true)
    window.confirm = vi.fn().mockReturnValue(false)
    expect(confirmLeaveIfBlocked()).toBe(false)
    window.confirm = vi.fn().mockReturnValue(true)
    expect(confirmLeaveIfBlocked()).toBe(true)
  })
})
