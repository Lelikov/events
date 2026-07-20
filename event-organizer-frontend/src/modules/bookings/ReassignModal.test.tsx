import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { ReassignModal } from './ReassignModal.tsx'
import * as api from './bookingsApi.ts'
import type { ReassignTarget } from './types.ts'

const targets: ReassignTarget[] = [
  { user_id: 'h2', name: 'Борис', email: 'boris@x.io' },
  { user_id: 'h3', name: null, email: 'no-name@x.io' },
]

let container: HTMLDivElement
let root: Root

async function mount(onClose = vi.fn(), onDone = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () =>
    root.render(<ReassignModal bookingId="b1" onClose={onClose} onReassigned={onDone} />),
  )
  await act(async () => {})
  return { onClose, onDone }
}
afterEach(() => {
  act(() => root.unmount())
  container.remove()
  vi.restoreAllMocks()
})

function confirmBtn(): HTMLButtonElement {
  return [...container.querySelectorAll('.modal-actions button')].find((b) => b.textContent === 'Переназначить') as HTMLButtonElement
}

describe('ReassignModal', () => {
  it('loads targets and confirms a pick', async () => {
    vi.spyOn(api, 'getReassignTargets').mockResolvedValue(targets)
    const doReassign = vi.spyOn(api, 'reassignBooking').mockResolvedValue()
    const { onDone } = await mount()
    const rows = [...container.querySelectorAll('.target-row')] as HTMLButtonElement[]
    expect(rows.length).toBe(2)
    expect(confirmBtn().disabled).toBe(true)
    await act(async () => rows[0].click())
    expect(confirmBtn().disabled).toBe(false)
    await act(async () => confirmBtn().click())
    await act(async () => {})
    expect(doReassign).toHaveBeenCalledWith('b1', 'h2')
    expect(onDone).toHaveBeenCalled()
  })

  it('shows an empty state when there are no other hosts', async () => {
    vi.spyOn(api, 'getReassignTargets').mockResolvedValue([])
    await mount()
    expect(container.textContent).toContain('Нет других хостов')
    expect(confirmBtn().disabled).toBe(true)
  })

  it('shows the error and stays open when reassign fails', async () => {
    vi.spyOn(api, 'getReassignTargets').mockResolvedValue(targets)
    const { ApiError } = await import('../shared/api.ts')
    vi.spyOn(api, 'reassignBooking').mockRejectedValue(new ApiError('Хост занят', 409, null))
    const { onDone } = await mount()
    await act(async () => (container.querySelector('.target-row') as HTMLButtonElement).click())
    await act(async () => confirmBtn().click())
    await act(async () => {})
    expect(container.querySelector('.error-text')?.textContent).toContain('Хост занят')
    expect(onDone).not.toHaveBeenCalled()
    expect(container.querySelector('.modal-overlay')).not.toBeNull()
  })
})
