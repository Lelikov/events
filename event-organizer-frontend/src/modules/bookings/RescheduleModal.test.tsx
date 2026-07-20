import { afterEach, describe, expect, it, vi } from 'vitest'
import { act } from 'react'
import { createRoot, type Root } from 'react-dom/client'
import { RescheduleModal } from './RescheduleModal.tsx'
import * as api from './bookingsApi.ts'

let container: HTMLDivElement
let root: Root

async function mount(onClose = vi.fn(), onDone = vi.fn()) {
  container = document.createElement('div')
  document.body.appendChild(container)
  root = createRoot(container)
  await act(async () =>
    root.render(
      <RescheduleModal
        bookingId="b1"
        currentStart="2026-10-01T09:00:00Z"
        organizerTz="Europe/Moscow"
        onClose={onClose}
        onRescheduled={onDone}
      />,
    ),
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
  return [...container.querySelectorAll('.modal-actions button')].find((b) => b.textContent === 'Перенести') as HTMLButtonElement
}

describe('RescheduleModal', () => {
  it('loads slots for the default date and confirms a pick', async () => {
    vi.spyOn(api, 'getBookingSlots').mockResolvedValue({
      date: '2026-10-01',
      time_zone: 'Europe/Moscow',
      slots: ['2026-10-01T09:00:00Z', '2026-10-01T10:00:00Z'],
    })
    const resch = vi.spyOn(api, 'rescheduleBooking').mockResolvedValue()
    const { onDone } = await mount()
    const chips = [...container.querySelectorAll('.slot-chip')] as HTMLButtonElement[]
    expect(chips.length).toBe(2)
    expect(confirmBtn().disabled).toBe(true)
    await act(async () => chips[1].click())
    expect(confirmBtn().disabled).toBe(false)
    await act(async () => confirmBtn().click())
    await act(async () => {})
    expect(resch).toHaveBeenCalledWith('b1', '2026-10-01T10:00:00Z')
    expect(onDone).toHaveBeenCalled()
  })

  it('shows the error and stays open when reschedule fails', async () => {
    vi.spyOn(api, 'getBookingSlots').mockResolvedValue({
      date: '2026-10-01',
      time_zone: 'Europe/Moscow',
      slots: ['2026-10-01T10:00:00Z'],
    })
    const { ApiError } = await import('../shared/api.ts')
    vi.spyOn(api, 'rescheduleBooking').mockRejectedValue(new ApiError('Слот занят', 409, null))
    const { onDone } = await mount()
    await act(async () => (container.querySelector('.slot-chip') as HTMLButtonElement).click())
    await act(async () => confirmBtn().click())
    await act(async () => {})
    expect(container.querySelector('.error-text')?.textContent).toContain('Слот занят')
    expect(onDone).not.toHaveBeenCalled()
    expect(container.querySelector('.modal-overlay')).not.toBeNull()
  })
})
