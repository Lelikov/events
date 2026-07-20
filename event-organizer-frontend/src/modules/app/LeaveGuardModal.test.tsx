import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { LeaveGuardModal } from './LeaveGuardModal.tsx'
import { cancelLeave, isLeavePending, requestLeave, setNavBlocker } from '../shared/navGuard.ts'

let container: HTMLDivElement
let root: Root

async function mount() {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () => root.render(<LeaveGuardModal />))
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  setNavBlocker(null)
  if (isLeavePending()) cancelLeave()
  vi.restoreAllMocks()
})

async function openWith(proceed: () => void) {
  setNavBlocker(() => true)
  await act(async () => requestLeave(proceed))
}

describe('LeaveGuardModal', () => {
  it('renders nothing while there is no pending leave', async () => {
    await mount()
    expect(container.querySelector('.modal-overlay')).toBeNull()
  })

  it('opens when a leave is requested while blocked', async () => {
    await mount()
    await openWith(vi.fn())
    expect(container.querySelector('.modal-overlay')).not.toBeNull()
    expect(container.textContent).toContain('Несохранённые изменения')
  })

  it('runs the pending action and closes on Уйти', async () => {
    await mount()
    const proceed = vi.fn()
    await openWith(proceed)
    const leave = [...container.querySelectorAll('.modal-actions button')].find((b) => b.textContent?.includes('Уйти'))!
    await act(async () => (leave as HTMLButtonElement).click())
    expect(proceed).toHaveBeenCalledTimes(1)
    expect(container.querySelector('.modal-overlay')).toBeNull()
  })

  it('cancels without running the action on Остаться', async () => {
    await mount()
    const proceed = vi.fn()
    await openWith(proceed)
    const stay = [...container.querySelectorAll('.modal-actions button')].find((b) => b.textContent === 'Остаться')!
    await act(async () => (stay as HTMLButtonElement).click())
    expect(proceed).not.toHaveBeenCalled()
    expect(container.querySelector('.modal-overlay')).toBeNull()
  })

  it('cancels on Escape', async () => {
    await mount()
    const proceed = vi.fn()
    await openWith(proceed)
    await act(async () => window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' })))
    expect(proceed).not.toHaveBeenCalled()
    expect(container.querySelector('.modal-overlay')).toBeNull()
  })
})
